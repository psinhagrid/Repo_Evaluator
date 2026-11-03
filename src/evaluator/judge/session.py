"""One criterion judged as a resumable state machine.

    session = CriterionSession(ctx, criterion, ...)
    while (params := session.next_request()) is not None:
        session.consume(await llm.create(params))     # or a batch result
    result = session.result

The same object drives the live flow (one request at a time) and the batch
flow (one request per session per batch round). History is append-only:
assistant content goes back unchanged, so thinking blocks stay valid.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from ..intake.snapshot import Snapshot
from ..llm import UsageLog, text_of
from ..scoring.config import Criterion, RubricConfig
from .prompts import JUDGE_PROCEDURE, PROMPT_VERSION, criterion_context, output_schema, shared_context
from .tools import ToolBox, tool_definitions
from .verify import Verifier, format_problems

MAX_TOKENS = 16_000
MAX_ATTEMPTS = 3          # fresh conversations (invalid output, refusal, API failure)
MAX_CORRECTIONS = 1       # in-conversation re-checks after failed verification
EXTRA_TURNS = 6           # turns allowed beyond the tool budget before giving up


@dataclass
class JudgeContext:
    """Everything shared by every criterion of one submission."""
    config: RubricConfig
    snap: Snapshot
    facts: dict
    repo_map: dict
    cache_ttl: str = "5m"  # "1h" for batch runs, where requests can sit for a long time

    def __post_init__(self):
        self.shared_text = shared_context(self.facts, self.repo_map, self.config.assignment_text)
        self.schema = output_schema(self.config)
        self.tools = tool_definitions()

    def system_blocks(self) -> list[dict]:
        cc = {"type": "ephemeral"} if self.cache_ttl == "5m" else {"type": "ephemeral", "ttl": self.cache_ttl}
        return [
            {"type": "text", "text": JUDGE_PROCEDURE, "cache_control": cc},
            {"type": "text", "text": self.shared_text, "cache_control": cc},
        ]


@dataclass
class CriterionResult:
    criterion: str
    model: str
    status: str  # "ok" | "not_graded"
    run_index: int = 0
    prompt_version: str = PROMPT_VERSION
    assessment: dict | None = None        # verified and pruned
    raw_assessment: dict | None = None    # last thing the judge returned
    reason: str | None = None
    attempts: int = 0
    corrections: int = 0
    citations_checked: int = 0
    citations_failed_first_pass: int = 0
    citations_failed_final: int = 0
    verification_notes: list[str] = field(default_factory=list)
    problems_first_pass: list[str] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)

    @property
    def rating(self) -> float | None:
        return None if self.assessment is None else float(self.assessment["rating"])

    def findings(self) -> dict[str, bool | None]:
        if not self.assessment:
            return {}
        return {f["id"]: f["value"] for f in self.assessment.get("findings", [])}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CriterionResult":
        return cls(**d)


def parse_json_reply(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


REQUIRED_KEYS = ("elements", "rating", "rationale", "what_worked", "what_to_improve", "defects", "unverified",
                 "findings", "grader_directed_text")


class CriterionSession:
    def __init__(self, ctx: JudgeContext, criterion: Criterion, doc_claims: list[dict] | None = None,
                 phrase_matches: list[dict] | None = None, run_index: int = 0):
        self.ctx, self.c = ctx, criterion
        self.doc_claims = doc_claims or []
        self.phrase_matches = phrase_matches or []
        self.usage = UsageLog()  # this session's calls only; callers aggregate
        self.result = CriterionResult(criterion=criterion.id, model=criterion.model, status="pending", run_index=run_index)
        self.done = False
        self._pending = False
        self._start_attempt()

    # -- lifecycle ----------------------------------------------------------------
    def _start_attempt(self) -> None:
        self.result.attempts += 1
        self.toolbox = ToolBox(self.ctx.snap, self.ctx.repo_map, self.c)
        self.verifier = Verifier(self.ctx.snap, self.ctx.facts, self.toolbox, self.c, self.ctx.config)
        visible = self.toolbox.visible_files()
        prompt = criterion_context(self.c, self.ctx.config, visible, self.doc_claims if self.c.doc_claims else [],
                                   self.phrase_matches)
        self.messages: list[dict] = [{"role": "user", "content": prompt}]
        self.turns = 0
        self.corrections = 0
        self.first_pass_recorded = False
        self.context_exhausted = False

    def _fail_attempt(self, reason: str, retryable: bool = True) -> None:
        self.result.reason = reason
        if retryable and self.result.attempts < MAX_ATTEMPTS:
            self._start_attempt()
        else:
            self._finish_not_graded(reason)

    def _finish_not_graded(self, reason: str) -> None:
        self.result.status = "not_graded"
        self.result.reason = reason
        self.result.tool_log = self.toolbox.log_dicts()
        self.result.usage = self.usage.to_list()
        self.done = True

    # -- driving ----------------------------------------------------------------------
    def next_request(self) -> dict | None:
        if self.done:
            return None
        self._pending = True
        return {
            "model": self.c.model,
            "max_tokens": MAX_TOKENS,
            "system": self.ctx.system_blocks(),
            "tools": self.ctx.tools,
            "messages": list(self.messages),
            "output_config": {"effort": self.c.effort, "format": {"type": "json_schema", "schema": self.ctx.schema}},
            "cache_control": {"type": "ephemeral"},  # automatic breakpoint on the growing tool-loop tail
        }

    def api_error(self, error: str, status_code: int | None = None) -> None:
        """The request failed at the API level (after SDK retries)."""
        self._pending = False
        # A 400 will fail the same way again; anything else may be transient.
        self._fail_attempt(f"API error: {error}", retryable=status_code != 400)

    def consume(self, message: dict, batch: bool = False) -> None:
        if self.done:
            raise RuntimeError("session already finished")
        self._pending = False
        self.usage.add(f"judge:{self.c.id}", message, batch=batch)
        self.turns += 1
        stop = message.get("stop_reason")
        content = message.get("content") or []

        if stop == "refusal":
            return self._fail_attempt("model refused the request")
        if stop == "max_tokens":
            return self._fail_attempt("response hit max_tokens")
        if self.turns > self.c.tool_budget + EXTRA_TURNS:
            return self._fail_attempt("too many turns without a final assessment")

        if stop == "tool_use":
            self.messages.append({"role": "assistant", "content": content})
            u = message.get("usage") or {}
            prompt_tokens = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if self.c.max_context_tokens and prompt_tokens >= self.c.max_context_tokens:
                self.context_exhausted = True
            results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                if self.context_exhausted:
                    text, is_error = ("Context limit for this criterion reached. Do not call more tools; return "
                                      "your final assessment JSON now.", True)
                else:
                    text, is_error = self.toolbox.run(block["id"], block["name"], block.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": text, "is_error": is_error})
            if not results:
                return self._fail_attempt("tool_use stop without tool calls")
            self.messages.append({"role": "user", "content": results})
            return

        # end_turn (or stop_sequence): this should be the final JSON.
        reply = parse_json_reply(text_of(message))
        if reply is None or any(k not in reply for k in REQUIRED_KEYS):
            if self.corrections < MAX_CORRECTIONS:
                self.corrections += 1
                self.result.corrections += 1
                self.messages.append({"role": "assistant", "content": content})
                self.messages.append({"role": "user", "content": (
                    "Your reply was not a complete assessment JSON. Return only the JSON object with every "
                    "required field.")})
                return
            return self._fail_attempt("invalid output: no complete assessment JSON")

        self.result.raw_assessment = reply
        check = self.verifier.verify(reply)
        if not self.first_pass_recorded:
            self.first_pass_recorded = True
            self.result.citations_failed_first_pass = check.failed
            self.result.problems_first_pass = [f"{p.where}: {p.message}" for p in check.problems]
        if not check.ok and self.corrections < MAX_CORRECTIONS:
            self.corrections += 1
            self.result.corrections += 1
            self.messages.append({"role": "assistant", "content": content})
            self.messages.append({"role": "user", "content": (
                "Code could not verify parts of your assessment:\n" + format_problems(check.problems)
                + "\n\nFix each problem: re-open the files to get exact line numbers and verbatim quotes, run the "
                "searches that prove absence, or remove claims you cannot support. Then return the complete "
                "corrected assessment JSON.")})
            return

        pruned, notes = self.verifier.prune(reply)
        final_check = self.verifier.verify(pruned)
        self.result.citations_checked = check.checked
        self.result.citations_failed_final = check.failed
        self.result.verification_notes = notes + ([f"remaining issue: {p.where}: {p.message}" for p in final_check.problems]
                                                  if final_check.problems else [])
        self.result.assessment = pruned
        self.result.status = "ok"
        self.result.reason = None
        self.result.tool_log = self.toolbox.log_dicts()
        self.result.usage = self.usage.to_list()
        self.done = True

"""One-shot structured-output call as a session (same driver interface as CriterionSession).

Used for the Haiku classifier, claim extraction and the summary call. The
reply must parse as JSON matching the schema and pass `validate`; otherwise
the call is retried, then marked failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..llm import UsageLog, text_of
from .session import parse_json_reply

MAX_ATTEMPTS = 3


@dataclass
class StructuredResult:
    purpose: str
    model: str
    status: str = "pending"  # "ok" | "failed"
    output: dict | None = None
    reason: str | None = None
    attempts: int = 0
    usage: list[dict] = field(default_factory=list)


class StructuredCall:
    def __init__(self, purpose: str, model: str, system: str, user: str, schema: dict,
                 validate: Callable[[dict], str | None] | None = None, effort: str = "low", max_tokens: int = 8000):
        self.purpose, self.model = purpose, model
        self.system, self.user, self.schema = system, user, schema
        self.validate = validate or (lambda _: None)
        self.effort, self.max_tokens = effort, max_tokens
        self.usage = UsageLog()
        self.result = StructuredResult(purpose=purpose, model=model)
        self.done = False

    def next_request(self) -> dict | None:
        if self.done:
            return None
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": [{"role": "user", "content": self.user}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": self.schema}},
        }

    def _fail(self, reason: str, retryable: bool = True) -> None:
        self.result.attempts += 1
        self.result.reason = reason
        if not retryable or self.result.attempts >= MAX_ATTEMPTS:
            self.result.status = "failed"
            self.result.usage = self.usage.to_list()
            self.done = True

    def api_error(self, error: str, status_code: int | None = None) -> None:
        self._fail(f"API error: {error}", retryable=status_code != 400)

    def consume(self, message: dict, batch: bool = False) -> None:
        self.usage.add(self.purpose, message, batch=batch)
        if message.get("stop_reason") in ("refusal", "max_tokens"):
            return self._fail(f"stop_reason {message.get('stop_reason')}")
        out = parse_json_reply(text_of(message))
        if out is None:
            return self._fail("invalid JSON")
        why = self.validate(out)
        if why:
            return self._fail(f"invalid output: {why}")
        self.result.attempts += 1
        self.result.status = "ok"
        self.result.output = out
        self.result.reason = None
        self.result.usage = self.usage.to_list()
        self.done = True

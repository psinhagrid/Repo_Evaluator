"""Scripted stand-ins for the Claude API, so the LLM stages run offline and deterministically."""

from __future__ import annotations

import itertools
import json

_ids = itertools.count(1)


def usage(inp=1000, out=200, read=0, write=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write}


def tool_use(*calls, model="claude-sonnet-5-5"):
    """calls: (name, input) pairs."""
    return {"model": model, "role": "assistant", "stop_reason": "tool_use", "usage": usage(),
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}]
            + [{"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": n, "input": i} for n, i in calls]}


def final(obj, model="claude-sonnet-5-5", stop="end_turn"):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return {"model": model, "role": "assistant", "stop_reason": stop, "usage": usage(out=800),
            "content": [{"type": "text", "text": text}]}


def refusal(model="claude-sonnet-5-5"):
    return {"model": model, "role": "assistant", "stop_reason": "refusal", "usage": usage(out=0), "content": []}


def ev(file, start, end, quote):
    return {"file": file, "start_line": start, "end_line": end, "quote": quote}


def assessment(elements, rating, findings=(), **kw):
    base = {
        "elements": elements, "rating": rating, "rationale": "Because of the cited evidence.",
        "what_worked": [], "what_to_improve": [], "defects": [], "unverified": [],
        "findings": list(findings), "grader_directed_text": [],
    }
    base.update(kw)
    return base


def element(eid, status, evidence=(), search_ids=(), note=""):
    return {"id": eid, "status": status, "evidence": list(evidence), "search_ids": list(search_ids), "note": note}


class ScriptedLLM:
    """Replies come from a function of the request params (or a fixed queue per key)."""

    def __init__(self, responder):
        self.responder = responder
        self.requests: list[dict] = []

    async def create(self, params: dict) -> dict:
        self.requests.append(json.loads(json.dumps(params, default=str)))
        reply = self.responder(params)
        if isinstance(reply, Exception):
            raise reply
        return reply


def criterion_of(params: dict) -> str | None:
    """Which criterion a judge request is for (from the layer-3 prompt)."""
    first = params["messages"][0]["content"]
    if isinstance(first, str) and '<criterion id="' in first:
        return first.split('<criterion id="', 1)[1].split('"', 1)[0]
    return None


def turn_index(params: dict) -> int:
    """Number of assistant turns already in the conversation."""
    return sum(1 for m in params["messages"] if m["role"] == "assistant")


class APIError400(Exception):
    status_code = 400


class FakeGrader:
    """Scripted model for whole-submission grading.

    ratings: criterion -> rating or list of ratings (one per successive run).
    findings: finding id -> bool. Every element is reported missing, proven by a
    zero-hit search, so the scripted judge always passes verification.
    """

    def __init__(self, config, ratings, findings=None, injection_label="grader_manipulation",
                 refuse=(), summary_ids=None):
        self.config = config
        self.ratings = {k: list(v) if isinstance(v, list) else [v] for k, v in ratings.items()}
        self.findings = findings or {}
        self.injection_label = injection_label
        self.refuse = set(refuse)
        self.summary_ids = summary_ids
        self.finals: dict[str, int] = {}
        self.calls: dict[str, int] = {}

    def __call__(self, params):
        system = params["system"]
        if isinstance(system, str):
            return self._structured(system, params)
        cid = criterion_of(params)
        self.calls[cid] = self.calls.get(cid, 0) + 1
        if cid in self.refuse:
            return refusal()
        if turn_index(params) == 0:
            return tool_use(("search", {"pattern": "zz_no_such_text_zz", "regex": False, "path_glob": None}))
        n = self.finals.get(cid, 0)
        self.finals[cid] = n + 1
        seq = self.ratings[cid]
        rating = seq[min(n, len(seq) - 1)]
        c = self.config.criterion(cid)
        if rating == 4:  # a 4 needs every element found with real evidence
            els = [element(e.id, "found", [ev("WRITEUP.md", 1, 1, "# Write-up")]) for e in c.elements]
        else:
            els = [element(e.id, "missing", search_ids=["t1"]) for e in c.elements]
        return final(assessment(
            els,
            rating,
            findings=[{"id": f, "value": self.findings.get(f, False), "evidence": [], "search_ids": ["t1"]}
                      for f in c.findings],
            defects=[{"text": f"{cid} defect", "severity": "high", "evidence": [], "search_ids": ["t1"]}],
            unverified=[f"{cid}: runtime behaviour"],
        ))

    def _structured(self, system, params):
        user = params["messages"][0]["content"]
        self.calls[system[:20]] = self.calls.get(system[:20], 0) + 1
        if system.startswith("You review regex matches"):
            n = user.count("<match index=")
            return final({"items": [{"index": i, "label": self.injection_label, "reason": "addressed to grader"}
                                    for i in range(n)]}, model="claude-haiku-5-5")
        if system.startswith("You extract factual claims"):
            return final({"claims": [{"file": "README.md", "line": 3, "claim": "run_all.sh reproduces all results"}]},
                         model="claude-haiku-5-5")
        if system.startswith("You write the closing section"):
            payload = json.loads(user)
            ids = self.summary_ids
            if ids is None:
                ids = [d["id"] for c in payload["criteria"] for d in c["defects"]][:3]
            return final({"top_defect_ids": ids, "verdict": "Solid start; evidence gaps limit the grade.",
                          "next_steps": ["Commit labels before running graders."]})
        raise AssertionError(f"unexpected structured call: {system[:60]}")

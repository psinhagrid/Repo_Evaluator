"""Summary call: reads only collected section results, never the repo.

It selects the most consequential defects by id from the list it is given,
so it cannot introduce new findings; code rejects unknown ids.
"""

from __future__ import annotations

import json

from ..scoring.config import RubricConfig
from .structured import StructuredCall

SUMMARY_SYSTEM = """\
You write the closing section of a grading report from results that were already verified. You \
receive each criterion's rating, rationale and defects (each defect has an id), plus the score. Choose \
up to five defects that most affected the grade or would most improve the work if fixed, by id, most \
consequential first. Then write a verdict (2-3 sentences, plain and specific) and 3-5 concrete next \
steps. Use only the information given; do not add new findings or speculate about the code."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "top_defect_ids": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["top_defect_ids", "verdict", "next_steps"],
    "additionalProperties": False,
}


def defect_index(sections: list[dict]) -> dict[str, dict]:
    """Stable ids for every verified defect: <criterion>#<n>."""
    idx = {}
    for s in sections:
        for k, d in enumerate((s.get("assessment") or {}).get("defects", []), start=1):
            idx[f"{s['criterion']}#{k}"] = {**d, "criterion": s["criterion"]}
    return idx


def summary_call(config: RubricConfig, sections: list[dict], score: dict) -> StructuredCall:
    idx = defect_index(sections)
    payload = {
        "assignment": config.title,
        "score": {"total": score["total"], "final": score["final"], "band": score["band"],
                  "caps_applied": [c["cap_id"] for c in score["caps"] if c["status"] == "applied"]},
        "criteria": [{
            "id": s["criterion"], "name": s["name"], "weight": s["weight"], "rating": s["rating"],
            "rationale": (s.get("assessment") or {}).get("rationale", ""),
            "defects": [{"id": i, "severity": d["severity"], "text": d["text"]}
                        for i, d in idx.items() if d["criterion"] == s["criterion"]],
        } for s in sections],
    }

    def validate(out):
        unknown = [i for i in out["top_defect_ids"] if i not in idx]
        if unknown:
            return f"unknown defect ids {unknown}"
        if len(out["top_defect_ids"]) > 5 or len(set(out["top_defect_ids"])) != len(out["top_defect_ids"]):
            return "at most five distinct defect ids"
        if not out["verdict"].strip():
            return "empty verdict"
        return None

    return StructuredCall("summary", config.models["summary"], SUMMARY_SYSTEM,
                          json.dumps(payload, indent=1, sort_keys=True), SUMMARY_SCHEMA, validate,
                          effort="low", max_tokens=8000)


def fallback_summary(sections: list[dict]) -> dict:
    """Deterministic stand-in if the summary call fails: highest-severity defects by weight."""
    idx = defect_index(sections)
    weight = {s["criterion"]: s["weight"] for s in sections}
    order = {"high": 0, "medium": 1, "low": 2}
    top = sorted(idx, key=lambda i: (order[idx[i]["severity"]], -weight[idx[i]["criterion"]], i))[:5]
    return {"top_defect_ids": top, "verdict": "", "next_steps": [], "fallback": True}

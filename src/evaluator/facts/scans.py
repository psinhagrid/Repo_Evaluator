"""Line-level regex scans over the snapshot.

Each scan yields findings with file and line so the judge and the report
can cite them directly. Secret matches are redacted in the output.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from ..intake.snapshot import Snapshot

MAX_FINDINGS_PER_SCAN = 200


@dataclass
class Finding:
    scan: str
    kind: str
    file: str
    line: int
    excerpt: str


def _excerpt(line: str, m: re.Match, width: int = 60) -> str:
    start = max(0, m.start() - width)
    end = min(len(line), m.end() + width)
    return line[start:end].strip()[:200]


# -- placeholders and leftovers ------------------------------------------------

PLACEHOLDER_PATTERNS = [
    ("angle_placeholder", re.compile(r"<\s*(?:your|insert|enter|add|replace)[\w\s\-]*>", re.I)),
    ("bracket_placeholder", re.compile(r"\[\s*(?:insert|your|add|todo|tbd|placeholder)[^\]]{0,60}\]", re.I)),
    ("todo_marker", re.compile(r"\b(?:TODO|FIXME|TBD|XXX)\b")),
    ("lorem_ipsum", re.compile(r"lorem ipsum", re.I)),
    ("template_var", re.compile(r"\{\{\s*[A-Z_][A-Z0-9_]*\s*\}\}")),
    ("example_value", re.compile(r"\b(?:YOUR_[A-Z_]+|example\.com/your|your-repo-url|your-username)\b", re.I)),
]

LEFTOVER_AGENT_PATTERNS = [
    ("assistant_preamble", re.compile(r"\b(?:Certainly|Sure|Absolutely)[!,] (?:here(?:'s| is)|I(?:'ll| will| can))", re.I)),
    ("assistant_offer", re.compile(r"\b(?:let me know if you(?:'d| would)? (?:like|want|need)|I hope this helps|feel free to ask)", re.I)),
    ("assistant_self_reference", re.compile(r"\bas an AI(?: language model)?\b", re.I)),
    ("here_is_updated", re.compile(r"\bhere(?:'s| is) (?:the|an?|your) (?:updated|revised|improved|complete|final) (?:version|code|file|write-?up|draft)", re.I)),
    ("agent_turn_marker", re.compile(r"^\s*(?:Human|Assistant|User|AI):\s", re.I)),
    ("would_you_like", re.compile(r"\bwould you like me to\b", re.I)),
]

# -- prompt-injection pre-check (regex half; a classifier labels matches later) --

INJECTION_PATTERNS = [
    ("ignore_instructions", re.compile(r"\b(?:ignore|disregard|forget)\b.{0,30}\b(?:previous|prior|above|all|earlier|your)\b.{0,20}\b(?:instructions?|prompts?|rules?|rubric)", re.I)),
    ("addressed_to_grader", re.compile(r"\b(?:dear|note to(?: the)?|attention|hey|hi|hello)\s+(?:AI\s+)?(?:grader|evaluator|reviewer|marker|assessor|judge|LLM|model|assistant|claude)\b", re.I)),
    ("score_request", re.compile(r"\b(?:give|award|assign|rate|score|grade|mark)\b.{0,25}\b(?:full|maximum|max|perfect|top|highest|100|4\s*/\s*4|10\s*/\s*10|A\+)\b.{0,15}\b(?:marks?|points?|score|grade|rating)?", re.I)),
    ("role_override", re.compile(r"\byou are (?:now )?(?:an?|the) (?:AI |LLM )?(?:grader|evaluator|reviewer|assistant|judge)\b", re.I)),
    ("system_prompt", re.compile(r"\b(?:system prompt|system message|</?system>|\[/?INST\]|<\|im_start\|>)", re.I)),
    ("grader_conditional", re.compile(r"\bif you are an? (?:AI|LLM|language model|grader|evaluator|automated)", re.I)),
]
HIDDEN_CHARS = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩\U000e0000-\U000e007f]")


def _scan(snap: Snapshot, scan: str, patterns, paths=None) -> list[Finding]:
    """First matching pattern per line, so one line yields at most one finding."""
    out: list[Finding] = []
    for path in sorted(paths if paths is not None else snap.files):
        for i, line in enumerate(snap.lines(path), start=1):
            for kind, pat in patterns:
                m = pat.search(line)
                if m:
                    out.append(Finding(scan, kind, path, i, _excerpt(line, m)))
                    if len(out) >= MAX_FINDINGS_PER_SCAN:
                        return out
                    break
    return out


def scan_placeholders(snap: Snapshot) -> dict:
    # TODO markers in code are normal engineering; only count them in prose files.
    prose = [p for p in snap.files if p.lower().endswith((".md", ".markdown", ".rst", ".txt"))]
    placeholders = _scan(snap, "placeholders", PLACEHOLDER_PATTERNS)
    placeholders = [f for f in placeholders if f.kind != "todo_marker" or f.file in prose]
    leftovers = _scan(snap, "leftover_agent_text", LEFTOVER_AGENT_PATTERNS, paths=prose)
    return {"placeholders": [asdict(f) for f in placeholders], "leftover_agent_text": [asdict(f) for f in leftovers]}


def scan_secrets(snap: Snapshot) -> list[dict]:
    """Secrets found (and masked) while the snapshot was written."""
    return list(snap.secret_findings)


def scan_injection(snap: Snapshot, extra_texts: dict[str, str] | None = None) -> list[dict]:
    """Regex sweep over every snapshot file plus extra texts (e.g. commit messages).

    Matches are candidates only; each is classified later as a legitimate
    project prompt, assignment text, or an attempt to manipulate the grader.
    """
    found = [asdict(f) for f in _scan(snap, "injection", INJECTION_PATTERNS)]
    for path in sorted(snap.files):
        for i, line in enumerate(snap.lines(path), start=1):
            m = HIDDEN_CHARS.search(line)
            if m:
                found.append(asdict(Finding("injection", "hidden_unicode", path, i,
                                            f"U+{ord(m.group(0)):04X} in: " + HIDDEN_CHARS.sub("�", line).strip()[:160])))
    for label, text in (extra_texts or {}).items():
        for i, line in enumerate(text.splitlines(), start=1):
            for kind, pat in INJECTION_PATTERNS:
                m = pat.search(line)
                if m:
                    found.append(asdict(Finding("injection", kind, label, i, _excerpt(line, m))))
    return found[:MAX_FINDINGS_PER_SCAN]


# -- hardcoded model names -------------------------------------------------------

MODEL_NAME_RE = re.compile(
    r"""['"]((?:claude|gpt|o\d|gemini|llama|mistral|mixtral|command|deepseek|qwen)[\w.\-:/]*)['"]""", re.I)


def scan_model_names(snap: Snapshot, code_paths: list[str]) -> list[dict]:
    out = []
    for path in sorted(code_paths):
        for i, line in enumerate(snap.lines(path), start=1):
            for m in MODEL_NAME_RE.finditer(line):
                name = m.group(1)
                if len(name) < 4 or name.lower() in ("command", "o1", "o3"):
                    continue
                out.append({"file": path, "line": i, "model": name})
    return out[:MAX_FINDINGS_PER_SCAN]

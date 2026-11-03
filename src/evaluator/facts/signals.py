"""Evaluate config-defined yes/no signals (used by caps) against the snapshot."""

from __future__ import annotations

import re
from fnmatch import fnmatch

from ..intake.snapshot import Snapshot
from ..scoring.config import Signal

MAX_EVIDENCE = 5


def _glob(path: str, patterns: list[str]) -> bool:
    low = path.lower()
    return any(fnmatch(low, p.lower()) or fnmatch(low.rsplit("/", 1)[-1], p.lower()) for p in patterns)


def _lookup(facts: dict, dotted: str):
    node = facts
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"fact sheet has no '{dotted}'")
        node = node[part]
    return node


def evaluate_signal(sig: Signal, snap: Snapshot, all_paths: list[str], markdown: list[dict], facts: dict) -> dict:
    evidence = []
    if sig.fact:
        value = _lookup(facts, sig.fact)
        if value:
            evidence.append({"fact": sig.fact, "why": f"{len(value) if isinstance(value, list) else value} in fact sheet"})
    for p in sorted(all_paths):
        if sig.any_file and _glob(p, sig.any_file):
            evidence.append({"file": p, "why": "file name matches"})
    heading_res = [re.compile(h, re.I) for h in sig.any_heading]
    for md in markdown:
        if sig.in_files and not _glob(md["file"], sig.in_files):
            continue
        for h in md["headings"]:
            if any(r.search(h["text"]) for r in heading_res):
                evidence.append({"file": md["file"], "line": h["line"], "why": f"heading: {h['text']}"})
    text_res = [re.compile(t, re.I) for t in sig.any_text]
    if text_res:
        for p in sorted(snap.files):
            if sig.in_files and not _glob(p, sig.in_files):
                continue
            for i, line in enumerate(snap.lines(p), start=1):
                if any(r.search(line) for r in text_res):
                    evidence.append({"file": p, "line": i, "why": "text matches"})
                    break
            if len(evidence) >= MAX_EVIDENCE:
                break
    return {"present": bool(evidence), "description": sig.description, "evidence": evidence[:MAX_EVIDENCE]}


def evaluate_signals(signals: dict[str, Signal], snap: Snapshot, all_paths: list[str], markdown: list[dict],
                     facts: dict) -> dict:
    return {sid: evaluate_signal(s, snap, all_paths, markdown, facts) for sid, s in signals.items()}

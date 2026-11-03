"""Verify a judge's assessment against the snapshot, the fact sheet and the tool log.

Nothing the judge says is trusted: each citation must exist, sit inside the
criterion's scope, contain its quote, and be of an evidence type the element
allows. "Missing" verdicts must point at zero-hit searches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..intake.snapshot import Snapshot
from ..scoring.config import Criterion, RubricConfig
from .tools import ToolBox

CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".pyi"}
DEPENDENCY_NAMES = re.compile(r"^(requirements[\w\-.]*\.(txt|in)|pyproject\.toml|package\.json|uv\.lock|poetry\.lock|"
                              r"Pipfile(\.lock)?|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.(toml|lock)|go\.(mod|sum))$")
MAX_QUOTE_CHECK = 300


def evidence_type(path: str, converted_from: str | None = None) -> str:
    if path == "fact_sheet":
        return "fact"
    p = PurePosixPath(path)
    name, ext = p.name, p.suffix.lower()
    if converted_from == "ipynb" or ext == ".ipynb":
        return "notebook"
    if DEPENDENCY_NAMES.match(name):
        return "dependencies"
    if ext in CODE_EXT:
        return "code"
    if ext in (".sh", ".bash") or name in ("Makefile", "justfile", "Dockerfile"):
        return "shell"
    if ext in (".md", ".markdown", ".rst", ".txt"):
        return "md"
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in (".json", ".jsonl"):
        return "json"
    if ext in (".yaml", ".yml", ".toml", ".cfg", ".ini", ".env.example"):
        return "config"
    if ext in (".log", ".out"):
        return "log"
    return "other"


def _fact_lookup(facts: dict, dotted: str) -> bool:
    node = facts
    for part in dotted.strip().split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return True


@dataclass
class Problem:
    where: str
    message: str


@dataclass
class Verification:
    problems: list[Problem] = field(default_factory=list)
    checked: int = 0
    failed: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


class Verifier:
    def __init__(self, snap: Snapshot, facts: dict, toolbox: ToolBox, criterion: Criterion, config: RubricConfig):
        self.snap, self.facts, self.toolbox, self.c, self.config = snap, facts, toolbox, criterion, config
        self._elements = {e.id: e for e in criterion.elements}

    # -- single citations ----------------------------------------------------
    def check_evidence(self, ev: dict, allowed_types: tuple[str, ...] = ()) -> str | None:
        """Return None if the citation verifies, else the reason it fails."""
        f = ev.get("file", "")
        if f == "fact_sheet" or f.startswith("fact_sheet:"):
            path = ev.get("quote", "") if f == "fact_sheet" else f.split(":", 1)[1]
            if not _fact_lookup(self.facts, path):
                return f"fact sheet has no path {path!r}"
            etype = "git" if path.startswith("git.") else "fact"
            if allowed_types and etype not in allowed_types and "fact" not in allowed_types:
                return f"fact-sheet evidence ({etype}) not allowed for this element (allowed: {', '.join(allowed_types)})"
            return None
        if f not in self.snap.files:
            return f"file {f!r} not in snapshot"
        if not self.toolbox.allowed(f):
            return f"{f} is outside this criterion's evidence scope"
        start, end = ev.get("start_line", 0), ev.get("end_line", 0)
        ok, why = self.snap.verify_citation(f, start, end or start, (ev.get("quote") or "")[:MAX_QUOTE_CHECK] or None)
        if not ok:
            return f"{f}:{start}-{end}: {why}"
        if allowed_types:
            etype = evidence_type(f, self.snap.files[f].converted_from)
            # Notebook cells count as code wherever code is accepted.
            accepted = etype in allowed_types or (etype == "notebook" and "code" in allowed_types)
            if not accepted:
                return f"{f} is {etype} evidence; this element accepts {', '.join(allowed_types)}"
        return None

    def check_search_ids(self, ids: list[str]) -> str | None:
        """At least one id must be a successful search/find_references with zero hits."""
        logged = {c.id: c for c in self.toolbox.log}
        unknown = [i for i in ids if i not in logged]
        if unknown:
            return f"unknown search ids {unknown}"
        zero = [i for i in ids if logged[i].ok and logged[i].tool in ("search", "find_references") and logged[i].hits == 0]
        if not zero:
            return "no cited search returned zero matches"
        return None

    # -- whole assessment ------------------------------------------------------
    def verify(self, a: dict) -> Verification:
        v = Verification()

        def bad(where, msg):
            v.problems.append(Problem(where, msg))

        ids = [e.get("id") for e in a.get("elements", [])]
        for missing in sorted(set(self._elements) - set(ids)):
            bad(f"elements.{missing}", "element not assessed")
        for extra in sorted(set(ids) - set(self._elements)):
            bad(f"elements.{extra}", "unknown element id")
        if len(ids) != len(set(ids)):
            bad("elements", "duplicate element ids")

        for e in a.get("elements", []):
            el = self._elements.get(e.get("id"))
            if el is None:
                continue
            where = f"elements.{el.id}"
            if e["status"] in ("found", "partial"):
                if not e.get("evidence"):
                    bad(where, f"status {e['status']} needs at least one citation")
                for k, ev in enumerate(e.get("evidence", [])):
                    v.checked += 1
                    why = self.check_evidence(ev, el.evidence)
                    if why:
                        v.failed += 1
                        bad(f"{where}.evidence[{k}]", why)
            else:
                why = self.check_search_ids(e.get("search_ids", []))
                if why:
                    bad(where, f"status missing needs a zero-hit search: {why}")

        rating = a.get("rating")
        if not isinstance(rating, (int, float)) or not self.config.scale.valid(float(rating)):
            bad("rating", f"rating {rating!r} not on the scale")
        elif rating >= self.config.scale.max and any(e.get("status") != "found" for e in a.get("elements", [])):
            bad("rating", "a 4 requires every element to be found")

        for section in ("what_worked", "what_to_improve", "defects"):
            for k, item in enumerate(a.get(section, [])):
                where = f"{section}[{k}]"
                if not item.get("evidence") and not item.get("search_ids"):
                    bad(where, "feedback item has no citation")
                for j, ev in enumerate(item.get("evidence", [])):
                    v.checked += 1
                    why = self.check_evidence(ev)
                    if why:
                        v.failed += 1
                        bad(f"{where}.evidence[{j}]", why)
                if item.get("search_ids") and not item.get("evidence"):
                    why = self.check_search_ids(item["search_ids"])
                    if why:
                        bad(where, why)

        expected = set(self.c.findings)
        got = [f.get("id") for f in a.get("findings", [])]
        for fid in sorted(expected - set(got)):
            bad(f"findings.{fid}", "required finding not reported")
        for fid in sorted(set(got) - expected):
            bad(f"findings.{fid}", "unexpected finding id")
        for f in a.get("findings", []):
            if f.get("id") not in expected:
                continue
            if f.get("value"):
                if not f.get("evidence") and not f.get("search_ids"):
                    bad(f"findings.{f['id']}", "a true finding needs citations or zero-hit searches")
                for j, ev in enumerate(f.get("evidence", [])):
                    v.checked += 1
                    why = self.check_evidence(ev)
                    if why:
                        v.failed += 1
                        bad(f"findings.{f['id']}.evidence[{j}]", why)
                if f.get("search_ids") and not f.get("evidence"):
                    why = self.check_search_ids(f["search_ids"])
                    if why:
                        bad(f"findings.{f['id']}", why)
        return v

    # -- after the last correction round: keep only what verifies --------------
    def prune(self, a: dict) -> tuple[dict, list[str]]:
        """Drop unverifiable claims and lower the rating where the 4/4 checklist no longer holds.

        Returns (pruned assessment, notes describing what was dropped).
        """
        notes: list[str] = []
        a = {**a}
        elements = []
        lost_element = False
        for e in a.get("elements", []):
            el = self._elements.get(e.get("id"))
            if el is None:
                notes.append(f"dropped unknown element {e.get('id')!r}")
                continue
            e = {**e}
            if e["status"] in ("found", "partial"):
                kept = [ev for ev in e.get("evidence", []) if self.check_evidence(ev, el.evidence) is None]
                if len(kept) < len(e.get("evidence", [])):
                    notes.append(f"{el.id}: dropped {len(e['evidence']) - len(kept)} unverified citation(s)")
                e["evidence"] = kept
                if not kept:
                    notes.append(f"{el.id}: no verified evidence left; treated as unverified")
                    e["status"] = "missing"
                    e["note"] = (e.get("note", "") + " [unverified: citations failed code checks]").strip()
                    lost_element = True
            elif self.check_search_ids(e.get("search_ids", [])):
                e["note"] = (e.get("note", "") + " [absence not proven by a zero-hit search]").strip()
            elements.append(e)
        for el in self.c.elements:
            if el.id not in {e["id"] for e in elements}:
                elements.append({"id": el.id, "status": "missing", "evidence": [], "search_ids": [],
                                 "note": "[not assessed by the judge]"})
                lost_element = True
        a["elements"] = elements

        for section in ("what_worked", "what_to_improve", "defects"):
            kept_items = []
            for item in a.get(section, []):
                evs = [ev for ev in item.get("evidence", []) if self.check_evidence(ev) is None]
                searches_ok = bool(item.get("search_ids")) and self.check_search_ids(item["search_ids"]) is None
                if evs or (searches_ok and not item.get("evidence")):
                    kept_items.append({**item, "evidence": evs})
                else:
                    notes.append(f"dropped {section} item without verified evidence: {item.get('text', '')[:60]!r}")
            a[section] = kept_items

        findings = []
        for f in a.get("findings", []):
            if f.get("id") not in self.c.findings:
                continue
            if f.get("value"):
                evs = [ev for ev in f.get("evidence", []) if self.check_evidence(ev) is None]
                searches_ok = bool(f.get("search_ids")) and self.check_search_ids(f["search_ids"]) is None
                if not evs and not searches_ok:
                    notes.append(f"finding {f['id']}: true without verified support; recorded as not established")
                    f = {**f, "value": None}
                else:
                    f = {**f, "evidence": evs}
            findings.append(f)
        for fid in self.c.findings:
            if fid not in {f["id"] for f in findings}:
                findings.append({"id": fid, "value": None, "evidence": [], "search_ids": []})
        a["findings"] = findings

        # Rate on verified evidence only: above level 3 the 4/4 checklist must hold.
        rating = float(a.get("rating", 0))
        ceiling = self.config.scale.max - 1
        if rating > ceiling and (lost_element or any(e["status"] != "found" for e in elements)):
            notes.append(f"rating lowered from {rating:g} to {ceiling:g}: not every element is verified as found")
            a["rating"] = ceiling
        return a, notes


def format_problems(problems: list[Problem], limit: int = 25) -> str:
    lines = [f"- {p.where}: {p.message}" for p in problems[:limit]]
    if len(problems) > limit:
        lines.append(f"- … and {len(problems) - limit} more")
    return "\n".join(lines)

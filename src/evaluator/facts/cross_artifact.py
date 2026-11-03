"""Find reported metrics whose values disagree between files.

Heuristic by design: it collects (metric name, value, location) triples
from prose, Markdown tables, CSV and JSON, keeps only names containing a
metric word, and reports names whose values differ beyond rounding. The
output is a list of candidates for the judge to check, never a verdict.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict

from ..intake.snapshot import Snapshot

METRIC_WORDS = re.compile(
    r"accuracy|precision|recall|f1|kappa|agreement|latency|p50|p90|p95|p99|cost|spend|tokens|mrr|ndcg|"
    r"hit.?rate|throughput|error.?rate|auc|pass.?rate|correlation|spearman|pearson|qps|rps|duration|"
    r"false.?positive|false.?negative|tpr|fpr|consistency|bleu|rouge|exact.?match", re.I)
NUM = r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|ms|s\b|sec|seconds|\$)?"
PROSE_RE = re.compile(r"([A-Za-z][\w@ /()'\-]{1,50}?)\s*(?:=|:|\bis\b|\bwas\b|\bof\b|\bat\b)\s*\$?" + NUM)
MAX_TABLE_ROWS = 60
# Key/value tables ("metric,value"): the row label alone names the metric.
GENERIC_COLUMNS = {"value", "values", "result", "results", "score", "mean", "avg", "average", "measurement", "number"}


def _norm_key(k: str) -> str:
    k = re.sub(r"[`*_]", "", k.lower())
    k = re.sub(r"\b(the|a|an|overall|mean|average|avg|our|final|reported|measured)\b", " ", k)
    return re.sub(r"[^a-z0-9@]+", " ", k).strip()


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _decimals(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def _add(out, key, raw, unit, file, line):
    if not METRIC_WORDS.search(key):
        return
    v = _num(raw)
    if v is None:
        return
    nk = _norm_key(key)
    if nk:
        out[nk].append({"value": v, "raw": raw, "unit": (unit or "").strip(), "file": file, "line": line})


def _from_prose(path, lines, out):
    for i, line in enumerate(lines, start=1):
        if line.lstrip().startswith("|"):
            continue
        for m in PROSE_RE.finditer(line):
            _add(out, m.group(1), m.group(2), m.group(3), path, i)


def _from_md_tables(path, lines, out):
    header = None
    for i, line in enumerate(lines, start=1):
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        if header is None:
            header = cells
            continue
        label = cells[0] if cells else ""
        for h, c in zip(header[1:], cells[1:]):
            m = re.fullmatch(r"\$?" + NUM, c.replace("**", ""))
            if m:
                key = label if h.strip().lower() in GENERIC_COLUMNS else f"{label} {h}"
                _add(out, key, m.group(1), m.group(2), path, i)


def _from_csv(path, text, out):
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or len(rows) > MAX_TABLE_ROWS + 1:
        return
    header = rows[0]
    for r_i, row in enumerate(rows[1:], start=2):
        label = row[0] if row and _num(row[0]) is None else ""
        for h, c in zip(header, row):
            if _num(c) is None:
                continue
            if label and h.strip().lower() in GENERIC_COLUMNS:
                key = label
            elif len(rows) > 2:
                key = f"{label} {h}".strip()
            else:
                key = h
            _add(out, key, c, "", path, r_i)


def _from_json(path, text, out):
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return
    stack = [((), obj)]
    seen = 0
    while stack and seen < 2000:
        keys, node = stack.pop()
        seen += 1
        if isinstance(node, dict):
            stack.extend((keys + (str(k),), v) for k, v in node.items())
        elif isinstance(node, list) and len(node) <= MAX_TABLE_ROWS:
            stack.extend((keys + (str(i),), v) for i, v in enumerate(node))
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            name = " ".join(k for k in keys[-2:] if not k.isdigit())
            _add(out, name, str(node), "", path, None)


def _agree(a: dict, b: dict) -> bool:
    x, y = a["value"], b["value"]
    places = min(_decimals(a["raw"]), _decimals(b["raw"]))
    tol = 0.5 * 10 ** -places + 1e-9
    # One file may report 0.83 and another 83%.
    return any(abs(p - q) <= tol for p, q in ((x, y), (x * 100, y), (x, y * 100)))


def extract_cross_artifact(snap: Snapshot) -> dict:
    found: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(snap.files):
        low = path.lower()
        lines = snap.lines(path)
        if low.endswith((".md", ".markdown", ".txt", ".rst")):
            _from_prose(path, lines, found)
            _from_md_tables(path, lines, found)
        elif low.endswith(".csv"):
            _from_csv(path, "\n".join(lines), found)
        elif low.endswith(".json") and not low.endswith(("package.json", "package-lock.json")):
            _from_json(path, "\n".join(lines), found)

    disagreements = []
    for key, items in sorted(found.items()):
        files = {i["file"] for i in items}
        if len(files) < 2:
            continue
        for idx, a in enumerate(items):
            clash = next((b for b in items[idx + 1:] if b["file"] != a["file"] and not _agree(a, b)), None)
            if clash:
                disagreements.append({"metric": key, "a": a, "b": clash})
                break
    return {
        "metrics_found": sum(len(v) for v in found.values()),
        "metric_names_in_multiple_files": sum(1 for v in found.values() if len({i["file"] for i in v}) > 1),
        "disagreements": disagreements[:50],
        "note": "heuristic candidates; the judge verifies each against the cited lines",
    }

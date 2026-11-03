"""Markdown structure and CSV/JSON summaries."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter

from ..intake.snapshot import Snapshot

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SETEXT_RE = re.compile(r"^(=+|-+)\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
WORD_RE = re.compile(r"[A-Za-z0-9][\w'’\-]*")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
MAX_CATEGORIES = 12  # columns with more distinct values are not treated as labels
MAX_TABLE_ROWS = 200_000

MARKDOWN_EXT = (".md", ".markdown")
TABLE_EXT = (".csv", ".tsv")
JSON_EXT = (".json", ".jsonl")


def markdown_facts(path: str, lines: list[str]) -> dict:
    headings, tables, links = [], 0, []
    in_code = False
    prose_words = 0
    for i, line in enumerate(lines, start=1):
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = HEADING_RE.match(line)
        if m:
            headings.append({"level": len(m.group(1)), "text": m.group(2)[:120], "line": i})
        elif i > 1 and SETEXT_RE.match(line) and lines[i - 2].strip() and not lines[i - 2].lstrip().startswith("|"):
            headings.append({"level": 1 if line.strip()[0] == "=" else 2, "text": lines[i - 2].strip()[:120], "line": i - 1})
        if TABLE_SEP_RE.match(line) and "|" in line:
            tables += 1
        links.extend(LINK_RE.findall(line))
        prose_words += len(WORD_RE.findall(line))
    return {
        "file": path,
        "lines": len(lines),
        "words": prose_words,
        "approx_pages": round(prose_words / 500, 1),  # ~500 words per page
        "headings": headings[:150],
        "tables": tables,
        "local_links": sorted({ln.split("#")[0] for ln in links if "://" not in ln and not ln.startswith("#")})[:100],
        "has_unclosed_code_fence": in_code,
    }


def _coerce_number(v: str):
    try:
        return float(v.replace(",", "")) if v.strip() else None
    except ValueError:
        return None


def csv_facts(path: str, text: str) -> dict:
    dialect = "excel-tab" if path.lower().endswith(".tsv") else "excel"
    try:
        rows = list(csv.reader(io.StringIO(text), dialect=dialect))
    except csv.Error as e:
        return {"file": path, "error": f"unreadable CSV: {e}"}
    if not rows:
        return {"file": path, "rows": 0, "columns": []}
    header, body = rows[0], rows[1:MAX_TABLE_ROWS + 1]
    columns = []
    for j, name in enumerate(header):
        values = [r[j].strip() for r in body if j < len(r)]
        nums = [x for x in (_coerce_number(v) for v in values) if x is not None]
        col = {"name": name[:80], "non_empty": sum(1 for v in values if v)}
        if values and len(nums) >= 0.9 * len([v for v in values if v]) and nums:
            col.update(type="number", min=min(nums), max=max(nums), mean=round(sum(nums) / len(nums), 6))
        counts = Counter(v for v in values if v)
        if 0 < len(counts) <= MAX_CATEGORIES and (col.get("type") != "number" or len(counts) <= 3):
            col["value_counts"] = dict(counts.most_common())
        columns.append(col)
    ragged = sum(1 for r in body if len(r) != len(header))
    return {"file": path, "rows": len(body), "columns": columns, "ragged_rows": ragged}


def json_facts(path: str, text: str) -> dict:
    if path.lower().endswith(".jsonl"):
        lines = [ln for ln in text.splitlines() if ln.strip()]
        bad = 0
        keys: Counter = Counter()
        for ln in lines:
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    keys.update(obj.keys())
            except json.JSONDecodeError:
                bad += 1
        return {"file": path, "type": "jsonl", "records": len(lines), "invalid_lines": bad,
                "keys": [k for k, _ in keys.most_common(30)]}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return {"file": path, "error": f"invalid JSON: {e.msg} at line {e.lineno}"}
    out: dict = {"file": path, "type": type(obj).__name__}
    if isinstance(obj, dict):
        out["keys"] = list(obj.keys())[:50]
    elif isinstance(obj, list):
        out["length"] = len(obj)
        if obj and isinstance(obj[0], dict):
            out["item_keys"] = list(obj[0].keys())[:30]
    return out


def extract_documents(snap: Snapshot) -> dict:
    md, tables, jsons = [], [], []
    for path in sorted(snap.files):
        low = path.lower()
        if low.endswith(MARKDOWN_EXT):
            md.append(markdown_facts(path, snap.lines(path)))
        elif low.endswith(TABLE_EXT):
            tables.append(csv_facts(path, "\n".join(snap.lines(path))))
        elif low.endswith(JSON_EXT) and not low.endswith(("package.json", "package-lock.json", "tsconfig.json")):
            jsons.append(json_facts(path, "\n".join(snap.lines(path))))
    return {"markdown": md, "csv": tables, "json": jsons}

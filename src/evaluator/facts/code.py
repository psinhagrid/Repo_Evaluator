"""Code facts from the repo map, plus unusual-file flags."""

from __future__ import annotations

import re
import sys
from collections import Counter
from fnmatch import fnmatch
from pathlib import PurePosixPath

from ..intake.snapshot import Snapshot
from .scans import scan_model_names

TEST_GLOBS = ["test_*.py", "*_test.py", "tests/*", "test/*", "*/tests/*", "*/test/*",
              "*.test.js", "*.test.ts", "*.test.tsx", "*.spec.js", "*.spec.ts", "conftest.py"]
ENTRY_NAMES = {"main.py", "run.py", "app.py", "cli.py", "__main__.py", "manage.py", "index.js", "index.ts",
               "server.js", "server.ts"}
MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*:(?!=)")
LARGE_FILE_BYTES = 1024 * 1024
WORD_RE = re.compile(r"[\w][\w.\-]*")  # identifiers and file names such as run_all.sh
STDLIB = set(sys.stdlib_module_names)


def _is_test(path: str) -> bool:
    return any(fnmatch(path, g) or fnmatch(PurePosixPath(path).name, g) for g in TEST_GLOBS)


def extract_code(snap: Snapshot, repo_map: dict, inventory: list[dict], package_scripts: list[str]) -> dict:
    files = repo_map["files"]
    imports: Counter = Counter()
    local_modules = {PurePosixPath(p).stem for p in files} | {
        part for p in files for part in PurePosixPath(p).parts[:-1]}
    for fm in files.values():
        for imp in fm["imports"]:
            top = imp.lstrip(".").split(".")[0].split("/")[0]
            if top and top not in local_modules and top not in STDLIB and not imp.startswith("."):
                imports[top] += 1

    test_files = sorted(p for p in snap.files if _is_test(p))
    test_functions = sum(1 for p in test_files if p in files
                         for s in files[p]["symbols"] if s["name"].startswith(("test_", "test")) and s["kind"] in ("function", "method"))

    entries = []
    for p, fm in files.items():
        if fm["has_main_guard"]:
            entries.append({"file": p, "why": "__main__ guard"})
        elif PurePosixPath(p).name in ENTRY_NAMES:
            entries.append({"file": p, "why": "conventional entry name"})
    for e in inventory:
        if e["language"] == "bash" and not e["in_git_dir"]:
            entries.append({"file": e["path"], "why": "shell script"})
    make_targets = []
    for p in snap.files:
        if PurePosixPath(p).name == "Makefile":
            for i, line in enumerate(snap.lines(p), start=1):
                m = MAKE_TARGET_RE.match(line)
                if m and not m.group(1).startswith("."):
                    make_targets.append({"file": p, "line": i, "target": m.group(1)})

    code_paths = [p for p in files if snap.files[p].converted_from is None]
    return {
        "files_parsed": len(files),
        "files_with_parse_errors": sorted(p for p, fm in files.items() if fm["parse_errors"]),
        "symbol_count": sum(len(fm["symbols"]) for fm in files.values()),
        "third_party_imports": dict(imports.most_common(40)),
        "test_files": test_files,
        "test_function_count": test_functions,
        "entry_points": sorted(entries, key=lambda e: e["file"]),
        "make_targets": make_targets,
        "package_scripts": package_scripts,
        "hardcoded_model_names": scan_model_names(snap, code_paths),
        "notebooks": sorted(p for p, f in snap.files.items() if f.converted_from == "ipynb"),
    }


def find_unusual(snap: Snapshot, repo_map: dict, inventory: list[dict], entry_files: set[str]) -> list[dict]:
    """Flag files the judge should look at: unreferenced scripts, large or odd files."""
    flags = []
    # One pass over every file builds word -> files; each lookup is then O(1), so this stays linear
    # in repo size (the pairwise version was quadratic and stalled on large repos).
    mentions: dict[str, set[str]] = {}
    for q in snap.files:
        for word in WORD_RE.findall("\n".join(snap.lines(q))):
            # "run_all.sh" and "common.call_judge" must also count as mentions of "run_all" / "common".
            for w in {word, *re.split(r"[.\-]", word)}:
                mentions.setdefault(w, set()).add(q)
    for p in sorted(repo_map["files"]):
        if p in entry_files or _is_test(p) or PurePosixPath(p).name == "__init__.py":
            continue
        stem = PurePosixPath(p).stem
        name = PurePosixPath(p).name
        referenced = any(mentions.get(w, set()) - {p} for w in {stem, name})
        if not referenced:
            flags.append({"file": p, "why": "code file not referenced by any other file"})
    for e in inventory:
        if e["in_git_dir"] or e["forbidden"]:  # forbidden items are reported under hygiene
            continue
        if e["size"] > LARGE_FILE_BYTES:
            flags.append({"file": e["path"], "why": f"large file ({e['size'] // 1024} KB)"})
        elif e["kind"] == "binary" and not re.search(r"\.(png|jpe?g|gif|svg|pdf|ico|webp)$", e["path"], re.I):
            flags.append({"file": e["path"], "why": "binary file"})
        if e["hidden"] and not e["forbidden"] and PurePosixPath(e["path"]).name not in (
                ".gitignore", ".gitattributes", ".python-version", ".env.example", ".editorconfig"):
            if not e["path"].startswith((".github/", ".vscode/", ".devcontainer/")):
                flags.append({"file": e["path"], "why": "hidden file"})
    return flags

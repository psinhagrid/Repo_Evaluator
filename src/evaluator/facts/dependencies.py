"""Dependency pinning and lockfile facts."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import PurePosixPath

from ..intake.snapshot import Snapshot

LOCKFILES = {
    "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "conda-lock.yml",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "Cargo.lock", "go.sum",
}
REQ_NAME_RE = re.compile(r"^requirements[\w\-.]*\.(?:txt|in)$")
PIN_RE = re.compile(r"^[A-Za-z0-9_.\-\[\],]+\s*===?\s*[\w.\-+*!]+")
NPM_EXACT_RE = re.compile(r"^v?\d+\.\d+\.\d+([\-+][\w.\-]+)?$")


def _requirement_lines(lines: list[str]):
    for i, raw in enumerate(lines, start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):  # -r, -e, --hash and options
            continue
        line = line.split(";", 1)[0].strip()  # environment markers
        if "@" in line and "://" in line:
            yield i, line, "url"
            continue
        yield i, line, "pinned" if PIN_RE.match(line) else "loose"


def _pep508_pinned(spec: str) -> bool:
    spec = spec.split(";", 1)[0].strip()
    return bool(PIN_RE.match(spec))


def extract_dependencies(snap: Snapshot, all_paths: list[str]) -> dict:
    manifests = []
    for path in sorted(snap.files):
        name = PurePosixPath(path).name
        lines = snap.lines(path)
        if REQ_NAME_RE.match(name):
            entries = list(_requirement_lines(lines))
            manifests.append({
                "file": path, "kind": "requirements",
                "total": len(entries),
                "pinned": sum(1 for _, _, k in entries if k == "pinned"),
                "loose": [{"line": i, "spec": s} for i, s, k in entries if k == "loose"][:50],
                "has_hashes": any("--hash" in ln for ln in lines),
            })
        elif name == "pyproject.toml":
            try:
                data = tomllib.loads("\n".join(lines))
            except tomllib.TOMLDecodeError as e:
                manifests.append({"file": path, "kind": "pyproject", "error": f"invalid TOML: {e}"})
                continue
            deps = list(data.get("project", {}).get("dependencies", []) or [])
            for group in (data.get("project", {}).get("optional-dependencies") or {}).values():
                deps.extend(group)
            poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
            poetry_specs = [f"{k}=={v}" if isinstance(v, str) and re.match(r"^\d", v) else f"{k}{v}"
                            for k, v in poetry.items() if k != "python"]
            specs = deps + poetry_specs
            manifests.append({
                "file": path, "kind": "pyproject",
                "total": len(specs),
                "pinned": sum(1 for s in specs if _pep508_pinned(s)),
                "loose": [{"spec": s} for s in specs if not _pep508_pinned(s)][:50],
                "requires_python": data.get("project", {}).get("requires-python"),
            })
        elif name == "package.json":
            try:
                data = json.loads("\n".join(lines))
            except json.JSONDecodeError as e:
                manifests.append({"file": path, "kind": "package.json", "error": f"invalid JSON: {e}"})
                continue
            specs = {}
            for key in ("dependencies", "devDependencies"):
                specs.update(data.get(key) or {})
            loose = [{"spec": f"{k}@{v}"} for k, v in specs.items() if not NPM_EXACT_RE.match(str(v))]
            manifests.append({
                "file": path, "kind": "package.json",
                "total": len(specs), "pinned": len(specs) - len(loose), "loose": loose[:50],
                "scripts": sorted((data.get("scripts") or {}).keys()),
            })

    locks = sorted(p for p in all_paths if PurePosixPath(p).name in LOCKFILES)
    summary = [f"{m['file']}: {m['pinned']} of {m['total']} pinned" for m in manifests if "total" in m]
    return {
        "manifests": manifests,
        "lockfiles": locks,
        "has_lockfile": bool(locks),
        "all_pinned": bool(manifests) and all(m.get("total", 0) == m.get("pinned", -1) for m in manifests),
        "summary": summary or ["no dependency manifest found"],
    }

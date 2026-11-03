"""File inventory: type, size, and forbidden-item flags for every extracted file."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

MAX_TEXT_BYTES = 2 * 1024 * 1024  # larger files are inventoried but not snapshotted

LANGUAGES = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".sh": "bash", ".bash": "bash",
    ".ipynb": "notebook",
    ".md": "markdown", ".markdown": "markdown", ".rst": "rst", ".txt": "text",
    ".csv": "csv", ".tsv": "csv", ".json": "json", ".jsonl": "jsonl",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".cfg": "ini", ".ini": "ini",
    ".html": "html", ".css": "css", ".sql": "sql",
}

# Directory names whose presence is a hygiene defect.
FORBIDDEN_DIRS = {
    "node_modules": "dependency directory",
    "venv": "virtual environment", ".venv": "virtual environment", "env": "virtual environment",
    "__pycache__": "bytecode cache", ".pytest_cache": "test cache", ".mypy_cache": "type-check cache",
    ".ruff_cache": "lint cache", ".ipynb_checkpoints": "notebook checkpoints", ".tox": "tox environment",
    "dist": "build output", "build": "build output", ".idea": "IDE settings",
}
FORBIDDEN_FILES = {
    ".env": "environment file (may hold secrets)",
    ".DS_Store": "OS metadata",
    "Thumbs.db": "OS metadata",
}
FORBIDDEN_SUFFIXES = {".pyc": "bytecode", ".pyo": "bytecode", ".pem": "key material", ".key": "key material"}


@dataclass
class FileEntry:
    path: str
    size: int
    kind: str  # "text" | "binary"
    language: str | None
    hidden: bool
    in_git_dir: bool
    forbidden: str | None = None
    snapshot: bool = False  # included in the line-numbered snapshot
    notes: list[str] = field(default_factory=list)


def _looks_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            data[:8192].decode("utf-8")  # truncated multibyte at the boundary is fine
            return True
        except UnicodeDecodeError:
            return False


def _forbidden_reason(p: PurePosixPath) -> str | None:
    for part in p.parts[:-1]:
        if part in FORBIDDEN_DIRS:
            return f"{FORBIDDEN_DIRS[part]} ({part}/)"
    name = p.name
    if name in FORBIDDEN_FILES:
        return FORBIDDEN_FILES[name]
    if name.startswith(".env.") and name not in (".env.example", ".env.sample", ".env.template"):
        return "environment file (may hold secrets)"
    if p.suffix in FORBIDDEN_SUFFIXES:
        return FORBIDDEN_SUFFIXES[p.suffix]
    return None


def build_inventory(root: Path, files: list[str]) -> list[FileEntry]:
    entries = []
    for rel in files:
        p = PurePosixPath(rel)
        full = root / rel
        size = full.stat().st_size
        with full.open("rb") as f:
            head = f.read(min(size, MAX_TEXT_BYTES))
        is_text = _looks_text(head)
        in_git = p.parts[0] == ".git"
        entry = FileEntry(
            path=rel,
            size=size,
            kind="text" if is_text else "binary",
            language=LANGUAGES.get(p.suffix.lower()) or ("makefile" if p.name == "Makefile" else None),
            hidden=any(part.startswith(".") for part in p.parts),
            in_git_dir=in_git,
            forbidden=None if in_git else _forbidden_reason(p),
        )
        entry.snapshot = is_text and not in_git and size <= MAX_TEXT_BYTES
        if entry.forbidden:
            # Models read the snapshot: credential files never go in, and neither do dependency,
            # cache or build folders (node_modules alone can be thousands of files of other people's
            # code). They stay in the inventory and the hygiene report.
            entry.snapshot = False
            secret = any(w in entry.forbidden for w in ("secrets", "key material"))
            entry.notes.append("excluded from snapshot: " + ("may hold secrets" if secret else "not the student's work"))
        if is_text and size > MAX_TEXT_BYTES:
            entry.notes.append("text file over 2 MB; not snapshotted")
        entries.append(entry)
    return entries


def inventory_summary(entries: list[FileEntry]) -> dict:
    visible = [e for e in entries if not e.in_git_dir]
    by_lang: dict[str, int] = {}
    for e in visible:
        by_lang[e.language or "other"] = by_lang.get(e.language or "other", 0) + 1
    notebooks = by_lang.get("notebook", 0)
    code = sum(by_lang.get(k, 0) for k in ("python", "javascript", "typescript", "tsx", "go", "rust", "java", "ruby"))
    return {
        "file_count": len(visible),
        "total_bytes": sum(e.size for e in visible),
        "by_language": dict(sorted(by_lang.items())),
        "has_git_dir": any(e.in_git_dir for e in entries),
        "mostly_notebooks": notebooks > 0 and notebooks >= code,
        "forbidden": _collapse_forbidden(visible),
    }


def _collapse_forbidden(entries: list[FileEntry]) -> list[dict]:
    """Forbidden items for the report: one row per offending folder (node_modules can hold thousands
    of files), plus individual forbidden files such as .env."""
    folders: dict[str, dict] = {}
    files = []
    for e in entries:
        if not e.forbidden:
            continue
        parts = PurePosixPath(e.path).parts
        cut = next((i for i, part in enumerate(parts[:-1]) if part in FORBIDDEN_DIRS), None)
        if cut is None:
            files.append({"path": e.path, "reason": e.forbidden, "files": 1})
            continue
        folder = "/".join(parts[: cut + 1]) + "/"
        row = folders.setdefault(folder, {"path": folder, "reason": e.forbidden, "files": 0})
        row["files"] += 1
    return sorted(folders.values(), key=lambda r: r["path"]) + sorted(files, key=lambda r: r["path"])


def to_dicts(entries: list[FileEntry]) -> list[dict]:
    return [asdict(e) for e in entries]

"""Line-numbered, anonymized snapshot of every text file.

The snapshot is what models read and what citations are verified against,
so it is written once and never changes. Notebooks are converted to a
plain-text view (cells separated by markers) so line citations make sense.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .anonymize import Anonymizer
from .inventory import FileEntry
from .secrets import redact_secrets


@dataclass
class SnapshotFile:
    path: str
    sha256: str
    line_count: int
    converted_from: str | None = None
    redactions: int = 0


@dataclass
class Snapshot:
    root: Path
    files: dict[str, SnapshotFile] = field(default_factory=dict)
    secret_findings: list[dict] = field(default_factory=list)

    # -- reading ------------------------------------------------------------
    def lines(self, path: str) -> list[str]:
        if path not in self.files:
            raise KeyError(path)
        return (self.root / "files" / path).read_text(encoding="utf-8").splitlines()

    def view(self, path: str, start: int = 1, end: int | None = None) -> str:
        """Return lines start..end (1-based, inclusive) prefixed with line numbers."""
        lines = self.lines(path)
        end = len(lines) if end is None else min(end, len(lines))
        start = max(1, start)
        width = len(str(end)) if end else 1
        return "\n".join(f"{i:>{width}}\t{lines[i - 1]}" for i in range(start, end + 1))

    def verify_citation(self, path: str, start: int, end: int | None = None, quote: str | None = None) -> tuple[bool, str]:
        """Check that a cited range exists and (optionally) contains `quote`."""
        if path not in self.files:
            return False, f"file not in snapshot: {path}"
        n = self.files[path].line_count
        end = start if end is None else end
        if start < 1 or end < start:
            return False, f"invalid range {start}-{end}"
        if end > n:
            return False, f"range {start}-{end} beyond end of file ({n} lines)"
        if quote:
            window = "\n".join(self.lines(path)[start - 1:end])
            if _norm(quote) not in _norm(window):
                return False, "quoted text not found in cited lines"
        return True, "ok"

    # -- persistence --------------------------------------------------------
    def save_index(self) -> None:
        index = {p: vars(f) for p, f in sorted(self.files.items())}
        (self.root / "index.json").write_text(json.dumps(index, indent=2))
        (self.root / "secrets.json").write_text(json.dumps(self.secret_findings, indent=2))

    @classmethod
    def load(cls, root: Path) -> "Snapshot":
        root = Path(root)
        data = json.loads((root / "index.json").read_text())
        secrets = root / "secrets.json"
        found = json.loads(secrets.read_text()) if secrets.exists() else []
        return cls(root=root, files={p: SnapshotFile(**f) for p, f in data.items()}, secret_findings=found)


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def notebook_to_text(raw: str) -> str:
    """Render .ipynb JSON as readable text with one marker line per cell."""
    nb = json.loads(raw)
    out: list[str] = []
    for i, cell in enumerate(nb.get("cells", []), start=1):
        kind = cell.get("cell_type", "unknown")
        src = cell.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        out.append(f"# %% [cell {i}: {kind}]")
        out.extend(src.splitlines())
        for o in cell.get("outputs", []) or []:
            text = o.get("text") or (o.get("data") or {}).get("text/plain")
            if text:
                text = "".join(text) if isinstance(text, list) else str(text)
                out.append(f"# %% [cell {i}: output]")
                out.extend(text.splitlines()[:200])
        out.append("")
    return "\n".join(out) + "\n"


def build_snapshot(extract_root: Path, entries: list[FileEntry], anon: Anonymizer, dest: Path) -> Snapshot:
    snap = Snapshot(root=Path(dest))
    files_dir = snap.root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for e in entries:
        if not e.snapshot:
            continue
        text = (extract_root / e.path).read_text(encoding="utf-8", errors="replace")
        converted = None
        if e.language == "notebook":
            try:
                text = notebook_to_text(text)
                converted = "ipynb"
            except (json.JSONDecodeError, AttributeError, TypeError):
                e.notes.append("notebook JSON invalid; kept raw")
        # Normalize line endings so line numbers are stable across platforms.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text, n_redacted = anon.redact(text)
        text, secrets = redact_secrets(e.path, text)
        snap.secret_findings.extend(secrets)
        out = files_dir / e.path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        snap.files[e.path] = SnapshotFile(
            path=e.path,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            line_count=len(text.splitlines()),
            converted_from=converted,
            redactions=n_redacted,
        )
    snap.save_index()
    return snap

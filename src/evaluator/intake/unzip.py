"""Safe extraction of a submission ZIP.

Rejects archives that would be dangerous or unreasonable to unpack and
returns a defined outcome instead of raising for any malformed input.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_FILES = 20_000
MAX_COMPRESSION_RATIO = 200  # per entry; zip bombs typically exceed 1000x
MAX_PATH_DEPTH = 40

# Archive noise that is never part of a submission.
IGNORED_PREFIXES = ("__MACOSX/",)


class IntakeRejected(Exception):
    """The submission cannot be graded; `reason` is shown in the report."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class ExtractResult:
    archive_sha256: str
    root: Path
    files: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    stripped_prefix: str | None = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member_path(name: str) -> PurePosixPath:
    """Normalize a member name and reject anything that escapes the root."""
    if "\x00" in name:
        raise IntakeRejected("path_traversal", f"NUL byte in member name {name!r}")
    norm = name.replace("\\", "/")
    p = PurePosixPath(norm)
    if p.is_absolute() or norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        raise IntakeRejected("path_traversal", f"absolute path in archive: {name!r}")
    parts = [part for part in p.parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise IntakeRejected("path_traversal", f"path traversal in archive: {name!r}")
    if len(parts) > MAX_PATH_DEPTH:
        raise IntakeRejected("path_too_deep", f"path nested too deeply: {name!r}")
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _common_root(names: list[str]) -> str | None:
    """Return the single top-level directory shared by every member, if any."""
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) != 1:
        return None
    top = next(iter(tops))
    if all("/" in n for n in names):
        return top + "/"
    return None


def safe_extract(zip_path: Path, dest: Path) -> ExtractResult:
    """Extract `zip_path` into `dest` (which must not exist yet).

    Raises IntakeRejected with a stable code for every ungradable input.
    """
    zip_path = Path(zip_path)
    dest = Path(dest)
    if not zip_path.is_file():
        raise IntakeRejected("not_found", f"submission file not found: {zip_path.name}")
    if zip_path.stat().st_size == 0:
        raise IntakeRejected("empty", "submission file is empty")
    if not zipfile.is_zipfile(zip_path):
        raise IntakeRejected("not_a_zip", "submission is not a ZIP archive")

    archive_hash = sha256_file(zip_path)

    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise IntakeRejected("corrupt_zip", f"ZIP could not be opened: {e}") from e

    with zf:
        infos = [i for i in zf.infolist() if not i.filename.startswith(IGNORED_PREFIXES)]
        file_infos = [i for i in infos if not i.is_dir()]

        if not file_infos:
            raise IntakeRejected("empty", "archive contains no files")
        if len(file_infos) > MAX_FILES:
            raise IntakeRejected("too_many_files", f"archive has {len(file_infos)} files (max {MAX_FILES})")
        if any(i.flag_bits & 0x1 for i in file_infos):
            raise IntakeRejected("encrypted", "archive is password-protected")

        total = 0
        for i in file_infos:
            _safe_member_path(i.filename)
            total += i.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise IntakeRejected("too_large", "archive exceeds 500 MB uncompressed")
            if i.compress_size and i.file_size / i.compress_size > MAX_COMPRESSION_RATIO and i.file_size > 10 * 1024 * 1024:
                raise IntakeRejected("zip_bomb", f"suspicious compression ratio for {i.filename!r}")

        names = [str(_safe_member_path(i.filename)) for i in file_infos]
        prefix = _common_root(names)

        dest.mkdir(parents=True, exist_ok=False)
        result = ExtractResult(archive_sha256=archive_hash, root=dest, stripped_prefix=prefix)
        written = 0
        seen: set[str] = set()
        for info, rel in zip(file_infos, names):
            if _is_symlink(info):
                result.skipped.append({"path": rel, "reason": "symlink"})
                continue
            if prefix:
                rel = rel[len(prefix):]
            key = rel.lower()
            if key in seen:
                result.skipped.append({"path": rel, "reason": "duplicate path (case-insensitive)"})
                continue
            seen.add(key)
            target = dest / rel
            # Belt and braces: confirm the resolved path stays inside dest.
            if not target.resolve().is_relative_to(dest.resolve()):
                raise IntakeRejected("path_traversal", f"member escapes root: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                # Stream with a running limit: header sizes can lie.
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > MAX_UNCOMPRESSED_BYTES:
                        out.close()
                        shutil.rmtree(dest, ignore_errors=True)
                        raise IntakeRejected("too_large", "archive exceeds 500 MB uncompressed")
                    out.write(chunk)
            result.files.append(rel)

    result.files.sort()
    return result

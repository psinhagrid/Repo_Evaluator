"""Stage 1 pipeline: ZIP -> snapshot, repo map and fact sheet. No model calls."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .facts.code import extract_code, find_unusual
from .facts.cross_artifact import extract_cross_artifact
from .facts.dependencies import extract_dependencies
from .facts.documents import extract_documents
from .facts.git_history import extract_git
from .facts.scans import scan_injection, scan_placeholders, scan_secrets
from .facts.signals import evaluate_signals
from .intake.anonymize import build_anonymizer
from .intake.git_reader import GitHistory, read_history
from .intake.inventory import build_inventory, inventory_summary, to_dicts
from .intake.repomap import build_repo_map
from .intake.snapshot import build_snapshot
from .intake.unzip import IntakeRejected, safe_extract
from .scoring.config import RubricConfig

FACT_SHEET_VERSION = "1"


@dataclass
class IntakeOutput:
    status: str  # "ok" | "not_graded"
    out_dir: Path
    archive_sha256: str | None
    reason: str | None = None


def _find_git_root(root: Path) -> tuple[Path | None, str]:
    """Shallowest directory holding .git/HEAD, and its path prefix relative to root."""
    candidates = sorted(root.rglob(".git/HEAD"), key=lambda p: len(p.parts))
    if not candidates:
        return None, ""
    repo = candidates[0].parent.parent
    rel = repo.relative_to(root).as_posix()
    return repo, "" if rel == "." else rel + "/"


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def run_intake(zip_path: Path, config: RubricConfig, out_dir: Path, roster: list[dict] | None = None) -> IntakeOutput:
    """Grade-independent preparation of one submission.

    Writes into `out_dir`:
      snapshot/            anonymized, line-numbered text files + index.json
      inventory.json       every file with type, size and flags
      repo_map.json        cleaned signatures and imports
      fact_sheet.json      ground truth handed to every judge call
      identities.json      instructor-only pseudonym map (never sent to a model)
      intake.json          status, hashes and versions
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "evaluator_version": __version__,
        "fact_sheet_version": FACT_SHEET_VERSION,
        "assignment": config.assignment,
        "policy_version": config.policy_version,
        "submission_file": Path(zip_path).name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    with tempfile.TemporaryDirectory(prefix="evaluator-extract-") as tmp:
        extract_root = Path(tmp) / "submission"
        try:
            extracted = safe_extract(Path(zip_path), extract_root)
        except IntakeRejected as e:
            meta.update(status="not_graded", reason_code=e.code, reason=e.reason, archive_sha256=None)
            _write(out_dir / "intake.json", meta)
            return IntakeOutput("not_graded", out_dir, None, e.reason)

        entries = build_inventory(extract_root, extracted.files)
        summary = inventory_summary(entries)
        if not any(e.snapshot for e in entries):
            reason = "archive contains no readable text files"
            meta.update(status="not_graded", reason_code="no_text", reason=reason, archive_sha256=extracted.archive_sha256)
            _write(out_dir / "intake.json", meta)
            return IntakeOutput("not_graded", out_dir, extracted.archive_sha256, reason)

        git_root, git_prefix = _find_git_root(extract_root)
        history = read_history(git_root) if git_root else GitHistory(available=False, reason="no .git directory in submission")
        anon = build_anonymizer(extract_root, history, roster)

        snap_dir = out_dir / "snapshot"
        if snap_dir.exists():
            shutil.rmtree(snap_dir)
        snap = build_snapshot(extract_root, entries, anon, snap_dir)

    all_paths = [e.path for e in entries if not e.in_git_dir]
    inv = to_dicts(entries)
    languages = {e.path: e.language for e in entries if e.snapshot}
    repo_map = build_repo_map(snap, languages)

    docs = extract_documents(snap)
    deps = extract_dependencies(snap, all_paths)
    package_scripts = next((m.get("scripts", []) for m in deps["manifests"] if m["kind"] == "package.json"), [])
    code = extract_code(snap, repo_map, inv, package_scripts)
    git = extract_git(history, anon, set(snap.files), git_prefix)
    texts = scan_placeholders(snap)
    commit_text = git.pop("commit_messages_text", "")

    fact_sheet = {
        "fact_sheet_version": FACT_SHEET_VERSION,
        "assignment": config.assignment,
        "submission_sha256": extracted.archive_sha256,
        "inventory": summary,
        "skipped_archive_entries": extracted.skipped,
        "dependencies": deps,
        "git": git,
        "documents": docs,
        "code": code,
        "cross_artifact": extract_cross_artifact(snap),
        "placeholders": texts["placeholders"],
        "leftover_agent_text": texts["leftover_agent_text"],
        "hygiene": {
            "forbidden_files": summary["forbidden"],
            "secret_matches": scan_secrets(snap),
        },
        "injection_candidates": scan_injection(snap, {"git:commit-messages": commit_text} if commit_text else None),
        "unusual_files": find_unusual(snap, repo_map, inv, {e["file"] for e in code["entry_points"]}),
    }
    # Signals run last: some read facts computed above (e.g. cross_artifact.disagreements).
    fact_sheet["signals"] = evaluate_signals(config.signals, snap, all_paths, docs["markdown"], fact_sheet)

    _write(out_dir / "inventory.json", inv)
    _write(out_dir / "repo_map.json", repo_map)
    _write(out_dir / "fact_sheet.json", fact_sheet)
    _write(out_dir / "identities.json", anon.mapping())
    meta.update(status="ok", archive_sha256=extracted.archive_sha256, stripped_prefix=extracted.stripped_prefix,
                snapshot_files=len(snap.files), git_root_prefix=git_prefix or None)
    _write(out_dir / "intake.json", meta)
    return IntakeOutput("ok", out_dir, extracted.archive_sha256)

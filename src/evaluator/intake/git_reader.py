"""Read commit history from a submitted .git directory without trusting it.

The submitted repository's own config can name programs for git to run
(fsmonitor, pagers, gpg, diff drivers). We copy the object store to a
scratch location, replace the config with a minimal one, drop hooks and
alternates, and run git with system and global config disabled.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

FIELD = "\x1f"
RECORD = "\x1e"
GIT_TIMEOUT_S = 30
MAX_COMMITS = 5000

_MINIMAL_CONFIG = "[core]\n\trepositoryformatversion = 0\n\tbare = true\n"


@dataclass
class Commit:
    sha: str
    author_name: str
    author_email: str
    authored_at: str
    committer_name: str
    committer_email: str
    committed_at: str
    message: str
    co_authors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class GitHistory:
    available: bool
    reason: str | None = None
    head_sha: str | None = None
    commits: list[Commit] = field(default_factory=list)  # oldest first
    file_first_added: dict[str, str] = field(default_factory=dict)  # path -> ISO time


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_PAGER="cat",
        PAGER="cat",
        LC_ALL="C",
    )
    return env


def _run(git_dir: Path, *args: str) -> str:
    cmd = [
        "git", "--no-pager", f"--git-dir={git_dir}",
        "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
        "-c", "gpg.program=false", "-c", "log.showSignature=false",
        "-c", "safe.directory=*",
        *args,
    ]
    out = subprocess.run(
        cmd, capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT_S, check=True, stdin=subprocess.DEVNULL
    )
    return out.stdout.decode("utf-8", errors="replace")


def _parse_co_authors(message: str) -> list[tuple[str, str]]:
    found = []
    for line in message.splitlines():
        if line.lower().startswith("co-authored-by:"):
            value = line.split(":", 1)[1].strip()
            if "<" in value and value.endswith(">"):
                name, email = value[:-1].split("<", 1)
                found.append((name.strip(), email.strip()))
            else:
                found.append((value, ""))
    return found


def read_history(repo_root: Path) -> GitHistory:
    src = repo_root / ".git"
    if not src.exists():
        return GitHistory(available=False, reason="no .git directory in submission")
    if not src.is_dir():
        return GitHistory(available=False, reason=".git is a file (worktree link); history not included")
    if shutil.which("git") is None:
        return GitHistory(available=False, reason="git not installed on grader host")

    with tempfile.TemporaryDirectory(prefix="evaluator-git-") as tmp:
        git_dir = Path(tmp) / "repo.git"
        shutil.copytree(src, git_dir, symlinks=False, ignore_dangling_symlinks=True,
                        ignore=shutil.ignore_patterns("hooks"))
        (git_dir / "config").write_text(_MINIMAL_CONFIG)
        alternates = git_dir / "objects" / "info" / "alternates"
        alternates.unlink(missing_ok=True)
        for name in ("info/attributes", "commondir"):
            (git_dir / name).unlink(missing_ok=True)

        try:
            head = _run(git_dir, "rev-parse", "--verify", "HEAD").strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return GitHistory(available=False, reason=".git present but has no readable HEAD commit")

        fmt = FIELD.join(["%H", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%B"]) + RECORD
        try:
            raw = _run(git_dir, "log", "--reverse", f"--max-count={MAX_COMMITS}", "--no-show-signature",
                       f"--format={fmt}", "HEAD")
            added = _run(git_dir, "log", "--reverse", "--diff-filter=A", "--name-only", "--no-renames",
                         "--no-ext-diff", "--no-textconv", f"--format={RECORD}%aI", "HEAD")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return GitHistory(available=False, reason=f"git log failed: {type(e).__name__}")

    commits = []
    for rec in raw.split(RECORD):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split(FIELD)
        if len(parts) != 8:
            continue
        c = Commit(*parts[:7], message=parts[7].strip())
        c.co_authors = _parse_co_authors(c.message)
        commits.append(c)

    first_added: dict[str, str] = {}
    for block in added.split(RECORD):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        when, paths = lines[0], lines[1:]
        for p in paths:
            first_added.setdefault(p, when)

    return GitHistory(available=True, head_sha=head, commits=commits, file_first_added=first_added)

"""Commit-history facts, using pseudonyms for every person."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from ..intake.anonymize import Anonymizer
from ..intake.git_reader import GitHistory

BURST_MINUTES = 15


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def extract_git(history: GitHistory, anon: Anonymizer, snapshot_paths: set[str], path_prefix: str = "") -> dict:
    if not history.available:
        return {"available": False, "reason": history.reason, "commit_count": 0, "head_sha": None,
                "chronology": "unverifiable: no git history"}

    commits = []
    authors: Counter = Counter()
    agent_authored = agent_assisted = 0
    for c in history.commits:
        author = anon.pseudonym_for(c.author_name, c.author_email)
        co = [anon.pseudonym_for(n, e) for n, e in c.co_authors]
        authors[author] += 1
        is_agent = author.startswith("agent:")
        if is_agent and not any(not x.startswith(("agent:", "platform:")) for x in co):
            agent_authored += 1
        elif any(x.startswith("agent:") for x in co):
            agent_assisted += 1
        subject = anon.redact(c.message.splitlines()[0] if c.message else "")[0][:120]
        commits.append({"sha": c.sha[:12], "author": author, "co_authors": co,
                        "authored_at": c.authored_at, "committed_at": c.committed_at, "subject": subject})

    n = len(commits)
    times = sorted(_ts(c["authored_at"]) for c in commits)
    span_hours = round((times[-1] - times[0]).total_seconds() / 3600, 2) if n > 1 else 0.0
    if n <= 1:
        chronology = "unverifiable: single commit"
    elif span_hours * 60 <= BURST_MINUTES:
        chronology = f"weak: all {n} commits within {BURST_MINUTES} minutes"
    else:
        chronology = f"available: {n} commits over {span_hours} hours"
    out_of_order = sum(1 for a, b in zip(commits, commits[1:]) if _ts(b["committed_at"]) < _ts(a["committed_at"]))

    first_added = {}
    for p, when in history.file_first_added.items():
        full = f"{path_prefix}{p}"
        if full in snapshot_paths:
            first_added[full] = when

    return {
        "available": True,
        "head_sha": history.head_sha,
        "commit_count": n,
        "first_commit_at": commits[0]["authored_at"] if commits else None,
        "last_commit_at": commits[-1]["authored_at"] if commits else None,
        "span_hours": span_hours,
        "chronology": chronology,
        "commits_out_of_order": out_of_order,
        "authors": dict(authors.most_common()),
        "agent_only_commits": agent_authored,
        "agent_coauthored_commits": agent_assisted,
        "commits": commits[:300],
        "file_first_added": dict(sorted(first_added.items())),
        # Full messages (redacted) so the injection scan covers commit bodies too.
        "commit_messages_text": "\n".join(anon.redact(c.message)[0] for c in history.commits),
    }

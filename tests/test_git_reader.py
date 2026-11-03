import os
import subprocess

from evaluator.intake.git_reader import read_history


def test_submitted_git_config_cannot_run_commands(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@x.org", "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@x.org"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=repo, check=True, env=env)

    marker = tmp_path / "pwned"
    evil = f"touch {marker}"
    with (repo / ".git" / "config").open("a") as f:
        f.write(f"[core]\n\tfsmonitor = {evil}\n\tpager = {evil}\n[gpg]\n\tprogram = {evil}\n"
                f"[log]\n\tshowSignature = true\n[diff]\n\texternal = {evil}\n")
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\n{evil}\n")
    hook.chmod(0o755)

    h = read_history(repo)
    assert h.available and len(h.commits) == 1
    assert not marker.exists()


def test_no_git_dir(tmp_path):
    h = read_history(tmp_path)
    assert not h.available and "no .git" in h.reason

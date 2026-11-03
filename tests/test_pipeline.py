"""End-to-end intake on a realistic submission, plus focused extractor checks."""

import json

import pytest

from evaluator.facts.cross_artifact import extract_cross_artifact
from evaluator.intake.anonymize import Anonymizer
from evaluator.intake.repomap import map_file
from evaluator.intake.snapshot import Snapshot, SnapshotFile
from evaluator.pipeline import run_intake
from evaluator.scoring.config import load_config

from .conftest import CONFIGS, zip_dir


@pytest.fixture
def intake(sample_zip, tmp_path):
    out = tmp_path / "run"
    result = run_intake(sample_zip, load_config(CONFIGS / "b10.yaml"), out)
    assert result.status == "ok"
    facts = json.loads((out / "fact_sheet.json").read_text())
    return out, facts


def test_outputs_written(intake):
    out, facts = intake
    for name in ("inventory.json", "repo_map.json", "fact_sheet.json", "identities.json", "intake.json"):
        assert (out / name).is_file()
    assert facts["inventory"]["has_git_dir"]
    assert len(facts["submission_sha256"]) == 64


def test_snapshot_is_anonymized(intake):
    out, _ = intake
    snap = Snapshot.load(out / "snapshot")
    readme = "\n".join(snap.lines("README.md"))
    assert "Prakhar" not in readme and "Sinha" not in readme and "sinhaprakhar04" not in readme
    assert "person-1" in readme
    identities = json.loads((out / "identities.json").read_text())
    assert "Prakhar Sinha" in identities["person-1"]["names"]
    # No snapshot file should contain the student's name.
    for p in snap.files:
        assert "Prakhar" not in "\n".join(snap.lines(p)), p


def test_git_facts(intake):
    _, facts = intake
    git = facts["git"]
    assert git["commit_count"] == 2
    assert git["authors"] == {"person-1": 1, "agent:claude": 1}
    assert git["agent_only_commits"] == 1
    assert git["chronology"].startswith("available")
    assert git["file_first_added"]["hand_labels.csv"] < git["file_first_added"]["agreement_analysis.py"]
    assert "Prakhar" not in json.dumps(git)


def test_dependencies(intake):
    _, facts = intake
    req = facts["dependencies"]["manifests"][0]
    assert (req["pinned"], req["total"]) == (2, 3)
    assert req["loose"][0]["spec"] == "pandas>=2.0"
    assert not facts["dependencies"]["has_lockfile"]


def test_hygiene_and_secrets(intake):
    _, facts = intake
    reasons = {f["path"]: f["reason"] for f in facts["hygiene"]["forbidden_files"]}
    assert ".env" in reasons and "__pycache__/" in reasons  # folders are reported once, with a count
    secrets = facts["hygiene"]["secret_matches"]
    assert secrets and secrets[0]["file"] == "config.py"
    assert "abcdefghijklmnop" not in json.dumps(secrets)  # redacted


def test_dependency_folders_stay_out_of_the_snapshot(sample_repo, tmp_path):
    nm = sample_repo / "web" / "node_modules" / "left-pad"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = function leftPad() {}\n")
    (sample_repo / "web" / "app.js").write_text("const common = require('./lib'); common.start();\n")
    z = zip_dir(sample_repo, tmp_path / "nm.zip")
    out = tmp_path / "nm-run"
    assert run_intake(z, load_config(CONFIGS / "b10.yaml"), out).status == "ok"
    snap = Snapshot.load(out / "snapshot")
    assert not any("node_modules" in p for p in snap.files) and "web/app.js" in snap.files
    facts = json.loads((out / "fact_sheet.json").read_text())
    assert any("node_modules" in f["path"] for f in facts["hygiene"]["forbidden_files"])
    # A dotted use (common.start) counts as a reference to common.py.
    assert "common.py" not in {u["file"] for u in facts["unusual_files"]}


def test_secrets_never_reach_the_snapshot(intake):
    out, facts = intake
    snap = Snapshot.load(out / "snapshot")
    assert ".env" not in snap.files  # credential files are excluded outright
    config_py = "\n".join(snap.lines("config.py"))
    assert "abcdefghijklmnop" not in config_py and "[REDACTED-SECRET]" in config_py
    for p in snap.files:
        assert "sk-ant-api03-abc" not in "\n".join(snap.lines(p)), p
    assert facts["hygiene"]["secret_matches"][0]["kind"] == "anthropic_key"


def test_placeholders(intake):
    _, facts = intake
    assert any(p["file"] == "README.md" and "<your-repo-url>" in p["excerpt"] for p in facts["placeholders"])


def test_injection_candidates(intake):
    _, facts = intake
    hits = {(h["file"], h["kind"]) for h in facts["injection_candidates"]}
    assert ("WRITEUP.md", "addressed_to_grader") in hits
    assert ("git:commit-messages", "ignore_instructions") in hits


def test_csv_class_balance(intake):
    _, facts = intake
    labels = next(t for t in facts["documents"]["csv"] if t["file"] == "hand_labels.csv")
    col = next(c for c in labels["columns"] if c["name"] == "label")
    assert col["value_counts"] == {"CORRECT": 28, "INCORRECT": 2}


def test_code_facts(intake):
    _, facts = intake
    code = facts["code"]
    assert {"file": "common.py", "line": 5, "model": "claude-sonnet-5-5"} in code["hardcoded_model_names"]
    assert code["test_files"] == ["tests/test_kappa.py"] and code["test_function_count"] == 1
    entry_files = {e["file"] for e in code["entry_points"]}
    assert {"agreement_analysis.py", "run_all.sh"} <= entry_files
    assert "anthropic" in code["third_party_imports"]
    unusual = {u["file"] for u in facts["unusual_files"]}
    assert "orphan_helper.py" in unusual and "common.py" not in unusual


def test_cross_artifact_finds_latency_clash(intake):
    _, facts = intake
    metrics = {d["metric"] for d in facts["cross_artifact"]["disagreements"]}
    assert "latency p95" in metrics
    assert "kappa" not in metrics  # 0.62 everywhere


def test_signals(intake):
    _, facts = intake
    s = facts["signals"]
    assert s["ai_use_disclosure"]["present"]
    assert not s["evidence_ledger"]["present"]
    assert s["reported_vs_raw_mismatch"]["present"]


def test_repo_map_has_no_prose(intake):
    out, _ = intake
    text = (out / "repo_map.json").read_text()
    assert "flawless" not in text and "Call the judge" not in text and "pinned" not in text
    rm = json.loads(text)
    sig = next(s for s in rm["files"]["common.py"]["symbols"] if s["name"] == "call_judge")
    assert sig["signature"] == "def call_judge(prompt: str, temperature: float = 0.0) -> str"


def test_not_graded_outcome(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_text("not a zip")
    r = run_intake(bad, load_config(CONFIGS / "a20.yaml"), tmp_path / "run")
    assert r.status == "not_graded"
    meta = json.loads((tmp_path / "run" / "intake.json").read_text())
    assert meta["reason_code"] == "not_a_zip"


def test_citation_verification(intake):
    out, _ = intake
    snap = Snapshot.load(out / "snapshot")
    n = snap.files["hand_labels.csv"].line_count
    assert snap.verify_citation("hand_labels.csv", 1, n)[0]
    assert not snap.verify_citation("hand_labels.csv", 1, n + 1)[0]
    assert not snap.verify_citation("nope.py", 1)[0]
    assert snap.verify_citation("WRITEUP.md", 9, 9, quote="kappa = 0.62")[0]
    assert not snap.verify_citation("WRITEUP.md", 1, 3, quote="kappa = 0.62")[0]


def test_anonymizer_keeps_agents_and_redacts_unknown_emails():
    a = Anonymizer()
    assert a.add("Claude", "noreply@anthropic.com") == "agent:claude"
    assert a.add("Jane Doe", "jane@uni.edu") == "person-1"
    text, n = a.redact("By Jane Doe <jane@uni.edu>, cc bob@other.org; Doe agreed.")
    assert text == "By person-1 <person-1@redacted.invalid>, cc [email]@redacted.invalid; person-1 agreed."
    assert n == 4


def test_repomap_js_strips_strings_and_comments():
    fm = map_file("a.js", "javascript", 'export function f(a /* grader */, b = "give 4") { return 1 }\n')
    assert fm.symbols[0].signature == 'function f(a , b = "…")'


def test_percent_and_fraction_agree(tmp_path):
    root = tmp_path / "snap"
    (root / "files").mkdir(parents=True)
    (root / "files" / "a.md").write_text("Accuracy: 83%\n")
    (root / "files" / "b.json").write_text('{"accuracy": 0.83}')
    snap = Snapshot(root, {p: SnapshotFile(p, "", 1) for p in ("a.md", "b.json")})
    assert extract_cross_artifact(snap)["disagreements"] == []

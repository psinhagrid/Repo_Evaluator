"""Step 3: whole-submission grading with a scripted model."""

import asyncio
import shutil

import pytest
import yaml

from evaluator.grading import GradeOptions, grade_submission
from evaluator.pipeline import run_intake
from evaluator.scoring.config import load_config, parse_config

from .conftest import CONFIGS, zip_dir
from .fakes import FakeGrader, ScriptedLLM

A20_RATINGS = {"ground_truth": 3, "agreement_analysis": 2.5, "deployment_recommendation": 3,
               "professional_baseline": 2, "ai_collaboration": 0}
B10_RATINGS = {"decision_framing": 3, "architecture_reconstruction": 3, "experimental_rigor": 2.5,
               "production_reasoning": 3, "evidence_quality": 2, "synthesis": 3, "ai_use_transparency": 4,
               "communication": 1.5}


def _intake(zip_path, tmp_path, config, name="run"):
    out = tmp_path / name
    assert run_intake(zip_path, config, out).status == "ok"
    return out


def _grade(run_dir, config, fake, **opts):
    llm = ScriptedLLM(fake)
    report = asyncio.run(grade_submission(run_dir, config, llm, GradeOptions(**opts)))
    return report, llm


@pytest.fixture
def a20():
    return load_config(CONFIGS / "a20.yaml")


@pytest.fixture
def b10():
    return load_config(CONFIGS / "b10.yaml")


def test_a20_end_to_end(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    fake = FakeGrader(a20, A20_RATINGS)
    report, llm = _grade(run, a20, fake)
    assert report["status"] == "graded"
    assert report["score"]["total"] == 60
    assert [s["rating"] for s in report["sections"]] == [3, 2.5, 3, 2, 0]
    # No bands -> no near-cutoff reruns; no judgment caps or zero-rule hits -> one run each.
    assert all(len(s["runs"]) == 1 for s in report["sections"])
    assert len(report["top_defects"]) == 3 and report["verdict"]
    d = next(p for p in (run / "grades").iterdir())
    for f in ("report.json", "report_instructor.md", "report_student.md", "usage.json", "pre.json"):
        assert (d / f).is_file(), f
    assert report["cost"]["total_usd"] > 0 and "judge:ground_truth" in report["cost"]["by_purpose"]


def test_first_criterion_runs_alone_then_parallel(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    _, llm = _grade(run, a20, FakeGrader(a20, A20_RATINGS))
    from .fakes import criterion_of
    judge_order = [criterion_of(p) for p in llm.requests if not isinstance(p["system"], str)]
    # ground_truth's two turns finish before any other criterion starts.
    assert judge_order[:2] == ["ground_truth", "ground_truth"]
    assert set(judge_order[2:]) == {c.id for c in a20.criteria[1:]}


def test_bias_isolation_in_prompts(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    _, llm = _grade(run, a20, FakeGrader(a20, A20_RATINGS))
    from .fakes import criterion_of
    first = {criterion_of(p): p["messages"][0]["content"] for p in llm.requests if not isinstance(p["system"], str)}
    aa = first["agreement_analysis"]
    assert "README.md:3 claims: run_all.sh reproduces all results" in aa
    assert "README.md" not in aa.split("Files you may open")[1].split("\n")[0]
    assert "claims:" not in first["ground_truth"]  # only doc_claims criteria get claims
    # Suspected-injection excerpts never reach the judge prompt.
    judge_req = next(p for p in llm.requests if not isinstance(p["system"], str))
    shared = judge_req["system"][1]["text"]
    assert "perfect score" not in shared and "award full marks" not in shared


def test_cached_regrade_makes_no_calls_and_policy_change_rescores(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    _grade(run, a20, FakeGrader(a20, A20_RATINGS))
    report, llm = _grade(run, a20, FakeGrader(a20, A20_RATINGS))
    assert llm.requests == [] and report["score"]["total"] == 60

    data = yaml.safe_load((CONFIGS / "a20.yaml").read_text())
    data["bands"] = [{"name": "Pass", "min": 50}, {"name": "Fail", "min": 0}]
    data["policy_version"] = "2026-09-01"
    new = parse_config(data)
    assert new.judge_hash == a20.judge_hash and new.config_hash != a20.config_hash
    report, llm = _grade(run, new, FakeGrader(new, A20_RATINGS), rerun_triggers=False)
    judge_calls = [p for p in llm.requests if not isinstance(p["system"], str)]
    assert judge_calls == [] and report["score"]["band"] == "Pass" and report["policy_version"] == "2026-09-01"


def test_b10_caps_and_near_cutoff_reruns_take_median(sample_zip, tmp_path, b10):
    run = _intake(sample_zip, tmp_path, b10)
    ratings = dict(B10_RATINGS, architecture_reconstruction=[3, 2, 2.5])
    fake = FakeGrader(b10, ratings)
    report, _ = _grade(run, b10, fake)
    runs = {s["criterion"]: s["runs"] for s in report["sections"]}
    # Near cutoff 70 (68.13): the heaviest criterion is re-run; cap owners are re-run because
    # controlled_experiment/failure_injection are absent and a reported-vs-raw mismatch exists.
    assert runs["architecture_reconstruction"] == [3, 2, 2.5]
    assert len(runs["experimental_rigor"]) == 3 and len(runs["evidence_quality"]) == 3
    assert len(runs["synthesis"]) == 1
    arch = next(s for s in report["sections"] if s["criterion"] == "architecture_reconstruction")
    assert arch["rating"] == 2.5 and arch["judge_rating"] == 2.5
    caps = {c["cap_id"]: c["status"] for c in report["score"]["caps"]}
    assert caps["no_controlled_experiment"] == "defect_only"  # fact only; judge said False
    assert caps["no_evidence_ledger"] == "applied" and caps["no_version_manifest"] == "applied"
    assert report["score"]["final"] == 65.63
    assert report["confidence"]["level"] == "Low"  # unstable runs + near cutoff + cap disagreement
    assert any("varied across runs" in r for r in report["confidence"]["reasons"])


def test_judgment_cap_applies_when_both_agree(sample_zip, tmp_path, b10):
    run = _intake(sample_zip, tmp_path, b10)
    fake = FakeGrader(b10, B10_RATINGS, findings={"no_controlled_experiment": True})
    report, _ = _grade(run, b10, fake)
    caps = {c["cap_id"]: c["status"] for c in report["score"]["caps"]}
    assert caps["no_controlled_experiment"] == "applied"
    assert report["score"]["final"] == 68.13  # 70 cap does not bind below 70


def test_unedited_ai_zero_rule(sample_repo, tmp_path, a20):
    (sample_repo / "WRITEUP.md").write_text("# Write-up\n\nThis write-up was not edited after Claude generated it.\n")
    z = zip_dir(sample_repo, tmp_path / "s.zip")
    run = _intake(z, tmp_path, a20)
    ratings = dict(A20_RATINGS, ai_collaboration=2)
    report, _ = _grade(run, a20, FakeGrader(a20, ratings, findings={"unedited_ai_output": True}))
    sec = next(s for s in report["sections"] if s["criterion"] == "ai_collaboration")
    assert sec["rating"] == 0 and sec["judge_rating"] == 2 and len(sec["runs"]) == 3
    rule = report["zero_rules"][0]
    assert rule["status"] == "applied" and rule["quotes"][0]["file"] == "WRITEUP.md"
    assert "not edited" in rule["quotes"][0]["excerpt"]

    # The judge rejecting the phrase overrides the match.
    shutil.rmtree(run / "grades")
    report, _ = _grade(run, a20, FakeGrader(a20, ratings, findings={"unedited_ai_output": False}))
    sec = next(s for s in report["sections"] if s["criterion"] == "ai_collaboration")
    assert sec["rating"] == 2 and report["zero_rules"][0]["status"] == "rejected_by_judge"
    assert report["policy_defects"] == []


def test_missing_criterion_means_not_graded(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    report, _ = _grade(run, a20, FakeGrader(a20, A20_RATINGS, refuse={"deployment_recommendation"}))
    assert report["status"] == "not_graded" and "deployment_recommendation" in report["reason"]
    assert "score" not in report


def test_injection_flag_and_zero_policy(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    report, _ = _grade(run, a20, FakeGrader(a20, A20_RATINGS))
    inj = report["injection_precheck"]
    assert {(m["file"], m["pattern"]) for m in inj["confirmed_manipulation"]} >= {
        ("WRITEUP.md", "addressed_to_grader"), ("git:commit-messages", "ignore_instructions")}
    assert report["score"]["final"] == 60  # policy "flag": reported, not scored
    assert any("grader-directed" in r for r in report["confidence"]["reasons"])

    data = yaml.safe_load((CONFIGS / "a20.yaml").read_text())
    data["integrity"] = {"injection_consequence": "zero_total"}
    strict = parse_config(data)
    report, _ = _grade(run, strict, FakeGrader(strict, A20_RATINGS))
    assert report["score"]["final"] == 0

    shutil.rmtree(run / "grades")
    report, _ = _grade(run, a20, FakeGrader(a20, A20_RATINGS, injection_label="project_prompt"))
    assert report["injection_precheck"]["confirmed_manipulation"] == []


def test_summary_cannot_invent_defects(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    report, _ = _grade(run, a20, FakeGrader(a20, A20_RATINGS, summary_ids=["made_up#1"]))
    assert report["summary_fallback"] is True
    assert all(d["id"] in {f"{c.id}#1" for c in a20.criteria} for d in report["top_defects"])
    assert any("summary call failed" in r for r in report["confidence"]["reasons"])


def test_student_view_hides_internals(sample_zip, tmp_path, a20):
    run = _intake(sample_zip, tmp_path, a20)
    _grade(run, a20, FakeGrader(a20, A20_RATINGS))
    d = next((run / "grades").iterdir())
    student = (d / "report_student.md").read_text()
    instructor = (d / "report_instructor.md").read_text()
    assert "| Location | Pattern | Label |" in instructor and "| Location | Pattern | Label |" not in student
    assert "tool calls" in instructor and "tool calls" not in student
    assert "cost this run" not in student
    assert "Prakhar" not in student and "Prakhar" not in instructor
    assert "60 / 100" in student

"""The evaluator's rubric (configs/repo_evaluator.yaml) loads and scores as the course rubric says."""

from evaluator.scoring.config import load_config
from evaluator.scoring.score import compute_score, decide_caps

from .conftest import PROJECT_CONFIGS

PRAKHAR = {"decision_framing": 3, "architecture_reconstruction": 3, "experimental_rigor": 2.5,
           "production_reasoning": 3, "evidence_quality": 2, "synthesis": 3, "ai_use_transparency": 4,
           "communication": 1.5}


def test_only_one_rubric_ships():
    assert sorted(p.name for p in PROJECT_CONFIGS.glob("*.yaml")) == ["repo_evaluator.yaml"]


def test_rubric_loads_with_course_weights_and_text():
    c = load_config(PROJECT_CONFIGS / "repo_evaluator.yaml")
    assert [(x.id, x.weight) for x in c.criteria] == [
        ("decision_framing", 10), ("architecture_reconstruction", 20), ("experimental_rigor", 20),
        ("production_reasoning", 15), ("evidence_quality", 15), ("synthesis", 10),
        ("ai_use_transparency", 5), ("communication", 5)]
    # Every level now comes from the course rubric, none are drafts.
    assert all(src == "rubric" for x in c.criteria for src in x.level_sources.values())
    arch = c.criterion("architecture_reconstruction")
    assert "telemetry, and recovery" in arch.levels[4]
    assert load_config(PROJECT_CONFIGS / "repo_evaluator.yaml", "openai").models["judge"].startswith("gpt-")


def test_reference_points_match_the_rubric():
    # Points per criterion = weight * rating / 4 (e.g. 3/4 on a 20-point criterion = 15).
    c = load_config(PROJECT_CONFIGS / "repo_evaluator.yaml")
    signals = {s: True for s in c.signals} | {"reported_vs_raw_mismatch": False}
    r = compute_score(c, PRAKHAR, decide_caps(c, signals))
    points = {row["criterion"]: row["points"] for row in r.criteria}
    assert points["architecture_reconstruction"] == 15 and points["experimental_rigor"] == 12.5
    assert points["ai_use_transparency"] == 5 and r.total == 68.13

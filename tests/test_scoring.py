import copy

import pytest
import yaml

from evaluator.scoring.config import ConfigError, load_config, parse_config
from evaluator.scoring.score import ScoringError, compute_score, decide_caps

from .conftest import CONFIGS

B10_PRAKHAR = {"decision_framing": 3, "architecture_reconstruction": 3, "experimental_rigor": 2.5,
               "production_reasoning": 3, "evidence_quality": 2, "synthesis": 3, "ai_use_transparency": 4,
               "communication": 1.5}
A20_PRAKHAR = {"ground_truth": 3, "agreement_analysis": 2.5, "deployment_recommendation": 3,
               "professional_baseline": 2, "ai_collaboration": 0}
ALL_SIGNALS_OK = {"version_manifest": True, "controlled_experiment": True, "failure_injection": True,
                  "evidence_ledger": True, "ai_use_disclosure": True, "reported_vs_raw_mismatch": False}


@pytest.fixture
def b10():
    return load_config(CONFIGS / "b10.yaml")


@pytest.fixture
def a20():
    return load_config(CONFIGS / "a20.yaml")


def test_configs_load_and_weights_total_100(a20, b10):
    assert sum(c.weight for c in a20.criteria) == 100
    assert sum(c.weight for c in b10.criteria) == 100


def test_b10_prakhar_conversion_matches_spec(b10):
    r = compute_score(b10, B10_PRAKHAR, decide_caps(b10, ALL_SIGNALS_OK))
    assert r.total == 68.13  # spec: "converted, they total 68.13"
    assert r.final == 68.13
    assert r.band == "Revision required"


def test_a20_prakhar(a20):
    r = compute_score(a20, A20_PRAKHAR)
    assert r.total == 60
    assert r.band is None  # A20 bands not yet defined


def test_weights_must_total_100():
    data = yaml.safe_load((CONFIGS / "a20.yaml").read_text())
    data["criteria"][0]["weight"] = 26
    with pytest.raises(ConfigError, match="total 101"):
        parse_config(data)


def test_missing_level_rejected():
    data = yaml.safe_load((CONFIGS / "a20.yaml").read_text())
    del data["criteria"][0]["levels"][2]
    with pytest.raises(ConfigError, match="missing level"):
        parse_config(data)


def test_missing_or_offscale_rating_rejected(a20):
    partial = dict(A20_PRAKHAR)
    del partial["ai_collaboration"]
    with pytest.raises(ScoringError, match="missing"):
        compute_score(a20, partial)
    with pytest.raises(ScoringError, match="not on scale"):
        compute_score(a20, {**A20_PRAKHAR, "ground_truth": 3.25})
    with pytest.raises(ScoringError, match="not on scale"):
        compute_score(a20, {**A20_PRAKHAR, "ground_truth": 4.5})


def test_fact_cap_applies_and_lowest_cap_binds(b10):
    perfect = {k: 4 for k in B10_PRAKHAR}
    signals = {**ALL_SIGNALS_OK, "version_manifest": False, "ai_use_disclosure": False}
    r = compute_score(b10, perfect, decide_caps(b10, signals))
    assert r.total == 100
    assert r.final == 75
    assert r.binding_cap == "no_ai_disclosure"
    assert {c.cap_id for c in r.caps if c.status == "applied"} == {"no_version_manifest", "no_ai_disclosure"}


def test_judgment_cap_needs_both_signals(b10):
    perfect = {k: 4 for k in B10_PRAKHAR}
    fact_only = {**ALL_SIGNALS_OK, "controlled_experiment": False}
    r = compute_score(b10, perfect, decide_caps(b10, fact_only))
    cap = next(c for c in r.caps if c.cap_id == "no_controlled_experiment")
    assert cap.status == "defect_only" and r.final == 100

    r = compute_score(b10, perfect, decide_caps(b10, fact_only, {"no_controlled_experiment": True}))
    assert r.final == 70

    judge_only = decide_caps(b10, ALL_SIGNALS_OK, {"fabricated_results": True})
    assert next(c for c in judge_only if c.cap_id == "fabricated_results").status == "defect_only"


def test_band_boundaries_use_thresholds(b10):
    cfg = copy.deepcopy(b10)
    from evaluator.scoring.score import band_for
    assert band_for(cfg, 89.99) == "Strong pass"
    assert band_for(cfg, 90.0) == "Distinction"
    assert band_for(cfg, 89.5) == "Strong pass"
    assert band_for(cfg, 0) == "Insufficient"


def test_near_cutoff_flag(b10):
    r = compute_score(b10, B10_PRAKHAR, decide_caps(b10, ALL_SIGNALS_OK))
    assert r.nearest_cutoff == {"cutoff": 70, "distance": 1.87}
    assert any("within 3" in f for f in r.flags)


def test_unknown_signal_in_cap_rejected():
    data = yaml.safe_load((CONFIGS / "b10.yaml").read_text())
    data["caps"][0]["signal"] = "nope"
    with pytest.raises(ConfigError, match="unknown signal"):
        parse_config(data)

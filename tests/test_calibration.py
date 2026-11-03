"""Step 4: calibration metrics, Haiku routing, README swap test, baseline gate."""

import asyncio
import zipfile


from evaluator.calibration import (Calibrator, agreement, compare_baseline, criterion_verdict, readme_hidden_criteria,
                                   render_calibration, stability, swap_readme)
from evaluator.scoring.config import load_config

from .conftest import CONFIGS, zip_dir
from .fakes import FakeGrader, ScriptedLLM, criterion_of


def test_agreement_and_stability_metrics():
    a = agreement([(3, 3), (2.5, 3), (2, 3), (4, 3)])
    assert a["n"] == 4 and a["within_half"] == 0.5 and a["exact"] == 0.25
    assert a["mae"] == 0.625 and a["bias"] == -0.125 and a["max_abs"] == 1
    harsh = agreement([(2, 3)] * 10)
    assert harsh["bias"] == -1 and harsh["bias_p"] < 0.01
    assert any("harsh" in i for i in criterion_verdict(harsh, {"n": 0}))
    s = stability([[3, 3, 3], [2, 3, 2.5], [1]])
    assert s == {"n": 2, "mean_range": 0.5, "all_equal": 0.5, "within_half": 0.5, "max_range": 1}
    assert any("unstable" in i for i in criterion_verdict({"n": 0}, {"n": 2, "mean_range": 0.75}))


def test_baseline_regression_gate():
    base = {"criteria": {"x": {"agreement": {"n": 30, "within_half": 0.9, "mae": 0.3}, "stability": {"n": 30, "mean_range": 0.2}}}}
    same = {"criteria": {"x": {"agreement": {"n": 30, "within_half": 0.88, "mae": 0.35}, "stability": {"n": 30, "mean_range": 0.25}}}}
    worse = {"criteria": {"x": {"agreement": {"n": 30, "within_half": 0.8, "mae": 0.5}, "stability": {"n": 30, "mean_range": 0.4}}}}
    assert compare_baseline(same, base) == []
    assert len(compare_baseline(worse, base)) == 3


def test_swap_readme_replaces_top_level_only(sample_zip, tmp_path):
    out = swap_readme(sample_zip, tmp_path / "x.zip", "# New\n")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        readme = [n for n in names if n.endswith("README.md")]
        assert readme == ["prakhar-a20/README.md"] and z.read(readme[0]) == b"# New\n"
        assert len(names) == len(zipfile.ZipFile(sample_zip).namelist())


def test_readme_hidden_criteria():
    assert readme_hidden_criteria(load_config(CONFIGS / "a20.yaml")) == ["agreement_analysis"]
    assert readme_hidden_criteria(load_config(CONFIGS / "b10.yaml")) == []


def _calibration_set(sample_repo, tmp_path, n=4):
    zips = {}
    for i in range(n):
        (sample_repo / "NOTE.md").write_text(f"# Note {i}\n")
        zips[f"s{i}"] = zip_dir(sample_repo, tmp_path / f"s{i}.zip")
    human = {f"s{i}": {"ground_truth": 3, "agreement_analysis": 2.5, "deployment_recommendation": 3,
                       "professional_baseline": 2, "ai_collaboration": 1} for i in range(n)}
    return zips, human


A20 = {"ground_truth": 3, "agreement_analysis": 2.5, "deployment_recommendation": [3, 2, 2],
       "professional_baseline": 2, "ai_collaboration": 2}


def test_calibration_run(sample_repo, tmp_path):
    a20 = load_config(CONFIGS / "a20.yaml")
    zips, human = _calibration_set(sample_repo, tmp_path, n=6)  # 4 would be too few for the sign test
    fake = FakeGrader(a20, A20)
    cal = Calibrator(a20, ScriptedLLM(fake), tmp_path / "cal", runs=3)
    rep = asyncio.run(cal.run(zips, human, haiku_criteria=["professional_baseline"]))
    c = rep["criteria"]
    assert rep["submissions"] == 6
    assert c["ground_truth"]["agreement"]["within_half"] == 1.0 and c["ground_truth"]["issues"] == []
    # ai_collaboration: judge 2 vs human 1 every time -> lenient, outside half a level.
    assert any("lenient" in i for i in c["ai_collaboration"]["issues"])
    # deployment: first judge run 3, later runs 2 -> unstable.
    assert c["deployment_recommendation"]["stability"]["mean_range"] > 0
    assert rep["haiku_routing"]["professional_baseline"]["route_to_haiku"] is True
    assert rep["readme_swap"]["criteria"] == ["agreement_analysis"] and rep["readme_swap"]["leaks"] == []
    assert rep["passed"] is False
    md = render_calibration(rep)
    assert "| ground_truth | 6 | 100% |" in md and "route to Haiku" in md


class LeakyFake(FakeGrader):
    """Rates a code criterion higher when the README's documentation claims sound impressive."""

    def __call__(self, params):
        system = params["system"]
        if isinstance(system, str) and system.startswith("You extract factual claims"):
            user = params["messages"][0]["content"]
            claim = "fully reproducible with one command" if "rigorous" in user else "runs analysis"
            from .fakes import final
            return final({"claims": [{"file": "README.md", "line": 1, "claim": claim}]}, model="claude-haiku-5-5")
        if not isinstance(system, str) and criterion_of(params) == "agreement_analysis":
            bump = "fully reproducible" in params["messages"][0]["content"]
            self.ratings["agreement_analysis"] = [3.5 if bump else 2.5]
        return super().__call__(params)


def test_readme_swap_detects_leak(sample_repo, tmp_path):
    a20 = load_config(CONFIGS / "a20.yaml")
    zips, human = _calibration_set(sample_repo, tmp_path, n=2)
    cal = Calibrator(a20, ScriptedLLM(LeakyFake(a20, A20)), tmp_path / "cal", runs=1)
    rep = asyncio.run(cal.run(zips, human))
    assert rep["readme_swap"]["leaks"] and "agreement_analysis: polished README" in rep["readme_swap"]["leaks"][0]
    assert rep["passed"] is False

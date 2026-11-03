"""Every CLI command end to end, with the scripted model in place of the API."""

import json

from evaluator import cli
from evaluator.batch import LLMBatchAPI
from evaluator.scoring.config import load_config

from .conftest import CONFIGS, zip_dir
from .fakes import FakeGrader, ScriptedLLM

A20 = {"ground_truth": 3, "agreement_analysis": 2.5, "deployment_recommendation": 3,
       "professional_baseline": 2, "ai_collaboration": 1}


def test_cli_end_to_end(sample_repo, tmp_path, monkeypatch, capsys):
    a20 = load_config(CONFIGS / "a20.yaml")
    monkeypatch.setattr(cli, "_llm", lambda config: ScriptedLLM(FakeGrader(a20, A20)))
    monkeypatch.setattr(cli, "_batch_api", lambda config: LLMBatchAPI(ScriptedLLM(FakeGrader(a20, A20))))
    cfg = str(CONFIGS / "a20.yaml")
    z = zip_dir(sample_repo, tmp_path / "sub.zip")

    assert cli.main(["intake", str(z), "--config", cfg, "--out", str(tmp_path / "run")]) == 0
    assert cli.main(["grade", str(tmp_path / "run"), "--config", cfg]) == 0
    assert "final=62.5" in capsys.readouterr().out
    assert cli.main(["judge", str(tmp_path / "run"), "--config", cfg, "--criterion", "ground_truth"]) == 0
    assert json.loads(capsys.readouterr().out)["rating"] == 3
    assert cli.main(["judge", str(tmp_path / "run"), "--config", cfg, "--criterion", "nope"]) == 1

    zips = tmp_path / "zips"
    zips.mkdir()
    for i in range(2):
        (sample_repo / "NOTE.md").write_text(f"# {i}\n")
        zip_dir(sample_repo, zips / f"c{i}.zip")
    assert cli.main(["cohort", str(zips), "--config", cfg, "--out", str(tmp_path / "cohort"), "--batch"]) == 0
    assert (tmp_path / "cohort" / "cohort.md").is_file() and (tmp_path / "cohort" / "cohort.csv").is_file()

    human = tmp_path / "human.csv"
    human.write_text("submission,criterion,rating\n" + "".join(
        f"c{i},{c},{r}\n" for i in range(2) for c, r in A20.items()))
    code = cli.main(["calibrate", str(zips), "--human", str(human), "--config", cfg, "--out", str(tmp_path / "cal"),
                     "--runs", "1"])
    assert code == 0 and "PASS" in capsys.readouterr().out

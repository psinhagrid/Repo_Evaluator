"""Local API for the frontend: discovery, documents, file lines, upload-and-grade jobs."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from evaluator.cohort import run_cohort
from evaluator.scoring.config import load_config
from evaluator.server import create_app

from .conftest import CONFIGS, zip_dir
from .fakes import FakeGrader, ScriptedLLM

A20 = {"ground_truth": 3, "agreement_analysis": 2.5, "deployment_recommendation": 3,
       "professional_baseline": 2, "ai_collaboration": 1}


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Run outside the repo so its real .env is never read; fake credentials satisfy the check.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("EVALUATOR_MODELS", raising=False)
    return tmp_path


@pytest.fixture
def client(env, sample_repo):
    a20 = load_config(CONFIGS / "a20.yaml")
    ws = env / "workspace"
    zips = []
    for i in range(2):
        (sample_repo / "NOTE.md").write_text(f"# {i}\n")
        zips.append(zip_dir(sample_repo, env / f"c{i}.zip"))
    asyncio.run(run_cohort(zips, a20, ws / "cohorts" / "a20-fall", llm=ScriptedLLM(FakeGrader(a20, A20))))
    app = create_app(ws, CONFIGS, llm_factory=lambda config: ScriptedLLM(FakeGrader(config, A20)))
    with TestClient(app) as c:  # keeps one event loop alive, so background grading jobs can finish
        yield c


def test_health_and_configs(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["providers"]["anthropic"] is True
    cfgs = {c["name"]: c for c in client.get("/api/configs").json()}
    assert set(cfgs) >= {"a20", "b10"}
    assert cfgs["a20"]["profiles"] == ["default", "openai"] and len(cfgs["a20"]["criteria"]) == 5
    status = cfgs["a20"]["profile_status"]
    # Only an Anthropic key is set in this test: Claude profile ready, OpenAI profile not.
    assert status["default"]["ready"] is True and status["default"]["models"]["judge"] == "claude-sonnet-5-5"
    assert status["openai"] == {**status["openai"], "ready": False, "missing": ["openai"]}
    assert cfgs["a20"]["suggested_profile"] == "default"


def test_openai_only_setup_suggests_openai(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("HOME", "/nonexistent")  # no `ant` profile
    cfg = next(c for c in client.get("/api/configs").json() if c["name"] == "a20")
    assert cfg["profile_status"]["default"]["missing"] == ["anthropic"]
    assert cfg["profile_status"]["openai"]["ready"] is True
    assert cfg["suggested_profile"] == "openai"


def test_index_and_documents(client):
    idx = client.get("/api/index").json()
    assert len(idx["cohorts"]) == 1 and idx["cohorts"][0]["graded"] == 2
    assert len(idx["reports"]) == 2 and all(r["final"] == 62.5 for r in idx["reports"])
    cid = idx["cohorts"][0]["id"]
    cohort = client.get(f"/api/cohorts/{cid}").json()
    assert [r["final"] for r in cohort["ranking"]] == [62.5, 62.5]
    reports = client.get(f"/api/cohorts/{cid}/reports").json()
    assert len(reports) == 2 and reports[0]["status"] == "graded"
    rid = idx["reports"][0]["id"]
    rep = client.get(f"/api/reports/{rid}").json()
    assert rep["judge_key"] == rid
    by_sha = client.get(f"/api/reports/{rep['submission_sha256'][:12]}").json()
    assert by_sha["submission_sha256"] == rep["submission_sha256"]
    assert client.get("/api/reports/nope").status_code == 404
    assert client.get("/api/cohorts/nope").status_code == 404


def test_file_lines_only_from_snapshot(client):
    rid = client.get("/api/index").json()["reports"][0]["id"]
    f = client.get(f"/api/reports/{rid}/file", params={"path": "hand_labels.csv", "start": 30, "end": 31}).json()
    assert f["total_lines"] == 31 and [x["n"] for x in f["lines"]] == [30, 31]
    assert f["lines"][0]["text"] == "28,INCORRECT"
    readme = client.get(f"/api/reports/{rid}/file", params={"path": "README.md"}).json()
    assert "Prakhar" not in str(readme)  # anonymized snapshot, never the raw upload
    for bad in ("../../.env", "/etc/passwd", ".env", "__pycache__/common.cpython-312.pyc"):
        assert client.get(f"/api/reports/{rid}/file", params={"path": bad}).status_code == 404


def test_upload_and_grade_job(client, sample_repo, env):
    z = zip_dir(sample_repo, env / "upload.zip")
    with z.open("rb") as fh:
        r = client.post("/api/grade", data={"config": "a20"}, files={"file": ("upload.zip", fh, "application/zip")})
    assert r.status_code == 202, r.text
    jid = r.json()["id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{jid}").json()
        if job["status"] in ("done", "failed", "not_graded"):
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    assert job["final"] == 62.5 and job["progress"] == {"done": 5, "total": 5}
    rep = client.get(f"/api/reports/{job['report_id']}").json()
    assert rep["status"] == "graded"
    assert any(j["id"] == jid for j in client.get("/api/jobs").json())


def test_grade_rejects_bad_input(client, monkeypatch, env):
    assert client.post("/api/grade", data={"config": "nope"},
                       files={"file": ("x.zip", b"PK", "application/zip")}).status_code == 400
    assert client.post("/api/grade", data={"config": "a20"},
                       files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("HOME", str(env))  # no `ant` profile either
    r = client.post("/api/grade", data={"config": "a20"}, files={"file": ("x.zip", b"PK", "application/zip")})
    assert r.status_code == 400 and "missing credentials" in r.json()["detail"]


def test_cors_allows_local_dev_only(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:8080"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8080"
    evil = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_resubmitting_same_zip_reuses_finished_work(client, sample_repo, env):
    z = zip_dir(sample_repo, env / "again.zip")

    def submit_and_wait():
        with z.open("rb") as fh:
            jid = client.post("/api/grade", data={"config": "a20"},
                              files={"file": ("again.zip", fh, "application/zip")}).json()["id"]
        for _ in range(100):
            job = client.get(f"/api/jobs/{jid}").json()
            if job["status"] in ("done", "failed", "not_graded"):
                return job
            time.sleep(0.05)
        return job

    first, second = submit_and_wait(), submit_and_wait()
    assert first["status"] == second["status"] == "done"
    assert first["run"] == second["run"] and first["report_id"] == second["report_id"]

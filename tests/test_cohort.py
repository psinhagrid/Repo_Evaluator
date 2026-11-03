"""Step 5: batch rounds, batch API contract, similarity, cohort view."""

import asyncio
import json

import anthropic
import httpx2
import pytest

from evaluator.batch import AnthropicBatchAPI, LLMBatchAPI
from evaluator.cohort import cohort_view, order_trend, run_cohort, similarity
from evaluator.scoring.config import load_config

from .conftest import CONFIGS, zip_dir
from .fakes import FakeGrader, ScriptedLLM

A20 = {"ground_truth": 3, "agreement_analysis": 2.5, "deployment_recommendation": 3,
       "professional_baseline": 2, "ai_collaboration": 1}


def _zips(sample_repo, tmp_path, n=3):
    zips = []
    for i in range(n):
        (sample_repo / "NOTE.md").write_text(f"# Note {i}\n")
        zips.append(zip_dir(sample_repo, tmp_path / f"cand{i}.zip"))
    return zips


def test_batch_matches_live_and_is_half_price(sample_repo, tmp_path):
    a20 = load_config(CONFIGS / "a20.yaml")
    zips = _zips(sample_repo, tmp_path)
    live = asyncio.run(run_cohort(zips, a20, tmp_path / "live", llm=ScriptedLLM(FakeGrader(a20, A20))))
    api = LLMBatchAPI(ScriptedLLM(FakeGrader(a20, A20)))
    batch = asyncio.run(run_cohort(zips, a20, tmp_path / "batch", batch_api=api))
    assert [r["final"] for r in live["ranking"]] == [r["final"] for r in batch["ranking"]] == [62.5] * 3
    # pre (1 round) + criteria (2 turns) + summary (1); no reruns (no bands, no judgment caps).
    assert len(api.batches) == 4 and api.batches[1] == 15  # 3 submissions x 5 criteria per round
    assert batch["cost_usd"] == pytest.approx(live["cost_usd"] / 2, rel=1e-6)
    log = (tmp_path / "batch" / "batches.jsonl").read_text().splitlines()
    assert [json.loads(x)["label"] for x in log] == ["pre", "criteria", "criteria", "summary"]
    # Rerunning the cohort reuses every cached report: no new batches.
    api2 = LLMBatchAPI(ScriptedLLM(FakeGrader(a20, A20)))
    asyncio.run(run_cohort(zips, a20, tmp_path / "batch", batch_api=api2))
    assert api2.batches == []


def test_batch_reruns_and_not_graded(sample_repo, tmp_path):
    b10 = load_config(CONFIGS / "b10.yaml")
    zips = _zips(sample_repo, tmp_path, n=2)
    ratings = {"decision_framing": 3, "architecture_reconstruction": 3, "experimental_rigor": 2.5,
               "production_reasoning": 3, "evidence_quality": 2, "synthesis": 3, "ai_use_transparency": 4,
               "communication": 1.5}
    api = LLMBatchAPI(ScriptedLLM(FakeGrader(b10, ratings, refuse={"synthesis"})))
    view = asyncio.run(run_cohort(zips, b10, tmp_path / "b", batch_api=api))
    assert view["graded"] == 0 and len(view["not_graded"]) == 2
    assert all("synthesis" in why for why in view["not_graded"].values())

    api = LLMBatchAPI(ScriptedLLM(FakeGrader(b10, ratings)))
    view = asyncio.run(run_cohort(zips, b10, tmp_path / "b2", batch_api=api))
    rep = json.loads(next((tmp_path / "b2" / "submissions").glob("*/grades/*/report.json")).read_text())
    runs = {s["criterion"]: len(s["runs"]) for s in rep["sections"]}
    assert runs["architecture_reconstruction"] == 3 and runs["experimental_rigor"] == 3 and runs["synthesis"] == 1
    assert view["ranking"][0]["final"] == 68.13


def test_anthropic_batch_api_contract():
    calls = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["requests"][0]["custom_id"] == "s0-c0-r0-0"
            return httpx2.Response(200, json=_batch("in_progress"))
        if req.url.path.endswith("/results"):
            lines = [
                {"custom_id": "s0-c0-r0-0", "result": {"type": "succeeded", "message": {
                    "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                    "stop_reason": "end_turn", "stop_sequence": None, "content": [{"type": "text", "text": "{}"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1}}}},
                {"custom_id": "s1-c0-r0-0", "result": {"type": "errored", "error": {
                    "type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}}},
                {"custom_id": "s2-c0-r0-0", "result": {"type": "expired"}},
            ]
            return httpx2.Response(200, content="\n".join(json.dumps(x) for x in lines).encode(),
                                   headers={"content-type": "application/binary"})
        return httpx2.Response(200, json=_batch("ended"))

    client = anthropic.AsyncAnthropic(api_key="t", http_client=anthropic.DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(handler)))
    api = AnthropicBatchAPI(client, poll_seconds=0)

    async def go():
        bid = await api.submit([("s0-c0-r0-0", {"model": "claude-sonnet-5-5", "max_tokens": 10,
                                                "messages": [{"role": "user", "content": "x"}]})])
        return bid, await api.results(bid)

    bid, res = asyncio.run(go())
    assert bid == "msgbatch_1"
    assert res["s0-c0-r0-0"][0] == "succeeded" and res["s0-c0-r0-0"][1]["stop_reason"] == "end_turn"
    assert res["s1-c0-r0-0"][0] == "errored" and res["s2-c0-r0-0"][0] == "expired"
    assert ("POST", "/v1/messages/batches") in calls


def _batch(status):
    return {"id": "msgbatch_1", "type": "message_batch", "processing_status": status,
            "request_counts": {"processing": 0, "succeeded": 1, "errored": 1, "canceled": 0, "expired": 1},
            "created_at": "2026-10-23T00:00:00Z", "expires_at": "2026-10-24T00:00:00Z", "ended_at": None,
            "archived_at": None, "cancel_initiated_at": None,
            "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results" if status == "ended" else None}


def test_similarity_flags_copies_and_ignores_template(sample_repo, tmp_path):
    from evaluator.pipeline import run_intake
    a20 = load_config(CONFIGS / "a20.yaml")
    long_code = "\n".join(f"def step_{i}(x):\n    return x * {i} + helper_{i}(x)\n" for i in range(40))
    (sample_repo / "pipeline.py").write_text(long_code)
    za = zip_dir(sample_repo, tmp_path / "a.zip")
    zb = zip_dir(sample_repo, tmp_path / "b.zip")
    (sample_repo / "pipeline.py").write_text("\n".join(f"class Thing{i}:\n    value = '{i * 7}'\n" for i in range(60)))
    zc = zip_dir(sample_repo, tmp_path / "c.zip")
    dirs = {}
    for name, z in (("a", za), ("b", zb), ("c", zc)):
        run_intake(z, a20, tmp_path / name)
        dirs[name] = tmp_path / name
    m = similarity(dirs, 0.8)
    pairs = {(x["a"], x["b"], x["kind"]) for x in m}
    assert ("a", "b", "code") in pairs and ("a", "c", "code") not in pairs
    # Starter code shared by everyone is not evidence of copying.
    assert not [x for x in similarity(dirs, 0.8, template=tmp_path / "a") if x["kind"] == "code" and x["b"] == "b"]


def test_order_trend():
    assert order_trend(list(range(30)))["flag"] is True
    flat = [50, 70, 60, 65, 55, 75, 58, 62, 68, 52, 71, 59] * 2
    assert order_trend(flat)["flag"] is False
    assert order_trend([1, 2])["rho"] is None


def _report(final, pts, caps=()):
    return {"status": "graded", "submission_sha256": "x" * 64, "score": {
        "final": final, "total": final, "band": "Pass", "binding_cap": None,
        "caps": [{"cap_id": c, "status": "applied"} for c in caps]},
        "confidence": {"level": "High"}, "cost": {"total_usd": 0.5},
        "integrity": {"forbidden_files": [], "secret_matches": [], "placeholders": [1], "leftover_agent_text": []},
        "sections": [{"criterion": c, "points": p, "rating": p / 5, "assessment": {"elements": [
            {"id": "e", "status": "missing"}]}} for c, p in pts.items()]}


def test_cohort_view_ranking_and_ties():
    a20 = load_config(CONFIGS / "a20.yaml")
    base = {"ground_truth": 10, "agreement_analysis": 20, "deployment_recommendation": 10,
            "professional_baseline": 10, "ai_collaboration": 10}
    reports = {
        "x": _report(60, base),
        "y": _report(60, {**base, "agreement_analysis": 15, "ground_truth": 15}),  # same total, less on heaviest
        "z": _report(70, base, caps=("no_ledger",)),
        "w": _report(60, base),
        "n": {"status": "not_graded", "reason": "empty"},
    }
    v = cohort_view(reports, a20, ["x", "y", "z", "w", "n"], [])
    assert [r["submission"] for r in v["ranking"]] == ["z", "w", "x", "y"]
    assert v["ranking"][1]["tied_with"] == ["x"] and v["ranking"][3]["tied_with"] == []
    assert v["ranking"][0]["percentile"] == 87.5 and v["ranking"][0]["caps"] == ["no_ledger"]
    assert v["not_graded"] == {"n": "empty"} and v["hygiene"] == {"placeholders": 4}
    assert v["cost_usd"] == 2.0

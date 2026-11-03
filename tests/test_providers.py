"""OpenAI adapter and provider routing: conversion both ways, real SDK over a mock transport."""

import asyncio
import json

import httpx2
import openai
import pytest

from evaluator.batch import OpenAIBatchAPI, RouterBatchAPI
from evaluator.intake.snapshot import Snapshot
from evaluator.judge.runner import run_session
from evaluator.judge.session import CriterionSession, JudgeContext
from evaluator.pipeline import run_intake
from evaluator.providers import (RouterLLM, OpenAILLM, from_openai_response, provider_of, supports_reasoning,
                                 to_openai_params)
from evaluator.scoring.config import load_config

from .conftest import CONFIGS

MODEL = "gpt-5.5-2026-04-23"


def test_provider_of_and_reasoning():
    assert provider_of("claude-sonnet-5-5") == "anthropic" and provider_of(MODEL) == "openai"
    assert supports_reasoning("gpt-5.5-2026-04-23") and supports_reasoning("o4-mini")
    assert not supports_reasoning("gpt-4.1") and not supports_reasoning("gpt-5-chat-latest")


def test_request_conversion():
    params = {
        "model": MODEL, "max_tokens": 16000,
        "system": [{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": "B", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "search", "description": "d", "strict": True, "input_schema": {"type": "object"}}],
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "search", "input": {"pattern": "x"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "No matches", "is_error": False}]},
            {"role": "assistant", "content": [
                {"type": "openai_output", "items": [{"type": "reasoning", "id": "rs_1", "encrypted_content": "enc"},
                                                     {"type": "function_call", "call_id": "c2", "name": "search", "arguments": "{}"}]},
                {"type": "tool_use", "id": "c2", "name": "search", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c2", "content": "1 match", "is_error": False}]},
        ],
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": {"type": "object"}}},
        "cache_control": {"type": "ephemeral"},
    }
    out = to_openai_params(params)
    assert out["instructions"] == "A\n\nB" and out["store"] is False and out["max_output_tokens"] == 16000
    items = out["input"]
    assert items[0] == {"role": "user", "content": "go"}
    assert items[1] == {"type": "function_call", "call_id": "c1", "name": "search", "arguments": '{"pattern": "x"}'}
    assert items[2] == {"type": "function_call_output", "call_id": "c1", "output": "No matches"}
    # Raw output items (encrypted reasoning first) go back verbatim and in order.
    assert items[3] == {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc"} and items[4]["call_id"] == "c2"
    assert len(items) == 6 and items[5]["type"] == "function_call_output"
    assert out["tools"][0] == {"type": "function", "name": "search", "description": "d",
                               "parameters": {"type": "object"}, "strict": True}
    assert out["text"]["format"]["strict"] is True and out["reasoning"] == {"effort": "medium"}
    assert out["include"] == ["reasoning.encrypted_content"]
    assert "cache_control" not in json.dumps(out) and "temperature" not in out
    plain = to_openai_params({**params, "model": "gpt-4.1"})
    assert "reasoning" not in plain and "include" not in plain


def test_response_conversion():
    output = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "enc", "summary": []},
              {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "search", "arguments": '{"pattern": "y"}'},
              {"type": "function_call", "id": "fc_2", "call_id": "c2", "name": "view_file", "arguments": "{bad"}]
    msg = from_openai_response({"id": "resp_1", "model": MODEL, "status": "completed", "output": output,
                                "usage": {"input_tokens": 1000, "output_tokens": 50,
                                          "input_tokens_details": {"cached_tokens": 800}}})
    assert msg["stop_reason"] == "tool_use"
    assert msg["content"][0] == {"type": "openai_output", "items": output}
    assert msg["content"][1] == {"type": "tool_use", "id": "c1", "name": "search", "input": {"pattern": "y"}}
    assert "__invalid_json__" in msg["content"][2]["input"]
    assert msg["usage"] == {"input_tokens": 200, "output_tokens": 50, "cache_read_input_tokens": 800,
                            "cache_creation_input_tokens": 0}

    def resp(status="completed", reason=None, content=({"type": "output_text", "text": "{}"},)):
        return {"status": status, "incomplete_details": {"reason": reason} if reason else None,
                "output": [{"type": "message", "role": "assistant", "content": list(content)}]}

    assert from_openai_response(resp())["stop_reason"] == "end_turn"
    assert from_openai_response(resp())["content"][1] == {"type": "text", "text": "{}"}
    assert from_openai_response(resp("incomplete", "max_output_tokens"))["stop_reason"] == "max_tokens"
    assert from_openai_response(resp("incomplete", "content_filter"))["stop_reason"] == "refusal"
    assert from_openai_response(resp(content=[{"type": "refusal", "refusal": "no"}]))["stop_reason"] == "refusal"


def _response(output, status="completed"):
    return {"id": "resp_1", "object": "response", "created_at": 0, "model": MODEL, "status": status,
            "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": 3000, "output_tokens": 400, "total_tokens": 3400,
                      "input_tokens_details": {"cached_tokens": 2048}, "output_tokens_details": {"reasoning_tokens": 300}}}


def _text_item(text):
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


@pytest.fixture
def ctx(sample_zip, tmp_path):
    cfg = load_config(CONFIGS / "a20.yaml", "openai")
    out = tmp_path / "run"
    assert run_intake(sample_zip, cfg, out).status == "ok"
    return JudgeContext(config=cfg, snap=Snapshot.load(out / "snapshot"),
                        facts=json.loads((out / "fact_sheet.json").read_text()),
                        repo_map=json.loads((out / "repo_map.json").read_text()))


def test_full_judge_session_through_openai_sdk(ctx):
    bodies = []
    final = {
        "elements": [
            {"id": "written_rubric", "status": "missing", "evidence": [], "search_ids": ["t1"], "note": ""},
            {"id": "labels_before_outputs", "status": "found", "search_ids": [], "note": "",
             "evidence": [{"file": "fact_sheet", "start_line": 0, "end_line": 0, "quote": "git.file_first_added"}]},
            {"id": "borderline_documented", "status": "missing", "evidence": [], "search_ids": ["t1"], "note": ""},
            {"id": "both_classes", "status": "partial", "search_ids": [], "note": "",
             "evidence": [{"file": "hand_labels.csv", "start_line": 30, "end_line": 31, "quote": "INCORRECT"}]},
        ],
        "rating": 1.5, "rationale": "r", "what_worked": [], "what_to_improve": [], "defects": [], "unverified": [],
        "findings": [], "grader_directed_text": [],
    }

    def handler(req: httpx2.Request) -> httpx2.Response:
        body = json.loads(req.content)
        bodies.append(body)
        assert req.url.path == "/v1/responses"
        if len(bodies) == 1:
            return httpx2.Response(200, json=_response([
                {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"},
                {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "search", "status": "completed",
                 "arguments": json.dumps({"pattern": "rubric", "regex": False, "path_glob": None})}]))
        return httpx2.Response(200, json=_response([_text_item(json.dumps(final))]))

    client = openai.AsyncOpenAI(api_key="t", http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    llm = RouterLLM(openai=OpenAILLM(client=client))
    session = CriterionSession(ctx, ctx.config.criterion("ground_truth"))
    res = asyncio.run(run_session(llm, session))
    assert res.status == "ok" and res.rating == 1.5 and res.model == MODEL
    assert bodies[0]["model"] == MODEL and bodies[0]["reasoning"] == {"effort": "medium"}
    assert bodies[0]["text"]["format"]["type"] == "json_schema" and bodies[0]["store"] is False
    second = bodies[1]["input"]
    assert second[1]["type"] == "reasoning" and second[1]["encrypted_content"] == "ENC"
    assert second[2]["type"] == "function_call" and second[2]["call_id"] == "call_1"
    assert second[3]["type"] == "function_call_output" and "No matches" in second[3]["output"]
    assert session.usage.records[0].cache_read_input_tokens == 2048


def test_openai_batch_api_contract():
    calls = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        calls.append((req.method, req.url.path))
        path = req.url.path
        if path == "/v1/files" and req.method == "POST":
            assert b'"url": "/v1/responses"' in req.content
            return httpx2.Response(200, json={"id": "file-in", "object": "file", "bytes": 1, "created_at": 0,
                                               "filename": "batch.jsonl", "purpose": "batch", "status": "processed"})
        if path == "/v1/batches" and req.method == "POST":
            return httpx2.Response(200, json=_batch("validating"))
        if path.startswith("/v1/batches/"):
            return httpx2.Response(200, json=_batch("completed"))
        if path == "/v1/files/file-out/content":
            rows = [{"custom_id": "a", "response": {"status_code": 200, "body": _response([_text_item("{}")])}},
                    {"custom_id": "b", "response": {"status_code": 400, "body": {"error": {"message": "bad"}}}}]
            return httpx2.Response(200, content="\n".join(json.dumps(r) for r in rows).encode())
        raise AssertionError(path)

    client = openai.AsyncOpenAI(api_key="t", http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    router = RouterBatchAPI(openai=OpenAIBatchAPI(client, poll_seconds=0))
    params = {"model": MODEL, "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}

    async def go():
        bid = await router.submit([("a", params), ("b", params)])
        return await router.results(bid)

    res = asyncio.run(go())
    assert res["a"][0] == "succeeded" and res["a"][1]["stop_reason"] == "end_turn"
    assert res["b"] == ("errored", None, "invalid_request")


def _batch(status):
    return {"id": "batch_1", "object": "batch", "endpoint": "/v1/responses", "input_file_id": "file-in",
            "completion_window": "24h", "status": status, "created_at": 0,
            "output_file_id": "file-out" if status == "completed" else None, "error_file_id": None}


def test_rate_limit_is_retried_not_failed(monkeypatch):
    from evaluator.judge import runner
    from evaluator.judge.structured import StructuredCall

    monkeypatch.setattr(runner.asyncio, "sleep", _no_sleep)

    class RateLimited(Exception):
        status_code = 429

    calls = {"n": 0}

    class Flaky:
        async def create(self, params):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise RateLimited("Rate limit reached ... Please try again in 3.951s.")
            return {"model": MODEL, "stop_reason": "end_turn", "usage": {},
                    "content": [{"type": "text", "text": '{"ok": true}'}]}

    call = StructuredCall("t", MODEL, "s", "u", {"type": "object"})
    res = asyncio.run(runner.run_session(Flaky(), call))
    assert res.status == "ok" and res.attempts == 1 and calls["n"] == 4
    assert runner._rate_limit_delay(RateLimited("Please try again in 3.951s."), 0) == pytest.approx(4.451)
    assert runner._rate_limit_delay(RateLimited("Please try again in 250ms."), 0) == pytest.approx(0.75)


async def _no_sleep(_s):
    return None


def test_token_rate_limiter_waits_for_room():
    from evaluator.llm import TokenRateLimiter

    async def go():
        lim = TokenRateLimiter(1000)
        await lim.acquire(600)
        await lim.acquire(400)  # exactly fills the window
        assert sum(t for _, t in lim._events) == 1000
        t = asyncio.create_task(lim.acquire(100))
        await asyncio.sleep(0.05)
        assert not t.done()  # must wait for the window to roll over
        t.cancel()

    asyncio.run(go())
    assert TokenRateLimiter.estimate({"max_tokens": 16000, "messages": "x" * 4000}) > 17000

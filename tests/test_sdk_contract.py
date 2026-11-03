"""The real SDK accepts our request bodies and our wrapper parses its responses (no network)."""

import asyncio
import json

import anthropic
import httpx2

from evaluator.llm import AnthropicLLM


def test_real_sdk_roundtrip_with_mock_transport():
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx2.Response(200, headers={"request-id": "req_test"}, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 0},
            "content": [{"type": "thinking", "thinking": "", "signature": "abc"},
                        {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"pattern": "x"}}],
        })

    client = anthropic.AsyncAnthropic(api_key="test", http_client=anthropic.DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(handler)))
    llm = AnthropicLLM(client=client)
    params = {
        "model": "claude-sonnet-5-5", "max_tokens": 16000,
        "system": [{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "search", "description": "d", "strict": True, "input_schema": {
            "type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"],
            "additionalProperties": False}}],
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": {"type": "object"}}},
        "cache_control": {"type": "ephemeral"},
    }
    msg = asyncio.run(llm.create(params))
    body = captured["body"]
    assert captured["url"].endswith("/v1/messages")
    assert body["output_config"]["format"]["type"] == "json_schema" and body["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["strict"] is True
    assert msg["stop_reason"] == "tool_use" and msg["usage"]["cache_read_input_tokens"] == 100
    assert msg["_request_id"] == "req_test"
    # Content round-trips back into a follow-up request unchanged.
    follow = {**params, "messages": params["messages"] + [{"role": "assistant", "content": msg["content"]}]}
    asyncio.run(llm.create(follow))
    assert captured["body"]["messages"][1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "abc"}

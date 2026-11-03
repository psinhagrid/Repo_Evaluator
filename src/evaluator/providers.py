"""Provider routing: Claude models go to Anthropic, everything else to OpenAI.

Internally every stage speaks one request/response shape (Messages-API style:
system blocks, content blocks, tool_use / tool_result, stop_reason, usage).
The OpenAI adapter translates that shape to the Responses API and back at the
edge, so sessions, verification, caching and batching are provider-neutral.

Which model handles which criterion is still a fixed table in the config,
never a live decision, so every candidate in a cohort gets the same model.
"""

from __future__ import annotations

import json

from .llm import LLM, AnthropicLLM, TokenRateLimiter

OPENAI = "openai"
ANTHROPIC = "anthropic"


def provider_of(model: str) -> str:
    return ANTHROPIC if model.startswith("claude-") else OPENAI


# -- request: internal -> Responses API ------------------------------------------------
#
# Responses API, not Chat Completions: Chat Completions rejects function tools together with
# reasoning effort on current models. Requests use store=False (OpenAI keeps no conversation
# state) and ask for encrypted reasoning, which is passed back verbatim on the next turn.

OPENAI_ITEMS = "openai_output"  # internal block holding a response's raw output items


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def to_openai_params(params: dict) -> dict:
    items: list[dict] = []
    for m in params["messages"]:
        content = m["content"]
        if m["role"] == "assistant":
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            raw = next((b for b in blocks if b.get("type") == OPENAI_ITEMS), None)
            if raw is not None:
                items.extend(raw["items"])  # reasoning + function calls + message, in the original order
                continue
            text = _text(blocks)
            if text:
                items.append({"role": "assistant", "content": text})
            for b in blocks:
                if b.get("type") == "tool_use":
                    items.append({"type": "function_call", "call_id": b["id"], "name": b["name"],
                                  "arguments": json.dumps(b.get("input") or {})})
        elif m["role"] == "user":
            if isinstance(content, str):
                items.append({"role": "user", "content": content})
                continue
            for b in content:
                if b.get("type") == "tool_result":
                    body = b["content"] if isinstance(b["content"], str) else _text(b["content"])
                    items.append({"type": "function_call_output", "call_id": b["tool_use_id"], "output": body})
            text = _text(content)
            if text:
                items.append({"role": "user", "content": text})
        else:
            raise ValueError(f"unsupported role {m['role']!r}")

    out = {"model": params["model"], "input": items, "max_output_tokens": params["max_tokens"], "store": False}
    system = params.get("system")
    if system:
        # Same text in the same order every time: OpenAI caches stable prefixes automatically.
        out["instructions"] = _text(system)
    if params.get("tools"):
        out["tools"] = [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                         "parameters": t["input_schema"], "strict": bool(t.get("strict", True))}
                        for t in params["tools"]]
    oc = params.get("output_config") or {}
    fmt = oc.get("format")
    if fmt and fmt.get("type") == "json_schema":
        out["text"] = {"format": {"type": "json_schema", "name": "result", "schema": fmt["schema"], "strict": True}}
    if supports_reasoning(params["model"]):
        if oc.get("effort"):
            out["reasoning"] = {"effort": oc["effort"]}
        out["include"] = ["reasoning.encrypted_content"]
    return out


def supports_reasoning(model: str) -> bool:
    """Reasoning parameters are rejected by non-reasoning models such as gpt-4.1 and *-chat-latest."""
    m = model.lower()
    if "chat" in m or m.startswith(("gpt-4", "gpt-3")):
        return False
    return m.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


# -- response: Responses API -> internal ----------------------------------------------------

def from_openai_response(resp: dict) -> dict:
    output = resp.get("output") or []
    texts, tool_uses, refused = [], [], False
    for item in output:
        kind = item.get("type")
        if kind == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
                elif c.get("type") == "refusal":
                    refused = True
        elif kind == "function_call":
            raw = item.get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"__invalid_json__": raw[:500]}  # the tool box reports bad arguments back to the model
            tool_uses.append({"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": args})

    content = [{"type": OPENAI_ITEMS, "items": output}]
    if texts:
        content.append({"type": "text", "text": "".join(texts)})
    content.extend(tool_uses)

    incomplete = (resp.get("incomplete_details") or {}).get("reason")
    if refused or incomplete == "content_filter":
        stop = "refusal"
    elif resp.get("status") == "incomplete" and incomplete == "max_output_tokens":
        stop = "max_tokens"
    elif tool_uses:
        stop = "tool_use"
    else:
        stop = "end_turn"
    u = resp.get("usage") or {}
    cached = ((u.get("input_tokens_details") or {}).get("cached_tokens")) or 0
    return {
        "id": resp.get("id"), "model": resp.get("model"), "role": "assistant", "content": content,
        "stop_reason": stop,
        "usage": {"input_tokens": (u.get("input_tokens") or 0) - cached, "output_tokens": u.get("output_tokens") or 0,
                  "cache_read_input_tokens": cached, "cache_creation_input_tokens": 0},
    }


class OpenAILLM:
    def __init__(self, client=None, max_retries: int = 4, timeout: float = 600.0):
        import openai

        self.client = client or openai.AsyncOpenAI(max_retries=max_retries, timeout=timeout)

    async def create(self, params: dict) -> dict:
        resp = await self.client.responses.create(**to_openai_params(params))
        out = from_openai_response(resp.model_dump(exclude_none=True))
        out["_request_id"] = getattr(resp, "_request_id", None)
        return out


class RouterLLM:
    """Sends each request to the provider that serves its model. Clients are created on first use."""

    def __init__(self, anthropic: LLM | None = None, openai: LLM | None = None,
                 tokens_per_minute: dict[str, int] | None = None):
        self._clients: dict[str, LLM | None] = {ANTHROPIC: anthropic, OPENAI: openai}
        if tokens_per_minute is None:
            tokens_per_minute = default_tpm()
        self._limits = {p: TokenRateLimiter(n) for p, n in tokens_per_minute.items() if n > 0}

    def _client(self, provider: str) -> LLM:
        if self._clients[provider] is None:
            self._clients[provider] = AnthropicLLM() if provider == ANTHROPIC else OpenAILLM()
        return self._clients[provider]

    async def create(self, params: dict) -> dict:
        provider = provider_of(params["model"])
        limiter = self._limits.get(provider)
        if limiter is not None:
            await limiter.acquire(limiter.estimate(params))
        return await self._client(provider).create(params)


def default_tpm() -> dict[str, int]:
    """Tokens-per-minute budgets: EVALUATOR_OPENAI_TPM / EVALUATOR_ANTHROPIC_TPM, 0 to disable.

    OpenAI defaults to 900k (a common gpt-5.5 tier limit is 1M); Anthropic is unthrottled by default.
    """
    import os

    def read(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except ValueError:
            return default

    return {OPENAI: read("EVALUATOR_OPENAI_TPM", 900_000), ANTHROPIC: read("EVALUATOR_ANTHROPIC_TPM", 0)}

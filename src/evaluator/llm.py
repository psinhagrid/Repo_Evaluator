"""Model access: one small interface, a real Anthropic client, and usage accounting.

Everything above this module works with plain dicts (request params in,
message dict out), so tests can script responses and the batch driver can
reuse the exact same request bodies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

# USD per million tokens (standard API price). Cache writes are 1.25x input.
# Haiku 5.5 rises to $0.50 / $2.50 above 100k prompt tokens.
PRICES = {
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "long_in": 0.50, "long_out": 2.50},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20},
}
HAIKU_LONG_PROMPT = 100_000
BATCH_DISCOUNT = 0.5


def register_prices(prices: dict[str, dict]) -> None:
    """Add per-model prices (USD per million tokens: in, out, cache_read) from the config."""
    for model, p in prices.items():
        missing = {"in", "out"} - set(p)
        if missing:
            raise ValueError(f"prices.{model}: missing {sorted(missing)}")
        PRICES[model] = {"cache_read": p.get("cache_read", p["in"]), **p}


class LLM(Protocol):
    async def create(self, params: dict) -> dict: ...


class AnthropicLLM:
    """Thin async wrapper over the official SDK. Credentials come from the environment."""

    def __init__(self, client=None, max_retries: int = 4, timeout: float = 600.0):
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.AsyncAnthropic(max_retries=max_retries, timeout=timeout)

    async def create(self, params: dict) -> dict:
        msg = await self.client.messages.create(**params)
        out = msg.to_dict()
        out["_request_id"] = getattr(msg, "_request_id", None)
        return out


@dataclass
class UsageRecord:
    purpose: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    batch: bool = False

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    def cost(self) -> float:
        p = PRICES.get(self.model)
        if p is None:
            return 0.0
        long = "long_in" in p and self.prompt_tokens > HAIKU_LONG_PROMPT
        rate_in = p["long_in"] if long else p["in"]
        rate_out = p["long_out"] if long else p["out"]
        usd = (self.input_tokens * rate_in + self.cache_creation_input_tokens * rate_in * 1.25
               + self.cache_read_input_tokens * p["cache_read"] + self.output_tokens * rate_out) / 1e6
        return usd * (BATCH_DISCOUNT if self.batch else 1.0)


@dataclass
class UsageLog:
    records: list[UsageRecord] = field(default_factory=list)

    def add(self, purpose: str, message: dict, batch: bool = False) -> None:
        u = message.get("usage") or {}
        self.records.append(UsageRecord(
            purpose=purpose, model=message.get("model", ""),
            input_tokens=u.get("input_tokens") or 0, output_tokens=u.get("output_tokens") or 0,
            cache_read_input_tokens=u.get("cache_read_input_tokens") or 0,
            cache_creation_input_tokens=u.get("cache_creation_input_tokens") or 0, batch=batch,
        ))

    def extend(self, other: "UsageLog") -> None:
        self.records.extend(other.records)

    def total_cost(self) -> float:
        return round(sum(r.cost() for r in self.records), 4)

    def summary(self) -> dict:
        by: dict[str, dict] = {}
        for r in self.records:
            s = by.setdefault(r.purpose, {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "usd": 0.0})
            s["calls"] += 1
            s["input"] += r.input_tokens
            s["output"] += r.output_tokens
            s["cache_read"] += r.cache_read_input_tokens
            s["cache_write"] += r.cache_creation_input_tokens
            s["usd"] = round(s["usd"] + r.cost(), 5)
        unpriced = sorted({r.model for r in self.records if r.model not in PRICES})
        return {"total_usd": self.total_cost(), "by_purpose": by, "unpriced_models": unpriced}

    def to_list(self) -> list[dict]:
        return [asdict(r) for r in self.records]


def text_of(message: dict) -> str:
    return "".join(b.get("text", "") for b in message.get("content", []) if b.get("type") == "text")


class TokenRateLimiter:
    """Client-side tokens-per-minute budget, so parallel criteria stay under a provider's TPM limit.

    Each request reserves its estimated size (prompt characters / 4 plus max_tokens, which is how
    providers count requested tokens) in a sliding 60-second window and waits for room if needed.
    """

    WINDOW = 60.0

    def __init__(self, tokens_per_minute: int):
        import asyncio
        from collections import deque

        self.tpm = tokens_per_minute
        self._events: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    @staticmethod
    def estimate(params: dict) -> int:
        import json

        return len(json.dumps(params, default=str)) // 4 + int(params.get("max_tokens") or 0)

    async def acquire(self, tokens: int) -> None:
        import asyncio
        import time

        tokens = min(tokens, self.tpm)  # a request larger than the whole budget still goes, alone
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._events and now - self._events[0][0] >= self.WINDOW:
                    self._events.popleft()
                used = sum(t for _, t in self._events)
                if used + tokens <= self.tpm:
                    self._events.append((now, tokens))
                    return
                await asyncio.sleep(max(0.2, self.WINDOW - (now - self._events[0][0])))

"""Batch driver: advance many sessions together, one Message Batch per round.

Each session (criterion judge or structured call) exposes next_request() /
consume() / api_error(). A round collects one pending request from every
active session, submits them as one batch, waits, and feeds each result back
by custom_id. Tool loops therefore take one round per turn, at batch prices.

Completed criterion results are cached on disk by SubmissionGrader, so if the
process stops, a rerun resumes from finished criteria (in-flight
conversations restart).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Protocol

POLL_SECONDS = 60
MAX_BATCH_REQUESTS = 100_000


class BatchAPI(Protocol):
    async def submit(self, requests: list[tuple[str, dict]]) -> str: ...
    async def results(self, batch_id: str) -> dict[str, tuple[str, dict | None, str | None]]: ...


class AnthropicBatchAPI:
    """Message Batches API: create, poll until ended, stream results (keyed by custom_id)."""

    def __init__(self, client=None, poll_seconds: int = POLL_SECONDS):
        import anthropic

        self.client = client or anthropic.AsyncAnthropic()
        self.poll_seconds = poll_seconds

    async def submit(self, requests: list[tuple[str, dict]]) -> str:
        batch = await self.client.messages.batches.create(
            requests=[{"custom_id": cid, "params": params} for cid, params in requests])
        return batch.id

    async def results(self, batch_id: str) -> dict[str, tuple[str, dict | None, str | None]]:
        while True:
            batch = await self.client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                break
            await asyncio.sleep(self.poll_seconds)
        out = {}
        async for r in await self.client.messages.batches.results(batch_id):
            kind = r.result.type
            if kind == "succeeded":
                out[r.custom_id] = ("succeeded", r.result.message.to_dict(), None)
            elif kind == "errored":
                out[r.custom_id] = ("errored", None, r.result.error.type if r.result.error else "error")
            else:  # canceled | expired
                out[r.custom_id] = (kind, None, kind)
        return out


class LLMBatchAPI:
    """Runs a 'batch' through a live LLM (tests and small dry runs): same interface, no discount."""

    def __init__(self, llm, concurrency: int = 10):
        self.llm, self.sem = llm, asyncio.Semaphore(concurrency)
        self._pending: dict[str, list[tuple[str, dict]]] = {}
        self.batches: list[int] = []

    async def submit(self, requests):
        bid = f"batch_{len(self.batches) + 1}"
        self.batches.append(len(requests))
        self._pending[bid] = requests
        return bid

    async def results(self, batch_id):
        async def one(cid, params):
            async with self.sem:
                try:
                    return cid, ("succeeded", await self.llm.create(params), None)
                except Exception as e:
                    status = getattr(e, "status_code", None)
                    return cid, ("errored", None, "invalid_request" if status == 400 else f"{type(e).__name__}")

        pairs = await asyncio.gather(*(one(c, p) for c, p in self._pending.pop(batch_id)))
        return dict(pairs)


async def drive_rounds(api: BatchAPI, sessions: dict[str, object], log_path: Path | None = None,
                       label: str = "round") -> int:
    """Run every session to completion. `sessions` maps a stable custom_id prefix to a session."""
    rounds = 0
    while True:
        pending = []
        for key, s in sessions.items():
            params = s.next_request()
            if params is not None:
                pending.append((f"{key}-{rounds}", key, params))
        if not pending:
            return rounds
        rounds += 1
        for chunk_start in range(0, len(pending), MAX_BATCH_REQUESTS):
            chunk = pending[chunk_start:chunk_start + MAX_BATCH_REQUESTS]
            started = time.time()
            bid = await api.submit([(cid, params) for cid, _, params in chunk])
            results = await api.results(bid)
            if log_path:
                with log_path.open("a") as f:
                    f.write(json.dumps({"label": label, "round": rounds, "batch_id": bid, "requests": len(chunk),
                                        "seconds": round(time.time() - started, 1)}) + "\n")
            for cid, key, _ in chunk:
                s = sessions[key]
                kind, message, error = results.get(cid, ("errored", None, "missing result"))
                if kind == "succeeded":
                    s.consume(message, batch=True)
                else:
                    s.api_error(f"batch {kind}: {error}", 400 if error == "invalid_request" else None)


class OpenAIBatchAPI:
    """OpenAI Batch API: upload a JSONL of Responses API requests, poll, download the output file."""

    TERMINAL = {"completed", "failed", "expired", "cancelled"}

    def __init__(self, client=None, poll_seconds: int = POLL_SECONDS):
        import openai

        self.client = client or openai.AsyncOpenAI()
        self.poll_seconds = poll_seconds

    async def submit(self, requests: list[tuple[str, dict]]) -> str:
        from .providers import to_openai_params

        lines = [json.dumps({"custom_id": cid, "method": "POST", "url": "/v1/responses",
                             "body": to_openai_params(params)}) for cid, params in requests]
        f = await self.client.files.create(file=("batch.jsonl", "\n".join(lines).encode()), purpose="batch")
        batch = await self.client.batches.create(input_file_id=f.id, endpoint="/v1/responses",
                                                 completion_window="24h")
        return batch.id

    async def results(self, batch_id: str) -> dict[str, tuple[str, dict | None, str | None]]:
        from .providers import from_openai_response

        while True:
            batch = await self.client.batches.retrieve(batch_id)
            if batch.status in self.TERMINAL:
                break
            await asyncio.sleep(self.poll_seconds)
        out: dict[str, tuple[str, dict | None, str | None]] = {}
        for file_id in (batch.output_file_id, batch.error_file_id):
            if not file_id:
                continue
            text = (await self.client.files.content(file_id)).text
            for line in text.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                resp = row.get("response") or {}
                if resp.get("status_code") == 200:
                    out[row["custom_id"]] = ("succeeded", from_openai_response(resp["body"]), None)
                else:
                    code = resp.get("status_code")
                    kind = "invalid_request" if code == 400 else (row.get("error") or {}).get("code") or f"http_{code}"
                    out[row["custom_id"]] = ("errored", None, kind)
        # Requests in neither file (batch failed or expired) come back as "missing result" in drive_rounds.
        return out


class RouterBatchAPI:
    """Splits each round by provider (model prefix) and merges the results."""

    def __init__(self, anthropic: BatchAPI | None = None, openai: BatchAPI | None = None):
        self._apis = {"anthropic": anthropic, "openai": openai}
        self._parts: dict[str, list[tuple[str, str]]] = {}

    def _api(self, provider: str) -> BatchAPI:
        if self._apis[provider] is None:
            self._apis[provider] = AnthropicBatchAPI() if provider == "anthropic" else OpenAIBatchAPI()
        return self._apis[provider]

    async def submit(self, requests: list[tuple[str, dict]]) -> str:
        from .providers import provider_of

        groups: dict[str, list[tuple[str, dict]]] = {}
        for cid, params in requests:
            groups.setdefault(provider_of(params["model"]), []).append((cid, params))
        parts = [(prov, await self._api(prov).submit(reqs)) for prov, reqs in sorted(groups.items())]
        key = "+".join(bid for _, bid in parts)
        self._parts[key] = parts
        return key

    async def results(self, batch_id: str):
        merged = {}
        for prov, bid in self._parts.pop(batch_id):
            merged.update(await self._api(prov).results(bid))
        return merged

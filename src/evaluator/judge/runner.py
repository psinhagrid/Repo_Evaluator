"""Live driver: run sessions against an LLM, with bounded concurrency."""

from __future__ import annotations

import asyncio
import re

from ..llm import LLM

RATE_LIMIT_WAIT_CAP_S = 600   # give up on a request after this much total waiting on 429s
RETRY_AFTER_RE = re.compile(r"try again in ([0-9.]+)\s*(ms|s)", re.I)


def _rate_limit_delay(error: Exception, attempt: int) -> float:
    """Seconds to wait after a 429: the provider's hint when it gives one, else exponential backoff."""
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    hint = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        if hint is not None:
            return min(60.0, float(hint) + 0.5)
    except ValueError:
        pass
    m = RETRY_AFTER_RE.search(str(error))
    if m:
        value = float(m.group(1)) / (1000 if m.group(2).lower() == "ms" else 1)
        return min(60.0, value + 0.5)
    return min(60.0, 2.0 * 2 ** attempt)


async def run_session(llm: LLM, session, purpose_timeout: float | None = None):
    """Drive any session object with next_request()/consume()/api_error() to completion.

    Rate limits (HTTP 429) are not failures of the request: the same request is retried after the
    provider's suggested wait, without using up one of the session's attempts.
    """
    while (params := session.next_request()) is not None:
        waited, attempt = 0.0, 0
        while True:
            try:
                if purpose_timeout:
                    message = await asyncio.wait_for(llm.create(params), timeout=purpose_timeout)
                else:
                    message = await llm.create(params)
            except asyncio.TimeoutError:
                session.api_error("timeout")
                message = None
            except Exception as e:  # SDK errors after its own retries; the session decides whether to retry
                if getattr(e, "status_code", None) == 429 and waited < RATE_LIMIT_WAIT_CAP_S:
                    delay = _rate_limit_delay(e, attempt)
                    attempt += 1
                    waited += delay
                    await asyncio.sleep(delay)
                    continue
                session.api_error(f"{type(e).__name__}: {e}"[:300], getattr(e, "status_code", None))
                message = None
            break
        if message is not None:
            session.consume(message)
    return session.result


async def run_sessions(llm: LLM, sessions: list, concurrency: int = 10) -> list:
    sem = asyncio.Semaphore(concurrency)

    async def one(s):
        async with sem:
            return await run_session(llm, s)

    return await asyncio.gather(*(one(s) for s in sessions))

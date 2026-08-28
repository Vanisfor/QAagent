"""Run real 20/50/100-concurrency load tests against the SSE chat endpoint."""

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass
from typing import Sequence

import httpx
from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class SessionCredential:
    """One server-created chat session and its bearer token."""

    session_id: str
    token: str


@dataclass(frozen=True)
class StreamResult:
    """Content-free timing result for one SSE request."""

    success: bool
    status_code: int
    first_event_seconds: float | None
    total_seconds: float


def percentile(values: Sequence[float], percent: float) -> float:
    """Return a linearly interpolated percentile."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


async def create_session(client: httpx.AsyncClient, user_token: str) -> SessionCredential:
    """Create one independently authenticated chat session."""
    response = await client.post(
        "/api/v1/auth/session",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    response.raise_for_status()
    payload = response.json()
    return SessionCredential(session_id=str(payload["session_id"]), token=str(payload["token"]["access_token"]))


async def stream_once(
    client: httpx.AsyncClient,
    credential: SessionCredential,
    message: str,
) -> StreamResult:
    """Consume one real SSE response without retaining model content."""
    started = time.perf_counter()
    first_event: float | None = None
    completed = False
    status_code = 0
    try:
        async with client.stream(
            "POST",
            "/api/v1/chatbot/chat/stream",
            headers={"Authorization": f"Bearer {credential.token}"},
            json={"messages": [{"role": "user", "content": message}], "reasoning_effort": "off"},
        ) as response:
            status_code = response.status_code
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                if first_event is None:
                    first_event = time.perf_counter() - started
                event = json.loads(line[6:])
                if event.get("type") == "done" and event.get("done") is True:
                    completed = True
    except (httpx.HTTPError, json.JSONDecodeError):
        completed = False
    return StreamResult(
        success=completed,
        status_code=status_code,
        first_event_seconds=first_event,
        total_seconds=time.perf_counter() - started,
    )


async def delete_session(client: httpx.AsyncClient, credential: SessionCredential) -> None:
    """Best-effort cleanup of one load-test session."""
    try:
        await client.delete(
            f"/api/v1/auth/session/{credential.session_id}",
            headers={"Authorization": f"Bearer {credential.token}"},
        )
    except httpx.HTTPError:
        return


async def run_level(
    client: httpx.AsyncClient,
    user_token: str,
    concurrency: int,
    message: str,
) -> tuple[list[StreamResult], float]:
    """Create isolated sessions and execute one concurrent load level."""
    credentials = list(await asyncio.gather(*(create_session(client, user_token) for _ in range(concurrency))))
    started = time.perf_counter()
    try:
        results = list(await asyncio.gather(*(stream_once(client, credential, message) for credential in credentials)))
        return results, time.perf_counter() - started
    finally:
        await asyncio.gather(*(delete_session(client, credential) for credential in credentials))


def add_row(table: Table, concurrency: int, results: list[StreamResult], wall_seconds: float) -> bool:
    """Add one content-free result row and return whether it passed."""
    successes = [result for result in results if result.success]
    first_events = [result.first_event_seconds for result in successes if result.first_event_seconds is not None]
    totals = [result.total_seconds for result in successes]
    success_rate = len(successes) / len(results) if results else 0.0
    table.add_row(
        str(concurrency),
        f"{success_rate:.1%}",
        f"{percentile(first_events, 0.50):.3f}",
        f"{percentile(first_events, 0.95):.3f}",
        f"{percentile(totals, 0.95):.3f}",
        f"{len(successes) / wall_seconds:.2f}" if wall_seconds else "0.00",
        f"{statistics.mean(totals):.3f}" if totals else "0.000",
    )
    return success_rate == 1.0


async def run(args: argparse.Namespace) -> int:
    """Run every configured concurrency level."""
    user_token = os.getenv("QAAGENT_USER_TOKEN", "")
    if not user_token:
        raise RuntimeError("QAAGENT_USER_TOKEN must contain a valid user bearer token")

    table = Table(title="QAagent SSE load test")
    for column in ("concurrency", "success", "TTFE p50", "TTFE p95", "total p95", "req/s", "mean total"):
        table.add_column(column)

    all_passed = True
    timeout = httpx.Timeout(args.timeout, connect=10.0)
    limits = httpx.Limits(max_connections=max(args.levels), max_keepalive_connections=max(args.levels))
    async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout, limits=limits) as client:
        for level in args.levels:
            results, wall_seconds = await run_level(client, user_token, level, args.message)
            all_passed = add_row(table, level, results, wall_seconds) and all_passed

    Console().print(table)
    return 0 if all_passed else 1


def parse_args() -> argparse.Namespace:
    """Parse command-line options without accepting secrets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--levels", nargs="+", type=int, default=[20, 50, 100])
    parser.add_argument("--message", default="Reply with exactly: load-test-ok")
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))

"""
runner.py -- MoE-Eval Benchmark Runner

An asynchronous Python script that reads test cases from the moe_eval_v1.json
dataset, sends them to the MoE Sovereign API, and records responses, latencies,
token usage, and routing information.

Supports:
  - Single-turn tests (precision, routing)
  - Multi-turn sessions (compounding knowledge / GraphRAG tests)

Configuration via environment variables:
  MOE_API_BASE     Base URL of the orchestrator (default: http://localhost:8002)
  MOE_API_KEY      API key for authentication (required)
  MOE_TEMPLATE     Template name to use (default: moe-reference-30b-balanced)
  MOE_EVAL_DATASET Path to the dataset JSON (default: datasets/moe_eval_v1.json)

Usage:
  MOE_API_KEY=moe-sk-... python benchmarks/runner.py
  MOE_API_KEY=moe-sk-... MOE_TEMPLATE=moe-reference-8b-fast python benchmarks/runner.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import httpx

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

API_BASE   = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY    = os.environ.get("MOE_API_KEY", "")
TEMPLATE   = os.environ.get("MOE_TEMPLATE", "moe-reference-30b-balanced")
_DATASET_FILE = os.environ.get("MOE_EVAL_DATASET", "moe_eval_v1.json")
_DATASET_DIR  = pathlib.Path(__file__).parent / "datasets"
DATASET = pathlib.Path(_DATASET_FILE) if pathlib.Path(_DATASET_FILE).is_absolute() else _DATASET_DIR / _DATASET_FILE
RESULTS_DIR = pathlib.Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Number of single_turn tests to run concurrently.
# multi_turn tests always run sequentially (session-state dependency).
PARALLEL_TESTS   = int(os.environ.get("MOE_PARALLEL_TESTS", "3"))
MAX_RETRIES      = int(os.environ.get("MOE_MAX_RETRIES", "3"))
RETRY_DELAY_S    = int(os.environ.get("MOE_RETRY_DELAY", "30"))
# Base timeout per API call attempt. Each retry attempt gets extra time:
#   attempt 1: BASE_TIMEOUT, attempt 2: BASE_TIMEOUT*1.5, attempt 3: BASE_TIMEOUT*2
# Capped at MAX_TIMEOUT (hard upper bound, prevents infinite waits).
BASE_TIMEOUT     = int(os.environ.get("MOE_TIMEOUT", "1200"))   # 20 min base
MAX_TIMEOUT      = int(os.environ.get("MOE_MAX_TIMEOUT", "3600")) # 1 h ceiling
# Max wait for API health-check before aborting (seconds).
API_HEALTH_WAIT  = int(os.environ.get("MOE_HEALTH_WAIT", "600"))

if not API_KEY:
    print("ERROR: MOE_API_KEY environment variable is required.", file=sys.stderr)
    print("  Create one via the Admin UI → Users → API Keys.", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class TurnResult:
    turn: int
    role: str
    prompt: str
    response: str
    wall_clock_s: float
    prompt_tokens: int
    completion_tokens: int
    http_status: int
    error: str = ""


@dataclass
class TestCaseResult:
    test_id: str
    test_name: str
    category: str
    template: str
    test_type: str   # single_turn or multi_turn
    turns: list[TurnResult] = field(default_factory=list)
    # Scoring (filled by evaluator.py, not here)
    score: float | None = None
    score_details: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# API call
# --------------------------------------------------------------------------

async def call_api(
    client: httpx.AsyncClient,
    messages: list[dict],
    session_id: str | None = None,
    max_tokens: int = 2048,
    timeout: int = 3600,
) -> dict:
    """Send a chat completion request to the MoE orchestrator."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["X-Session-ID"] = session_id

    payload = {
        "model": TEMPLATE,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{API_BASE}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        dt = time.perf_counter() - t0
        data = r.json()
        return {"dt": dt, "status": r.status_code, "data": data}
    except httpx.ReadTimeout:
        return {"dt": time.perf_counter() - t0, "status": 0,
                "data": {"error": "client_timeout"}}
    except Exception as e:
        return {"dt": time.perf_counter() - t0, "status": 0,
                "data": {"error": str(e)[:500]}}


async def wait_for_api(client: httpx.AsyncClient) -> bool:
    """Poll the API health endpoint until it responds or API_HEALTH_WAIT is exceeded.

    Returns True if the API is healthy, False if it timed out.
    This prevents tests from starting while models are still loading into VRAM
    or the orchestrator is restarting.
    """
    deadline = time.monotonic() + API_HEALTH_WAIT
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            r = await client.get(f"{API_BASE}/health", timeout=10)
            if r.status_code < 500:
                if attempt > 1:
                    print(f"    [health] API ready after {attempt} attempts", flush=True)
                return True
        except Exception:
            pass
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            break
        print(
            f"    [health] API not ready (attempt {attempt}), retrying in 15s"
            f" ({remaining}s left)...",
            flush=True,
        )
        await asyncio.sleep(15)
    print(f"    [health] API did not become ready within {API_HEALTH_WAIT}s", flush=True)
    return False


async def call_api_with_retry(
    client: httpx.AsyncClient,
    messages: list[dict],
    session_id: str | None = None,
    max_tokens: int = 2048,
    label: str = "",
) -> dict:
    """Wrapper around call_api with up to MAX_RETRIES attempts on transient failures.

    Each attempt receives more time (adaptive timeout) and waits longer before
    the next retry (exponential backoff), accommodating extreme hardware delays
    such as model loading or VRAM pressure:
      attempt 1: timeout = BASE_TIMEOUT,       delay = RETRY_DELAY_S
      attempt 2: timeout = BASE_TIMEOUT * 1.5, delay = RETRY_DELAY_S * 2
      attempt 3: timeout = BASE_TIMEOUT * 2,   delay = RETRY_DELAY_S * 4
    All values are capped at MAX_TIMEOUT / 300s respectively.

    Retries on status=0 (timeout / network error) or HTTP 5xx.
    Does not retry on HTTP 4xx (client error — bad request, auth failure).
    """
    res: dict = {"status": 0, "dt": 0.0, "data": {"error": "no_attempts"}}
    for attempt in range(1, MAX_RETRIES + 1):
        per_attempt_timeout = min(int(BASE_TIMEOUT * (1 + 0.5 * (attempt - 1))), MAX_TIMEOUT)
        res = await call_api(client, messages, session_id, max_tokens, per_attempt_timeout)
        status = res["status"]
        if status == 200 or (400 <= status < 500):
            return res

        reason = res["data"].get("error", "") if status == 0 else f"HTTP {status}"
        if attempt < MAX_RETRIES:
            delay = min(RETRY_DELAY_S * (2 ** (attempt - 1)), 300)
            next_timeout = min(int(BASE_TIMEOUT * (1 + 0.5 * attempt)), MAX_TIMEOUT)
            print(
                f"      [retry {attempt}/{MAX_RETRIES}] {label}failed ({reason}) "
                f"after {res['dt']:.0f}s — waiting {delay}s, next timeout={next_timeout}s...",
                flush=True,
            )
            # Before retrying, verify the API is actually alive so we don't burn
            # the next timeout slot waiting on a down service.
            await asyncio.sleep(delay)
            await wait_for_api(client)
        else:
            print(
                f"      [give up] {label}all {MAX_RETRIES} attempts exhausted ({reason})",
                flush=True,
            )
    return res


# --------------------------------------------------------------------------
# Test case execution
# --------------------------------------------------------------------------

async def run_single_turn(
    client: httpx.AsyncClient, tc: dict,
) -> TestCaseResult:
    """Execute a single-turn test case."""
    result = TestCaseResult(
        test_id=tc["id"],
        test_name=tc["name"],
        category=tc["category"],
        template=TEMPLATE,
        test_type="single_turn",
    )

    messages = [{"role": "user", "content": tc["prompt"]}]
    res = await call_api_with_retry(client, messages, label=f"{tc['id']} ")

    data = res["data"]
    choices = data.get("choices", [])
    usage = data.get("usage", {})
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    err = ""
    if isinstance(data.get("error"), dict):
        err = data["error"].get("message", "")
    elif isinstance(data.get("error"), str):
        err = data["error"]

    result.turns.append(TurnResult(
        turn=1,
        role="query",
        prompt=tc["prompt"],
        response=content,
        wall_clock_s=res["dt"],
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        http_status=res["status"],
        error=err,
    ))
    return result


async def run_multi_turn(
    client: httpx.AsyncClient, tc: dict,
) -> TestCaseResult:
    """Execute a multi-turn test case, maintaining conversation history."""
    result = TestCaseResult(
        test_id=tc["id"],
        test_name=tc["name"],
        category=tc["category"],
        template=TEMPLATE,
        test_type="multi_turn",
    )

    # Use a stable session ID so the orchestrator can track the conversation
    session_id = f"moe-eval-{tc['id']}-{int(time.time())}"
    messages: list[dict] = []

    for turn_def in tc["turns"]:
        turn_num = turn_def["turn"]
        prompt = turn_def["prompt"]

        # Append user message to history
        messages.append({"role": "user", "content": prompt})

        print(f"    turn {turn_num}: {prompt[:60]}...", flush=True)

        res = await call_api_with_retry(
            client, messages, session_id=session_id,
            label=f"turn {turn_num} ",
        )

        data = res["data"]
        choices = data.get("choices", [])
        usage = data.get("usage", {})
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        err = ""
        if isinstance(data.get("error"), dict):
            err = data["error"].get("message", "")

        # Append assistant response to history for next turn.
        # On total failure, use a marker so subsequent turns are not misled by silence.
        history_content = content if content else "[TURN FAILED — RETRIES EXHAUSTED]"
        messages.append({"role": "assistant", "content": history_content})

        result.turns.append(TurnResult(
            turn=turn_num,
            role=turn_def.get("role", "query"),
            prompt=prompt,
            response=content,
            wall_clock_s=res["dt"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            http_status=res["status"],
            error=err,
        ))

        snippet = content[:80].replace("\n", " ")
        status = "✓" if res["status"] == 200 and not err else "✗"
        print(f"      {status} dt={res['dt']:.1f}s tok={usage.get('completion_tokens',0)} "
              f"→ {snippet}", flush=True)

    return result


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def _print_result(r: TestCaseResult, prefix: str = "") -> None:
    """Print a one-line summary for a completed test case."""
    total_dt  = sum(t.wall_clock_s for t in r.turns)
    total_tok = sum(t.completion_tokens for t in r.turns)
    last_turn = r.turns[-1] if r.turns else None
    status    = "✓" if last_turn and last_turn.http_status == 200 and not last_turn.error else "✗"
    print(f"{prefix}{status} {r.test_id}  dt={total_dt:.1f}s  tokens={total_tok}", flush=True)


async def _run_single_with_sem(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    tc: dict,
    idx: int,
    total: int,
) -> TestCaseResult:
    """Acquire semaphore, run a single-turn test, release, and return result."""
    async with sem:
        print(f"\n[{idx}/{total}] {tc['id']} — {tc['name']}  (parallel)", flush=True)
        r = await run_single_turn(client, tc)
        _print_result(r, prefix="  ")
        return r


async def main() -> int:
    dataset = json.loads(DATASET.read_text())
    test_cases = dataset["test_cases"]

    print(f"MoE-Eval Benchmark Runner", flush=True)
    print(f"  Dataset:      {dataset['name']} v{dataset['version']}", flush=True)
    print(f"  Template:     {TEMPLATE}", flush=True)
    print(f"  API:          {API_BASE}", flush=True)
    print(f"  Tests:        {len(test_cases)}", flush=True)
    print(f"  Parallelism:  {PARALLEL_TESTS} concurrent single_turn tests", flush=True)
    print(f"{'='*72}", flush=True)

    single_turn_cases = [(i + 1, tc) for i, tc in enumerate(test_cases)
                         if tc.get("type", "single_turn") == "single_turn"]
    multi_turn_cases  = [(i + 1, tc) for i, tc in enumerate(test_cases)
                         if tc.get("type", "single_turn") == "multi_turn"]
    total = len(test_cases)

    # Index map so we can restore dataset order after concurrent execution
    order_index = {tc["id"]: i for i, tc in enumerate(test_cases)}

    results: list[TestCaseResult] = []

    async with httpx.AsyncClient() as client:
        # Wait for the orchestrator to be ready before starting any tests.
        # This absorbs cold-start delays (model loading, container restart).
        print(f"[pre-flight] Checking API health at {API_BASE} ...", flush=True)
        if not await wait_for_api(client):
            print("ERROR: API did not become healthy. Aborting run.", file=sys.stderr)
            return 1
        print(f"[pre-flight] API healthy. Starting tests.\n", flush=True)

        # --- Phase 1: single_turn tests in parallel --------------------------
        if single_turn_cases:
            sem = asyncio.Semaphore(PARALLEL_TESTS)
            print(f"\n--- Phase 1: {len(single_turn_cases)} single_turn tests"
                  f" (concurrency={PARALLEL_TESTS}) ---", flush=True)
            tasks = [
                _run_single_with_sem(sem, client, tc, idx, total)
                for idx, tc in single_turn_cases
            ]
            single_results = await asyncio.gather(*tasks)
            results.extend(single_results)

        # --- Phase 2: multi_turn tests sequentially --------------------------
        if multi_turn_cases:
            print(f"\n--- Phase 2: {len(multi_turn_cases)} multi_turn tests"
                  f" (sequential) ---", flush=True)
            for idx, tc in multi_turn_cases:
                print(f"\n[{idx}/{total}] {tc['id']} — {tc['name']}", flush=True)
                print(f"  category: {tc['category']}  type: multi_turn", flush=True)
                r = await run_multi_turn(client, tc)
                results.append(r)
                _print_result(r, prefix="  ")

    # Restore dataset order
    results.sort(key=lambda r: order_index.get(r.test_id, 9999))

    # Save results
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = {
        "dataset": dataset["name"],
        "dataset_version": dataset["version"],
        "template": TEMPLATE,
        "timestamp": ts,
        "api_base": API_BASE,
        "results": [asdict(r) for r in results],
    }

    json_path = RESULTS_DIR / f"run_{TEMPLATE}_{ts}.json"
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Also write a stable latest file
    latest = RESULTS_DIR / f"latest_{TEMPLATE}.json"
    latest.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    print(f"\n{'='*72}", flush=True)
    print(f"Results saved: {json_path}", flush=True)
    print(f"Stable:        {latest}", flush=True)

    # Print compact summary table
    print(f"\n{'='*72}", flush=True)
    print(f"{'Test ID':40s} {'Cat':15s} {'dt(s)':>8s} {'tok':>6s} {'Status':>8s}", flush=True)
    print(f"{'-'*40} {'-'*15} {'-'*8} {'-'*6} {'-'*8}", flush=True)
    for r in results:
        total_dt = sum(t.wall_clock_s for t in r.turns)
        total_tok = sum(t.completion_tokens for t in r.turns)
        last = r.turns[-1] if r.turns else None
        st = "OK" if last and last.http_status == 200 and not last.error else "FAIL"
        print(f"{r.test_id:40s} {r.category:15s} {total_dt:8.1f} {total_tok:6d} {st:>8s}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

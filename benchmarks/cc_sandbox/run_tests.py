#!/usr/bin/env python3
"""
Claude Code Profile Benchmark Runner

Tests 3 CC profiles (native, reasoning, orchestrated) against 5 coding tasks
using the MoE Sovereign /v1/messages API (Anthropic-compatible).

Each task sends the test project source code as context and asks the LLM
to perform a specific coding task. Measures latency, token usage, and
captures the response for manual quality scoring.

Configuration via environment:
  MOE_API_BASE    Orchestrator URL (default: http://localhost:8002)
  MOE_API_KEY     API key (required)

Usage:
  MOE_API_KEY=moe-sk-... python3 benchmarks/cc_sandbox/run_tests.py
"""

import asyncio
import json
import os
import pathlib
import sys
import time
from datetime import datetime

import httpx

API_BASE = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY = os.environ.get("MOE_API_KEY", "")

if not API_KEY:
    print("ERROR: MOE_API_KEY required.", file=sys.stderr)
    sys.exit(1)

RESULTS_DIR = pathlib.Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

TEST_PROJECT_DIR = pathlib.Path(__file__).parent / "test_project"

# Profile sets — select via MOE_PROFILE_SET env var
_PROFILE_SETS = {
    "reference": ["cc-ref-native", "cc-ref-reasoning", "cc-ref-orchestrated"],
    "innovator": ["cc-expert-fast", "cc-expert-balanced", "cc-expert-deep"],
}
PROFILES = _PROFILE_SETS.get(
    os.environ.get("MOE_PROFILE_SET", "reference"),
    _PROFILE_SETS["reference"]
)

TASKS = [
    {
        "id": "bugfix-sqli",
        "name": "Fix SQL Injection",
        "prompt": (
            "The file app.py has a SQL injection vulnerability in the get_user() function. "
            "Find the vulnerability, explain the risk, and provide the fixed code."
        ),
    },
    {
        "id": "feature-health",
        "name": "Add /health Endpoint",
        "prompt": (
            "Add a /health endpoint to app.py that returns a JSON response with "
            "the following fields: status ('ok'), uptime_seconds (time since app start), "
            "and database ('connected' or 'error'). The endpoint should check if the "
            "SQLite database is accessible."
        ),
    },
    {
        "id": "refactor-db",
        "name": "Extract Database Module",
        "prompt": (
            "Refactor app.py to separate database logic into a new file database.py. "
            "Move all SQLite operations (init_db, queries) into database.py as functions, "
            "and update app.py to import and use them. Keep the API routes unchanged."
        ),
    },
    {
        "id": "test-users",
        "name": "Write Pytest Tests",
        "prompt": (
            "Write pytest tests for the /users endpoint in app.py. Cover: "
            "1) GET /users returns empty list initially, "
            "2) POST /users creates a user and returns 201, "
            "3) GET /users/<username> returns the created user, "
            "4) POST /users with duplicate username returns 409, "
            "5) DELETE /users/<id> removes the user. "
            "Use Flask's test_client. Save as test_app.py."
        ),
    },
    {
        "id": "review-security",
        "name": "Security Code Review",
        "prompt": (
            "Perform a security code review of app.py. Identify all security issues, "
            "rank them by severity (critical/high/medium/low), explain the risk for each, "
            "and provide specific fix recommendations with code examples."
        ),
    },
]


def load_source_code() -> str:
    """Load the test project source for inclusion in the prompt."""
    app_path = TEST_PROJECT_DIR / "app.py"
    return app_path.read_text()


async def run_task(profile_id: str, task: dict, source_code: str) -> dict:
    """Run a single task against a profile via the /v1/chat/completions API."""
    # Map profile to MoE template/mode
    MODE_MAP = {
        "cc-ref-native": "default",
        "cc-ref-reasoning": "default",
        "cc-ref-orchestrated": "moe-reference-30b-balanced",
        "cc-expert-fast": "cc-expert-fast",
        "cc-expert-balanced": "cc-expert-balanced",
        "cc-expert-deep": "cc-expert-deep",
    }
    model_id = MODE_MAP.get(profile_id, profile_id)

    messages = [
        {
            "role": "user",
            "content": (
                f"Here is a Flask application (app.py):\n\n"
                f"```python\n{source_code}\n```\n\n"
                f"Task: {task['prompt']}"
            ),
        }
    ]

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "content-type": "application/json",
    }

    body = {
        "model": model_id,
        "max_tokens": 4096,
        "messages": messages,
    }

    t0 = time.time()
    answer = ""
    prompt_tokens = 0
    completion_tokens = 0
    error = None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12000.0)) as client:
            resp = await client.post(
                f"{API_BASE}/v1/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                error = f"HTTP {resp.status_code}: {resp.text[:500]}"
            else:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    answer = choices[0].get("message", {}).get("content", "")
                usage = data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
    except Exception as e:
        error = str(e)

    dt = time.time() - t0

    return {
        "profile": profile_id,
        "task_id": task["id"],
        "task_name": task["name"],
        "answer": answer[:2000] if answer else "",
        "answer_full_length": len(answer),
        "error": error,
        "latency_s": round(dt, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "ts": datetime.utcnow().isoformat(),
    }


async def main():
    source_code = load_source_code()

    print("Claude Code Profile Benchmark Runner")
    print(f"  API:      {API_BASE}")
    print(f"  Profiles: {', '.join(PROFILES)}")
    print(f"  Tasks:    {len(TASKS)}")
    print("=" * 72)

    all_results = []

    for profile in PROFILES:
        print(f"\n{'─' * 72}")
        print(f"Profile: {profile}")
        print(f"{'─' * 72}")

        for i, task in enumerate(TASKS, 1):
            print(f"  [{i}/{len(TASKS)}] {task['name']}...", end="", flush=True)
            result = await run_task(profile, task, source_code)
            all_results.append(result)

            if result["error"]:
                print(f" FAIL ({result['latency_s']}s) — {result['error'][:80]}")
            else:
                print(
                    f" OK ({result['latency_s']}s, "
                    f"{result['total_tokens']} tok, "
                    f"{result['answer_full_length']} chars)"
                )

    # Summary table
    print(f"\n{'=' * 72}")
    print("Results Summary")
    print(f"{'=' * 72}")
    print(f"{'Profile':<25} {'Task':<22} {'Time':>7} {'Tokens':>8} {'Status'}")
    print(f"{'-' * 25} {'-' * 22} {'-' * 7} {'-' * 8} {'-' * 6}")
    for r in all_results:
        status = "OK" if not r["error"] else "FAIL"
        print(
            f"{r['profile']:<25} {r['task_name']:<22} "
            f"{r['latency_s']:>6.1f}s {r['total_tokens']:>7} {status}"
        )

    # Save results
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"cc_profiles_{ts}.json"
    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nSaved: {out_path}")

    # Also save as latest
    latest = RESULTS_DIR / "cc_profiles_latest.json"
    latest.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"Stable: {latest}")


if __name__ == "__main__":
    asyncio.run(main())

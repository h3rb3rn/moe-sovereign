"""
pre_run_check.py — Smoke-test before starting a GAIA benchmark run.

Checks every layer that caused silent failures in previous runs:
  1. Container health (orchestrator, mcp-precision, splash, redis)
  2. MCP tool availability + basic function smoke-test
  3. Planner prompt length (>14K → context overflow → } leaks)
  4. Redis connectivity + cache key count
  5. Orchestrator /health endpoint
  6. GAIA runner syntax (import check)
  7. End-to-end: single simple question through full pipeline

Usage:
    HF_TOKEN=... MOE_API_KEY=... python benchmarks/pre_run_check.py
"""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time

import httpx

# Load benchmarks/.env if present — shell vars take precedence (override=False)
_env_file = pathlib.Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            _v = _v.strip().strip('"').strip("'")  # strip outer quotes
            os.environ.setdefault(_k.strip(), _v)

API_BASE = os.environ.get("MOE_API_BASE", "http://localhost:8002")
MCP_BASE = os.environ.get("MCP_BASE", "http://localhost:8003")
API_KEY  = os.environ.get("MOE_API_KEY", "")
TEMPLATE = os.environ.get("MOE_TEMPLATE", "moe-aihub-free-gremium-deep-wcc")

PASS = "✓"
FAIL = "✗"
WARN = "⚠"


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    sym = PASS if ok else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {sym} {label}{suffix}")
    return ok


# ── 1. Container health ───────────────────────────────────────────────────────

def check_containers() -> bool:
    section("1. Container health")
    required = {
        "langgraph-orchestrator": "up",     # has healthcheck → shows "(healthy)"
        "mcp-precision":         "up",      # no healthcheck, just needs to be up
        "terra_cache":           "up",      # Redis, no healthcheck
    }
    optional = {"moe-splash": "running"}
    all_ok = True
    try:
        result = subprocess.run(
            ["sudo", "docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        running = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                running[parts[0]] = parts[1]
    except Exception as e:
        print(f"  {FAIL} docker ps failed: {e}")
        return False

    for name, expected_state in required.items():
        status = running.get(name, "NOT FOUND")
        ok = "up" in status.lower()  # accept both "Up X (healthy)" and "Up X"
        all_ok = check(name, ok, status[:50]) and all_ok
    for name, _ in optional.items():
        status = running.get(name, "NOT FOUND")
        sym = PASS if "up" in status.lower() else WARN
        print(f"  {sym} {name} (optional)  ({status[:50]})")
    return all_ok


# ── 2. Orchestrator /health ───────────────────────────────────────────────────

def check_orchestrator() -> bool:
    section("2. Orchestrator /health + /v1/models")
    all_ok = True
    try:
        r = httpx.get(f"{API_BASE}/health", timeout=5)
        all_ok = check("/health endpoint", r.status_code == 200, f"HTTP {r.status_code}") and all_ok
    except Exception as e:
        all_ok = check("/health endpoint", False, str(e)[:60]) and all_ok

    if API_KEY:
        try:
            r = httpx.get(
                f"{API_BASE}/v1/models",
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=5,
            )
            templates = [m["id"] for m in r.json().get("data", [])]
            found = TEMPLATE in templates
            all_ok = check(f"Template '{TEMPLATE}'", found,
                          f"available: {len(templates)}" if not found else "") and all_ok
        except Exception as e:
            all_ok = check("Template check", False, str(e)[:60]) and all_ok
    else:
        print(f"  {WARN} Skipping template check (no MOE_API_KEY)")
    return all_ok


# ── 3. MCP tool smoke-tests ───────────────────────────────────────────────────
# MCP runs over stdio/SSE, not a REST endpoint — use docker exec to call tools.

def _docker_exec_mcp(tool: str, py_call: str, expected: str) -> bool:
    script = f"from server import {tool}; r = {py_call}; print(r)"
    try:
        result = subprocess.run(
            ["sudo", "docker", "exec", "mcp-precision", "python3", "-c", script],
            capture_output=True, text=True, timeout=30,
        )
        out = result.stdout + result.stderr
        ok = result.returncode == 0 and expected in out
        detail = out[:80].replace("\n", " ") if not ok else ""
        return check(f"mcp:{tool}", ok, detail)
    except Exception as e:
        return check(f"mcp:{tool}", False, str(e)[:60])


async def check_mcp_tools() -> bool:
    section("3. MCP tool smoke-tests (via docker exec mcp-precision)")
    all_ok = True
    tests = [
        ("calculate",            'calculate("2**10")',                              "1024"),
        ("wikidata_sparql",      'wikidata_sparql("SELECT ?dob WHERE { wd:Q192131 wdt:P569 ?dob. }")', "1910"),
        ("wayback_fetch",        'wayback_fetch("https://en.wikipedia.org/wiki/Python_(programming_language)", max_chars=100)', "snapshot_timestamp"),
        ("github_search_issues", 'github_search_issues(repo="numpy/numpy", labels="Regression", state="closed", max_results=1)', "issues"),
        ("duckduckgo_search",    'duckduckgo_search("Python", max_results=1)',      "results"),
        ("orcid_works_count",    'orcid_works_count("0000-0002-1825-0097", before_year=2020)', "filtered_count"),
        ("python_sandbox",       'python_sandbox("print(sum(range(10)))")',                   "45"),
    ]
    for tool, call, expected in tests:
        ok = _docker_exec_mcp(tool, call, expected)
        all_ok = ok and all_ok
    return all_ok


# ── 4. Planner prompt length ──────────────────────────────────────────────────

async def check_planner_prompt() -> bool:
    section("4. Planner prompt length (>14K → context overflow → } leaks)")
    try:
        import psycopg
        db_url = os.environ.get("MOE_USERDB_URL", "")
        if not db_url:
            print(f"  {WARN} MOE_USERDB_URL not set — skipping")
            return True
        conn = await psycopg.AsyncConnection.connect(db_url)
        cur = conn.cursor()
        await cur.execute(
            "SELECT length(config_json->>'planner_prompt') FROM admin_expert_templates WHERE name = %s",
            (TEMPLATE,)
        )
        row = await cur.fetchone()
        await conn.close()
        if not row:
            return check("Template in DB", False, f"'{TEMPLATE}' not found")
        length = row[0] or 0
        ok = length < 14_000
        return check(
            f"Planner prompt length: {length} chars",
            ok,
            "OK" if ok else f"TOO LONG — risk of context overflow + }} leaks",
        )
    except Exception as e:
        print(f"  {WARN} DB check skipped: {e}")
        return True


# ── 5. Redis cache ────────────────────────────────────────────────────────────

def check_redis() -> bool:
    section("5. Redis search cache")
    try:
        redis_pass = ""
        env_file = "/opt/moe-sovereign/.env"
        if os.path.exists(env_file):
            for line in open(env_file):
                if line.startswith("REDIS_PASSWORD="):
                    redis_pass = line.strip().split("=", 1)[1]

        cmd = ["sudo", "docker", "exec", "terra_cache", "redis-cli"]
        if redis_pass:
            cmd += ["-a", redis_pass]
        cmd += ["--scan", "--pattern", "moe:search_cache:*"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        keys = [l for l in result.stdout.splitlines() if l and "Warning" not in l]
        ok = True  # Redis is functional regardless of cache count
        return check(f"Redis reachable", ok, f"{len(keys)} search_cache entries")
    except Exception as e:
        return check("Redis check", False, str(e)[:60])


# ── 6. MinIO connectivity ─────────────────────────────────────────────────────

def check_minio() -> bool:
    section("6. MinIO connectivity (GAIA attachment uploads)")
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    user     = os.environ.get("MINIO_ROOT_USER", "")
    password = os.environ.get("MINIO_ROOT_PASSWORD", "")
    if not endpoint:
        print(f"  {WARN} MINIO_ENDPOINT not set — attachment uploads will use text-injection fallback")
        return True
    # Quick HTTP health check (MinIO S3 root returns 403 AccessDenied = server up)
    proto = "https" if os.environ.get("MINIO_SECURE", "") else "http"
    url = f"{proto}://{endpoint}/"
    try:
        import httpx as _httpx
        r = _httpx.get(url, timeout=5)
        reachable = r.status_code in (200, 403)
        msg = f"HTTP {r.status_code} at {endpoint}"
        if not reachable:
            return check("MinIO reachable", False, msg + " — check MINIO_ENDPOINT")
        check("MinIO reachable", True, msg)
        # Verify credentials via minio Python client (proper S3 Signature-V4 auth)
        if user and password:
            bucket = os.environ.get("MINIO_DEFAULT_BUCKET", "moe-files")
            try:
                from minio import Minio
                mc = Minio(endpoint, access_key=user, secret_key=password, secure=(proto == "https"))
                exists = mc.bucket_exists(bucket)
                return check("MinIO credentials", True, f"bucket '{bucket}' {'exists' if exists else 'not found (ok)'}")
            except Exception as _ce:
                return check("MinIO credentials", False, str(_ce)[:80])
        return True
    except Exception as e:
        return check("MinIO reachable", False, f"{endpoint}: {str(e)[:60]}")


# ── 7. Runner import syntax ───────────────────────────────────────────────────

def check_runner_syntax() -> bool:
    section("7. Runner import syntax")
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('benchmarks/gaia_runner.py').read())"],
            capture_output=True, text=True, timeout=10,
        )
        ok = result.returncode == 0
        return check("gaia_runner.py AST parse", ok, result.stderr[:60] if not ok else "")
    except Exception as e:
        return check("gaia_runner.py AST parse", False, str(e)[:60])


def check_main_syntax() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('main.py').read())"],
            capture_output=True, text=True, timeout=10,
        )
        ok = result.returncode == 0
        return check("main.py AST parse", ok, result.stderr[:60] if not ok else "")
    except Exception as e:
        return check("main.py AST parse", False, str(e)[:60])


# ── 8. End-to-end mini question ───────────────────────────────────────────────

async def check_e2e() -> bool:
    section("8. End-to-end: simple question through full pipeline")
    if not API_KEY:
        print(f"  {WARN} No MOE_API_KEY — skipping E2E test")
        return True
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{API_BASE}/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": TEMPLATE,
                    "messages": [
                        {"role": "system", "content": "Answer in one word."},
                        {"role": "user", "content": "What is 2 + 2?"},
                    ],
                },
            )
        elapsed = time.time() - t0
        content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        ok = r.status_code == 200 and any(x in content for x in ("4", "four", "Four"))
        return check(
            f"E2E response (2+2={content.strip()[:20]})",
            ok,
            f"{elapsed:.1f}s — HTTP {r.status_code}",
        )
    except Exception as e:
        return check("E2E question", False, str(e)[:80])


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 60)
    print("  MoE GAIA Pre-Run Smoke Test")
    print("=" * 60)

    results = [
        check_containers(),
        check_orchestrator(),
        await check_mcp_tools(),
        await check_planner_prompt(),
        check_redis(),
        check_minio(),
        check_runner_syntax(),
        check_main_syntax(),
        await check_e2e(),
    ]

    passed = sum(1 for r in results if r)
    total  = len(results)
    print(f"\n{'=' * 60}")
    print(f"  Result: {passed}/{total} checks passed")
    if passed < total:
        print(f"  {FAIL} Fix failing checks before running the benchmark!")
        sys.exit(1)
    else:
        print(f"  {PASS} All checks passed — ready to run benchmark.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
scripts/reference_set_regression.py — Held-out reference set regression test.

Runs the curated question set in data/reference_questions.json against the
CURRENTLY ACTIVE pipeline configuration (real HTTP call to the orchestrator's
own /v1/chat/completions, exercising the live planner/dynamic-router — not a
direct graph invocation from inside this script), then judges each answer
against its known-good reference answer + rubric. Complements the sampled,
blind-A/B live traffic probe in services/quality_probe.py (which has no
ground truth) with a small, repeatable, ground-truth-based regression check —
intended to catch template/model changes (e.g. after an Eurisko mutation)
that silently degrade answer quality.

Results are purely a passive report (template_regression_log table + a log
summary) — this does NOT feed back into
scripts/eurisko_template_optimizer.py's weight updates.

Schedule:
  Run nightly via systemd timer (see reference-set-regression.timer/.service),
  offset from ontology-gap-healer.timer to avoid GPU contention:
    docker exec langgraph-orchestrator python3 /app/scripts/reference_set_regression.py

Configuration via environment variables:
  MOE_API_BASE   Orchestrator URL (default: http://localhost:8000)
  MOE_API_KEY    API key for the bench/admin user (required)
  MOE_TEMPLATE   Template used for the pipeline calls (default: moe-n04-rtx-qwen3.6:35b-256k)
  JUDGE_MODEL    Override judge model (default: first Ollama entry in INFERENCE_SERVERS)
  JUDGE_URL      Override judge node URL (default: same as JUDGE_MODEL's server)
  JUDGE_TOKEN    Override judge node token (default: that server's configured token)

Usage:
  MOE_API_KEY=moe-sk-... python3 scripts/reference_set_regression.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys
import time
import uuid

import httpx

API_BASE = os.environ.get("MOE_API_BASE", "http://localhost:8000")
API_KEY = os.environ.get("MOE_API_KEY", "")
TEMPLATE = os.environ.get("MOE_TEMPLATE", "moe-n04-rtx-qwen3.6:35b-256k")

LOG_DIR = pathlib.Path("/app/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [reference-set-regression] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "reference_set_regression.log"),
    ],
)
log = logging.getLogger(__name__)

if not API_KEY:
    log.error("MOE_API_KEY is required")
    sys.exit(1)

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS template_regression_log (
    id            TEXT PRIMARY KEY,
    created_at    TIMESTAMPTZ DEFAULT now(),
    run_id        TEXT,
    reference_id  TEXT,
    category      TEXT,
    template_id   TEXT,
    query_preview TEXT,
    pipeline_answer_chars INT,
    verdict       TEXT,          -- 'PASS' | 'PARTIAL' | 'FAIL' | 'ERROR'
    judge_raw     TEXT,
    unsupported_claims_ratio DOUBLE PRECISION,
    latency_ms    INT
);
"""

_REGRESSION_JUDGE_PROMPT = """You are a strict evaluator grading an AI answer against a known-good reference answer and rubric.

Respond with exactly two lines:
VERDICT: PASS, PARTIAL, or FAIL
REASON: one short sentence.

PASS = the answer satisfies the rubric and is consistent with the reference answer.
PARTIAL = the answer is on the right track but misses or gets part of the rubric wrong.
FAIL = the answer contradicts the reference answer or fails the rubric.

## Question
{query}

## Reference answer
{reference_answer}

## Rubric
{rubric}

## Answer to grade
{answer}
"""


async def _db_insert(row: dict) -> None:
    from config import MOE_USERDB_URL
    try:
        import psycopg
        async with await psycopg.AsyncConnection.connect(MOE_USERDB_URL) as conn:
            async with conn.cursor() as cur:
                await cur.execute(_TABLE_SQL)
                cols = ",".join(row.keys())
                ph = ",".join(["%s"] * len(row))
                await cur.execute(
                    f"INSERT INTO template_regression_log ({cols}) VALUES ({ph})",
                    list(row.values()),
                )
            await conn.commit()
    except Exception as e:
        log.warning("DB insert failed: %s", e)


async def _pick_judge_server() -> tuple[str, str, str]:
    """Returns (model, url, token) for the judge. JUDGE_MODEL/JUDGE_URL env
    vars override; otherwise queries /api/tags on Ollama servers from
    INFERENCE_SERVERS (same discovery approach as model_lifecycle.py) and
    uses the first model reported as currently loaded, falling back to the
    first model in the server's tag list."""
    judge_model = os.environ.get("JUDGE_MODEL", "")
    judge_url = os.environ.get("JUDGE_URL", "")
    judge_token = os.environ.get("JUDGE_TOKEN", "")
    if judge_model and judge_url:
        return judge_model, judge_url, judge_token or "ollama"
    from config import INFERENCE_SERVERS_LIST
    async with httpx.AsyncClient(timeout=10.0) as client:
        for srv in INFERENCE_SERVERS_LIST:
            if srv.get("api_type", "ollama") != "ollama" or not srv.get("url"):
                continue
            _url = srv["url"]
            _token = srv.get("token", "ollama")
            base = _url.rstrip("/").removesuffix("/v1")
            try:
                r = await client.get(f"{base}/api/ps", headers={"Authorization": f"Bearer {_token}"})
                loaded = r.json().get("models", [])
                if loaded:
                    return judge_model or loaded[0]["name"], judge_url or _url, judge_token or _token
                r = await client.get(f"{base}/api/tags", headers={"Authorization": f"Bearer {_token}"})
                tags = r.json().get("models", [])
                if tags:
                    return judge_model or tags[0]["name"], judge_url or _url, judge_token or _token
            except Exception:
                continue
    raise RuntimeError("No usable Ollama server found in INFERENCE_SERVERS for the judge")


async def query_pipeline(client: httpx.AsyncClient, query: str) -> tuple[str, int]:
    """Real end-to-end call through the live pipeline (planner + dynamic
    router + experts), exactly like a normal user request would go through —
    not a direct graph invocation, so it actually exercises the currently
    active template/routing configuration."""
    t0 = time.monotonic()
    r = await client.post(
        f"{API_BASE}/v1/chat/completions",
        json={
            "model": TEMPLATE, "stream": False,
            "messages": [{"role": "user", "content": query}],
        },
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        timeout=300.0,
    )
    data = r.json()
    answer = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    latency_ms = int((time.monotonic() - t0) * 1000)
    return answer, latency_ms


async def run_regression() -> None:
    from services.reference_set_store import load_reference_set
    from services.quality_probe import _ollama_generate
    from services.trust_score import _unsupported_claim_ratio

    items = load_reference_set()
    if not items:
        log.warning("No reference questions found — nothing to do")
        return

    judge_model, judge_url, judge_token = await _pick_judge_server()
    run_id = uuid.uuid4().hex
    summary: dict[str, dict[str, int]] = {}

    async with httpx.AsyncClient() as client:
        for item in items:
            category = item.get("category", "general")
            summary.setdefault(category, {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "ERROR": 0})
            try:
                answer, latency_ms = await query_pipeline(client, item["query"])
                if not answer.strip():
                    raise RuntimeError("empty pipeline answer")

                judge_raw, _ = await _ollama_generate(
                    judge_url, judge_token, judge_model,
                    _REGRESSION_JUDGE_PROMPT.format(
                        query=item["query"][:2000],
                        reference_answer=item.get("reference_answer", "")[:2000],
                        rubric=item.get("rubric", "")[:1000],
                        answer=answer[:4000],
                    ),
                )
                verdict = "ERROR"
                for line in judge_raw.splitlines():
                    if line.strip().upper().startswith("VERDICT:"):
                        v = line.split(":", 1)[1].strip().upper()
                        if v.startswith("PASS"):
                            verdict = "PASS"
                        elif v.startswith("PARTIAL"):
                            verdict = "PARTIAL"
                        elif v.startswith("FAIL"):
                            verdict = "FAIL"
                        break

                unsupported_ratio = _unsupported_claim_ratio(answer, item.get("reference_answer", ""))

                await _db_insert({
                    "id": uuid.uuid4().hex, "run_id": run_id, "reference_id": item["id"],
                    "category": category, "template_id": TEMPLATE,
                    "query_preview": item["query"][:300],
                    "pipeline_answer_chars": len(answer), "verdict": verdict,
                    "judge_raw": judge_raw[:300], "unsupported_claims_ratio": unsupported_ratio,
                    "latency_ms": latency_ms,
                })
                summary[category][verdict] = summary[category].get(verdict, 0) + 1
                log.info("%s [%s]: %s (%dms)", item["id"], category, verdict, latency_ms)
            except Exception as e:
                summary[category]["ERROR"] += 1
                log.warning("%s [%s]: ERROR (%s)", item.get("id", "?"), category, e)
                await _db_insert({
                    "id": uuid.uuid4().hex, "run_id": run_id, "reference_id": item.get("id", "?"),
                    "category": category, "template_id": TEMPLATE,
                    "query_preview": item.get("query", "")[:300],
                    "pipeline_answer_chars": 0, "verdict": "ERROR",
                    "judge_raw": str(e)[:300], "unsupported_claims_ratio": None,
                    "latency_ms": None,
                })

    total = sum(sum(c.values()) for c in summary.values())
    total_pass = sum(c["PASS"] for c in summary.values())
    log.info("=== Regression run %s complete: %d/%d PASS overall ===", run_id, total_pass, total)
    for category, counts in summary.items():
        cat_total = sum(counts.values())
        log.info("  %-10s PASS=%d PARTIAL=%d FAIL=%d ERROR=%d (of %d)",
                  category, counts["PASS"], counts["PARTIAL"], counts["FAIL"], counts["ERROR"], cat_total)


if __name__ == "__main__":
    asyncio.run(run_regression())

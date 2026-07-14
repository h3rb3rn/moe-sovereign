"""
services/quality_probe.py — Online A/B: pipeline answer vs. single best expert.

Fire-and-forget probe, sampled (MOE_QUALITY_SAMPLE_RATE, default 5%). The same
user prompt is answered by the single strongest expert; a blind judge call
decides which answer is better. Results accumulate in pipeline_quality_log and
answer the core question: does the pipeline overhead buy quality?

Flag: MOE_QUALITY_PROBE=1 (default off).
"""

import asyncio
import json
import logging
import os
import random
import time
import uuid

import httpx

logger = logging.getLogger("MOE-SOVEREIGN")

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_quality_log (
    id            TEXT PRIMARY KEY,
    created_at    TIMESTAMPTZ DEFAULT now(),
    request_id    TEXT,
    user_id       TEXT,
    template_id   TEXT,
    query_preview TEXT,
    pipeline_answer_chars INT,
    baseline_model TEXT,
    baseline_answer_chars INT,
    winner        TEXT,          -- 'pipeline' | 'baseline' | 'tie' | 'error'
    judge_raw     TEXT,
    baseline_tokens INT,
    judge_tokens  INT
);
"""

_JUDGE_PROMPT = """You are a strict evaluator. Two answers (A and B) to the same user request follow.
Decide which answer is better on correctness, completeness and usefulness.
Respond ONLY with one word: A, B, or TIE.

## User request
{query}

## Answer A
{a}

## Answer B
{b}
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
                    f"INSERT INTO pipeline_quality_log ({cols}) VALUES ({ph})",
                    list(row.values()),
                )
            await conn.commit()
    except Exception as e:
        logger.warning("quality_probe: DB insert failed: %s", e)


def _pick_baseline_expert(experts: dict) -> dict | None:
    """Strongest single expert: first entry of 'general', else 'reasoning', else any."""
    for cat in ("general", "reasoning", "code"):
        lst = (experts or {}).get(cat) or []
        for e in lst:
            if e.get("model") and e.get("url"):
                return e
    for lst in (experts or {}).values():
        if isinstance(lst, list):
            for e in lst:
                if e.get("model") and e.get("url"):
                    return e
    return None


async def _ollama_generate(url: str, token: str, model: str, prompt: str,
                           num_ctx: int = 32768, timeout: float = 300.0) -> tuple:
    base = url.rstrip("/").removesuffix("/v1")
    payload = {
        "model": model, "stream": False, "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"num_ctx": num_ctx, "num_predict": 4096},
        # No explicit keep_alive — respects each Ollama instance's own
        # server-configured OLLAMA_KEEP_ALIVE default instead of silently
        # overriding it.
    }
    async with httpx.AsyncClient(timeout=timeout) as cl:
        r = await cl.post(f"{base}/api/chat", json=payload,
                          headers={"Authorization": f"Bearer {token}"})
        j = r.json()
    return (j.get("message", {}).get("content", "") or "", int(j.get("eval_count", 0)))


async def run_probe(query: str, pipeline_answer: str, experts: dict,
                    planner_cfg: dict, request_id: str, user_id: str,
                    template_id: str = "") -> None:
    """Fire-and-forget entry point. Call via asyncio.create_task()."""
    try:
        if os.getenv("MOE_QUALITY_PROBE", "0") != "1":
            return
        rate = float(os.getenv("MOE_QUALITY_SAMPLE_RATE", "0.05"))
        if random.random() > rate:
            return
        if not query or not pipeline_answer:
            return
        exp = _pick_baseline_expert(experts)
        if not exp:
            return
        t0 = time.monotonic()
        baseline, base_tok = await _ollama_generate(
            exp["url"], exp.get("token", "ollama"), exp["model"], query,
            num_ctx=int(exp.get("context_window") or 32768),
        )
        if not baseline.strip():
            return
        # Blind judging: randomize A/B assignment to avoid position bias.
        flip = random.random() < 0.5
        a, b = (pipeline_answer, baseline) if not flip else (baseline, pipeline_answer)
        judge_model = (planner_cfg or {}).get("judge_model_override") or exp["model"]
        judge_url   = (planner_cfg or {}).get("judge_url_override") or exp["url"]
        judge_tok   = (planner_cfg or {}).get("judge_token_override") or exp.get("token", "ollama")
        verdict_raw, judge_tokens = await _ollama_generate(
            judge_url, judge_tok, judge_model,
            _JUDGE_PROMPT.format(query=query[:4000], a=a[:6000], b=b[:6000]),
        )
        v = verdict_raw.strip().upper()[:3]
        if v.startswith("A"):
            winner = "pipeline" if not flip else "baseline"
        elif v.startswith("B"):
            winner = "baseline" if not flip else "pipeline"
        elif v.startswith("TIE"):
            winner = "tie"
        else:
            winner = "error"
        await _db_insert({
            "id": uuid.uuid4().hex, "request_id": request_id, "user_id": user_id,
            "template_id": template_id, "query_preview": query[:300],
            "pipeline_answer_chars": len(pipeline_answer),
            "baseline_model": f'{exp["model"]}@{exp.get("endpoint", "?")}',
            "baseline_answer_chars": len(baseline), "winner": winner,
            "judge_raw": verdict_raw[:200], "baseline_tokens": base_tok,
            "judge_tokens": judge_tokens,
        })
        logger.info(
            "quality_probe: winner=%s baseline=%s dur=%.0fs (request %s)",
            winner, exp["model"], time.monotonic() - t0, request_id,
        )
    except Exception as e:
        logger.warning("quality_probe failed (non-fatal): %s", e)

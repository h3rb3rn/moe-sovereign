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

# Additive schema migration for columns introduced after the table already
# existed in production — CREATE TABLE IF NOT EXISTS above won't add columns
# to an existing table, so this runs alongside it on every insert (no-op
# after the first run, same idempotency principle as the rest of this file).
_MIGRATE_SQL = """
ALTER TABLE pipeline_quality_log ADD COLUMN IF NOT EXISTS baseline_cost_eur DOUBLE PRECISION;
ALTER TABLE pipeline_quality_log ADD COLUMN IF NOT EXISTS pipeline_cost_eur DOUBLE PRECISION;
ALTER TABLE pipeline_quality_log ADD COLUMN IF NOT EXISTS pipeline_tokens_est INT;
ALTER TABLE pipeline_quality_log ADD COLUMN IF NOT EXISTS pipeline_tokens_source TEXT;
ALTER TABLE pipeline_quality_log ADD COLUMN IF NOT EXISTS unsupported_claims_ratio DOUBLE PRECISION;
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
                await cur.execute(_MIGRATE_SQL)
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
    # Never downgrade a warm model: llama-server can't resize a running
    # instance's context, so requesting a smaller ctx than what's already
    # loaded forces a full unload+reload (tens of seconds, disruptive to
    # every other caller of that node). This probe is a fire-and-forget,
    # sampled, non-critical call — it must never be the reason a shared
    # expert model gets evicted/reloaded. Confirmed live: this exact path
    # (default num_ctx=32768) was reloading an already-262144-loaded
    # qwen3.6:35b on N04-RTX back down, immediately followed by the next
    # real request reloading it back up.
    try:
        async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
            _ps_r = await _ps_cl.get(
                f"{base}/api/ps", headers={"Authorization": f"Bearer {token}"},
            )
            for _loaded in _ps_r.json().get("models", []):
                _lname = _loaded.get("name", "").split(":")[0]
                _mname = model.split(":")[0]
                _loaded_ctx = _loaded.get("context_length", 0)
                if _lname == _mname and _loaded_ctx >= num_ctx:
                    num_ctx = _loaded_ctx
                    break
    except Exception:
        pass  # non-fatal — fall through with the originally requested ctx
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
                    template_id: str = "", pipeline_tokens: int = 0,
                    graph_context: str = "", web_research: str = "",
                    mcp_result: str = "") -> None:
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
        _baseline_ctx = int(exp.get("context_window") or 32768)
        baseline, base_tok = await _ollama_generate(
            exp["url"], exp.get("token", "ollama"), exp["model"], query,
            num_ctx=_baseline_ctx,
        )
        if not baseline.strip():
            return
        # Blind judging: randomize A/B assignment to avoid position bias.
        flip = random.random() < 0.5
        a, b = (pipeline_answer, baseline) if not flip else (baseline, pipeline_answer)
        judge_model = (planner_cfg or {}).get("judge_model_override") or exp["model"]
        judge_url   = (planner_cfg or {}).get("judge_url_override") or exp["url"]
        judge_tok   = (planner_cfg or {}).get("judge_token_override") or exp.get("token", "ollama")
        # Explicitly reuse the baseline call's ctx rather than falling back to
        # _ollama_generate's hardcoded 32768 default. Confirmed live: when no
        # judge_model_override is set (the common case), judge_model/judge_url
        # are the SAME model+node the baseline call just used — but the
        # in-function /api/ps reuse-check can race the baseline call's own
        # (possibly still in-progress) load, so this judge call landed on a
        # fresh 32768 load moments after the baseline call had already loaded
        # the same model at 262144, forcing an avoidable reload cycle on
        # qwen3.6:35b@N04-RTX. Only fall back to the model's own configured
        # context_window when the judge targets a genuinely different model/
        # node (an actual override).
        _judge_ctx = _baseline_ctx if judge_model == exp["model"] and judge_url == exp["url"] \
            else int((planner_cfg or {}).get("judge_context_window") or 32768)
        verdict_raw, judge_tokens = await _ollama_generate(
            judge_url, judge_tok, judge_model,
            _JUDGE_PROMPT.format(query=query[:4000], a=a[:6000], b=b[:6000]),
            num_ctx=_judge_ctx,
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

        # Cost estimate (synthetic — MoE Sovereign has no real per-model $
        # pricing table; local experts are self-hosted Ollama, so this is a
        # rough compute-cost proxy using the same flat rate already shown to
        # users in admin_ui/templates/users.html, not a real API price).
        _price = float(os.getenv("TOKEN_PRICE_EUR", "0.00002"))
        _ptok, _psrc = (pipeline_tokens, "exact") if pipeline_tokens else (len(pipeline_answer) // 4, "estimated")
        baseline_cost_eur = round(base_tok * _price, 6)
        pipeline_cost_eur = round(_ptok * _price, 6)

        # Objective claim check as a supplement to the blind LLM judge verdict
        # above (does not alter winner/judge_raw, only adds a second signal).
        from services.trust_score import _unsupported_claim_ratio
        _combined_sources = "\n".join([graph_context, web_research, mcp_result])
        unsupported_ratio = _unsupported_claim_ratio(pipeline_answer, _combined_sources)

        await _db_insert({
            "id": uuid.uuid4().hex, "request_id": request_id, "user_id": user_id,
            "template_id": template_id, "query_preview": query[:300],
            "pipeline_answer_chars": len(pipeline_answer),
            "baseline_model": f'{exp["model"]}@{exp.get("endpoint", "?")}',
            "baseline_answer_chars": len(baseline), "winner": winner,
            "judge_raw": verdict_raw[:200], "baseline_tokens": base_tok,
            "judge_tokens": judge_tokens,
            "baseline_cost_eur": baseline_cost_eur, "pipeline_cost_eur": pipeline_cost_eur,
            "pipeline_tokens_est": _ptok, "pipeline_tokens_source": _psrc,
            "unsupported_claims_ratio": unsupported_ratio,
        })
        logger.info(
            "quality_probe: winner=%s baseline=%s dur=%.0fs (request %s)",
            winner, exp["model"], time.monotonic() - t0, request_id,
        )
    except Exception as e:
        logger.warning("quality_probe failed (non-fatal): %s", e)

"""
services/tracking.py — Request lifecycle tracking and user budget management.

All functions are fire-and-forget async helpers:
  - Usage logging to Postgres (usage_log table)
  - Active request registration in Redis (live monitoring)
  - User token budget increments in Redis
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import state
from config import HISTORY_MAX_ENTRIES

logger = logging.getLogger("MOE-SOVEREIGN")


async def _log_usage_to_db(
    user_id: str, api_key_id: str, request_id: str,
    model: str, moe_mode: str,
    prompt_tokens: int, completion_tokens: int,
    status: str = "ok", session_id: str = None,
    latency_ms: Optional[int] = None,
    complexity_level: str = "",
    expert_domains: str = "",
    cache_hit: bool = False,
    agentic_rounds: int = 0,
) -> None:
    """Fire-and-forget Postgres usage log. Never raises exceptions."""
    try:
        if state._userdb_pool is None:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        async with state._userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO usage_log "
                    "(id,user_id,api_key_id,request_id,session_id,model,moe_mode,prompt_tokens,"
                    "completion_tokens,total_tokens,status,requested_at,"
                    "latency_ms,complexity_level,expert_domains,cache_hit,agentic_rounds) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (uuid.uuid4().hex, user_id, api_key_id or None, request_id,
                     session_id or None, model, moe_mode, prompt_tokens, completion_tokens,
                     prompt_tokens + completion_tokens, status, now_iso,
                     latency_ms, complexity_level or None, expert_domains or None,
                     cache_hit, agentic_rounds),
                )
                await cur.execute(
                    "UPDATE api_keys SET last_used_at=%s WHERE user_id=%s AND is_active=TRUE",
                    (now_iso, user_id),
                )
    except Exception as e:
        logger.warning("Usage log failed: %s", e)


async def _register_active_request(
    chat_id: str, user_id: str, model: str,
    moe_mode: str, req_type: str,
    template_name: str = "", client_ip: str = "",
    backend_model: str = "", backend_host: str = "",
    api_key_id: str = "",
) -> None:
    """Register a running request in Redis for live monitoring."""
    if state.redis_client is None:
        return
    try:
        _key_label = ""
        _key_prefix = ""
        if api_key_id and state._userdb_pool is not None:
            try:
                from psycopg.rows import dict_row
                async with state._userdb_pool.connection() as _conn:
                    async with _conn.cursor(row_factory=dict_row) as _cur:
                        await _cur.execute(
                            "SELECT label, key_prefix FROM api_keys WHERE id=%s",
                            (api_key_id,),
                        )
                        _row = await _cur.fetchone()
                        if _row:
                            _key_label  = _row["label"] or ""
                            _key_prefix = _row["key_prefix"] or ""
            except Exception:
                pass
        meta = {
            "chat_id":       chat_id,
            "user_id":       user_id,
            "model":         model,
            "moe_mode":      moe_mode,
            "type":          req_type,
            "template_name": template_name,
            "client_ip":     client_ip,
            "backend_model": backend_model,
            "backend_host":  backend_host,
            "api_key_id":    api_key_id,
            "key_label":     _key_label,
            "key_prefix":    _key_prefix,
            "started_at":    datetime.utcnow().isoformat() + "Z",
        }
        await state.redis_client.set(f"moe:active:{chat_id}", json.dumps(meta), ex=7200)
    except Exception as e:
        logger.debug("Active request registration failed: %s", e)


async def _deregister_active_request(chat_id: str) -> None:
    """Remove a completed request from Redis live monitoring and write to history."""
    if state.redis_client is None:
        return
    try:
        key = f"moe:active:{chat_id}"
        raw = await state.redis_client.get(key)
        if raw:
            try:
                meta = json.loads(raw)
                meta["status"]   = "completed"
                meta["ended_at"] = datetime.now(timezone.utc).isoformat()
                score = datetime.now(timezone.utc).timestamp()
                await state.redis_client.zadd(
                    "moe:admin:completed", {json.dumps(meta, default=str): score}
                )
                await state.redis_client.zremrangebyrank(
                    "moe:admin:completed", 0, -(HISTORY_MAX_ENTRIES + 1)
                )
            except Exception as _he:
                logger.warning("History entry failed: %s", _he)
        await state.redis_client.delete(key)
    except Exception as e:
        logger.debug("Active request deregister failed: %s", e)


async def _increment_user_budget(
    user_id: str, tokens: int,
    prompt_tokens: int = 0, completion_tokens: int = 0,
) -> None:
    """Increment Redis budget counters for a user. Fire-and-forget."""
    if not user_id or user_id == "anon" or state.redis_client is None:
        return
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    month = date.today().strftime("%Y-%m")
    try:
        cost_factor = 1.0
        cf_raw = await state.redis_client.get(f"user:{user_id}:cost_factor")
        if cf_raw:
            cost_factor = float(cf_raw)
        effective   = max(1, round(tokens * cost_factor))
        eff_prompt  = round(prompt_tokens * cost_factor)
        eff_out     = round(completion_tokens * cost_factor)
        pipe = state.redis_client.pipeline()
        pipe.incrby(f"user:{user_id}:tokens:daily:{today}", effective)
        pipe.expire(f"user:{user_id}:tokens:daily:{today}", 48 * 3600)
        pipe.incrby(f"user:{user_id}:tokens:monthly:{month}", effective)
        pipe.expire(f"user:{user_id}:tokens:monthly:{month}", 35 * 86400)
        pipe.incrby(f"user:{user_id}:tokens:total", effective)
        if eff_prompt > 0:
            pipe.incrby(f"user:{user_id}:tokens:daily:{today}:input", eff_prompt)
            pipe.expire(f"user:{user_id}:tokens:daily:{today}:input", 48 * 3600)
            pipe.incrby(f"user:{user_id}:tokens:monthly:{month}:input", eff_prompt)
            pipe.expire(f"user:{user_id}:tokens:monthly:{month}:input", 35 * 86400)
            pipe.incrby(f"user:{user_id}:tokens:total:input", eff_prompt)
        if eff_out > 0:
            pipe.incrby(f"user:{user_id}:tokens:daily:{today}:output", eff_out)
            pipe.expire(f"user:{user_id}:tokens:daily:{today}:output", 48 * 3600)
            pipe.incrby(f"user:{user_id}:tokens:monthly:{month}:output", eff_out)
            pipe.expire(f"user:{user_id}:tokens:monthly:{month}:output", 35 * 86400)
            pipe.incrby(f"user:{user_id}:tokens:total:output", eff_out)
        await pipe.execute()
    except Exception as e:
        logger.warning("Budget counter failed: %s", e)
    if state._userdb_pool is not None:
        try:
            from admin_ui.database import get_user_teams as _get_user_teams
            from admin_ui.database import deduct_team_budget as _deduct_team_budget
            for _team_id in await _get_user_teams(user_id):
                await _deduct_team_budget(_team_id, effective)
        except Exception as _tbe:
            logger.debug("Team budget deduct failed: %s", _tbe)

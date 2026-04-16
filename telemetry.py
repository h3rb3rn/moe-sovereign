"""Routing telemetry — async fire-and-forget recording of routing decisions.

Writes to ``routing_telemetry`` in the moe_userdb PostgreSQL database.
All functions are safe to call in the hot path: errors are logged and
swallowed, never propagated to the caller.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("moe.telemetry")

_CODE_PATTERN = re.compile(
    r"```|def |class |import |function |SELECT |CREATE |const |var |let ",
    re.IGNORECASE,
)

_INSERT_SQL = """
INSERT INTO routing_telemetry (
    response_id, user_id, template_name,
    prompt_length, prompt_lang, complexity,
    has_images, has_code,
    planner_plan, experts_used, mcp_tools_used,
    cache_hit, fast_path,
    self_score, judge_refined, correction_applied,
    total_tokens, wall_clock_ms, expert_scores
) VALUES (
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s
)
"""

_UPDATE_RATING_SQL = """
UPDATE routing_telemetry SET user_rating = %s WHERE response_id = %s
"""

_UPDATE_SELF_SCORE_SQL = """
UPDATE routing_telemetry SET self_score = %s WHERE response_id = %s
"""


def _detect_language(text: str) -> str:
    german_markers = ("der ", "die ", "das ", "und ", "ist ", "für ", "ein ",
                      "nicht ", "auf ", "mit ", "von ", "den ", "dem ")
    sample = text[:500].lower()
    hits = sum(1 for m in german_markers if m in sample)
    return "de" if hits >= 3 else "en"


def _has_code(text: str) -> bool:
    return bool(_CODE_PATTERN.search(text[:2000]))


async def record_routing_decision(
    pool,
    response_id: str,
    state: dict[str, Any],
    expert_scores: Optional[dict] = None,
    wall_clock_ms: int = 0,
) -> None:
    """Record a completed routing decision. Call after merger/response."""
    if pool is None:
        return
    try:
        user_input = state.get("input", "")
        experts_used = state.get("expert_models_used", [])
        plan_raw = state.get("planner_plan") or state.get("planned_tasks") or []
        mcp_tools = state.get("mcp_tools_used", [])

        async with pool.connection() as conn:
            await conn.execute(
                _INSERT_SQL,
                (
                    response_id,
                    state.get("user_id", ""),
                    state.get("template_name", ""),
                    len(user_input),
                    _detect_language(user_input),
                    state.get("complexity_level", ""),
                    bool(state.get("images")),
                    _has_code(user_input),
                    json.dumps(plan_raw, ensure_ascii=False) if plan_raw else None,
                    experts_used if experts_used else None,
                    mcp_tools if mcp_tools else None,
                    bool(state.get("cache_hit")),
                    bool(state.get("fast_path")),
                    None,
                    bool(state.get("judge_refined")),
                    bool(state.get("correction_applied")),
                    state.get("prompt_tokens", 0) + state.get("completion_tokens", 0),
                    wall_clock_ms,
                    json.dumps(expert_scores, ensure_ascii=False) if expert_scores else None,
                ),
            )
    except Exception as exc:
        logger.debug("telemetry insert failed: %s", exc)


async def record_user_feedback(pool, response_id: str, rating: int) -> None:
    """Update the user_rating column for an existing telemetry row."""
    if pool is None:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(_UPDATE_RATING_SQL, (rating, response_id))
    except Exception as exc:
        logger.debug("telemetry rating update failed: %s", exc)


async def record_self_score(pool, response_id: str, score: int) -> None:
    """Update the self_score column after async self-evaluation completes."""
    if pool is None:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(_UPDATE_SELF_SCORE_SQL, (score, response_id))
    except Exception as exc:
        logger.debug("telemetry self_score update failed: %s", exc)

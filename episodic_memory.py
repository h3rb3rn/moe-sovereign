"""
episodic_memory.py — Episodic memory for MoE Sovereign.

Logs successful task completions to Neo4j (:Episode nodes) and retrieves
routing hints from past episodes to guide the planner on similar queries.

Scientific basis:
  Tulving (1972): episodic vs. semantic memory distinction.
  Park et al. (2023): Generative Agents — episodic memory streams for LLM agents.
  Packer et al. (2023): MemGPT — tiered memory for LLM-as-OS architecture.

What this module does NOT do:
  - It does NOT store conversation turns (that is memory_retrieval.py / ChromaDB).
  - It does NOT store corrections (that is graph_rag/corrections.py / Neo4j).
  - It logs task-level outcomes: query intent, routing path, tools, confidence.

Storage: Neo4j (:Episode nodes). Requires graph_manager.driver.
Lookup: Called from graph_rag_node — appends "[Episode Hint]" to graph_context
        when a strongly-similar past episode exists.

Environment:
  EPISODIC_MEMORY_ENABLED       Set to "0" to disable (default: enabled).
  EPISODIC_MAX_HINTS            Max past episodes to inject as hints (default: 2).
  EPISODIC_MIN_CONFIDENCE       Minimum stored episode confidence to recall (default: 0.6).
  EPISODIC_TTL_DAYS             Days before episodes expire (default: 90).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN.episodic_memory")

_ENABLED         = os.getenv("EPISODIC_MEMORY_ENABLED", "1") not in ("0", "false", "no")
_MAX_HINTS       = int(os.getenv("EPISODIC_MAX_HINTS", "2"))
_MIN_CONFIDENCE  = float(os.getenv("EPISODIC_MIN_CONFIDENCE", "0.6"))
_TTL_DAYS        = int(os.getenv("EPISODIC_TTL_DAYS", "90"))

# ── Schema ────────────────────────────────────────────────────────────────────

_ENSURE_SCHEMA = """
CREATE CONSTRAINT episode_hash_unique IF NOT EXISTS
  FOR (e:Episode) REQUIRE e.hash IS UNIQUE
"""

# ── Cypher queries ────────────────────────────────────────────────────────────

_STORE_EPISODE = """
MERGE (ep:Episode {hash: $hash})
ON CREATE SET
  ep.query_pattern    = $query_pattern,
  ep.task_type        = $task_type,
  ep.routing_path     = $routing_path,
  ep.tools_used       = $tools_used,
  ep.model_signature  = $model_signature,
  ep.confidence       = $confidence,
  ep.total_tokens     = $total_tokens,
  ep.recall_count     = 0,
  ep.created          = datetime(),
  ep.expires_at       = $expires_at,
  ep.user_id          = $user_id
ON MATCH SET
  ep.recall_count = ep.recall_count + 1,
  ep.confidence   = CASE WHEN $confidence > ep.confidence THEN $confidence ELSE ep.confidence END,
  ep.last_seen    = datetime()
RETURN ep.hash AS hash
"""

_QUERY_EPISODES = """
MATCH (ep:Episode)
WHERE ep.task_type = $task_type
  AND ep.confidence >= $min_confidence
  AND ep.expires_at > $now
WITH ep,
     apoc.text.sorensenDiceSimilarity(
         toLower(ep.query_pattern), toLower($query_pattern)
     ) AS sim
WHERE sim > $min_sim
RETURN
    ep.routing_path   AS routing_path,
    ep.tools_used     AS tools_used,
    ep.model_signature AS model_sig,
    ep.confidence     AS confidence,
    ep.recall_count   AS recall_count,
    sim
ORDER BY sim DESC, ep.confidence DESC
LIMIT $limit
"""

# Fallback without APOC (uses recency instead of similarity)
_QUERY_EPISODES_FALLBACK = """
MATCH (ep:Episode)
WHERE ep.task_type = $task_type
  AND ep.confidence >= $min_confidence
  AND ep.expires_at > $now
RETURN
    ep.routing_path   AS routing_path,
    ep.tools_used     AS tools_used,
    ep.model_signature AS model_sig,
    ep.confidence     AS confidence,
    ep.recall_count   AS recall_count,
    0.5               AS sim
ORDER BY ep.created DESC
LIMIT $limit
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _episode_hash(query: str, task_type: str) -> str:
    """Stable hash for deduplication — same query intent + task type = same episode."""
    blob = f"{query[:200].lower().strip()}|{task_type}"
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _extract_task_type(plan: list[dict]) -> str:
    """Derive the primary task type from the planner's output.

    Uses the first plan step's category. Falls back to "general" when the
    plan is empty or malformed — ensures the field is always a non-empty string.
    """
    for step in plan:
        cat = step.get("category", "").strip()
        if cat:
            return cat
    return "general"


def _extract_tools_used(state: dict) -> list[str]:
    """Determine which pipeline tools were active for this request."""
    tools = []
    if state.get("graph_context"):
        tools.append("graphrag")
    if state.get("mcp_result"):
        tools.append("mcp")
    if state.get("math_result"):
        tools.append("math")
    if state.get("web_research"):
        tools.append("web")
    if state.get("cached_facts"):
        tools.append("cache")
    return tools


def _extract_confidence(state: dict) -> float:
    """Estimate episode quality from expert results and final response length.

    Uses a simple heuristic: average expert confidence + response completeness.
    No LLM call — must be fast for the fire-and-forget log path.
    """
    from parsing import _parse_expert_confidence
    conf_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
    confs = [
        conf_map.get(_parse_expert_confidence(r), 0.5)
        for r in state.get("expert_results", [])
    ]
    expert_conf = sum(confs) / len(confs) if confs else 0.5
    # Reward longer, more complete answers (heuristic: >200 chars = good)
    response_len = len(state.get("final_response", ""))
    completeness = min(1.0, response_len / 600)
    return round(expert_conf * 0.7 + completeness * 0.3, 3)


def _normalise_query(query: str) -> str:
    """Reduce query to a normalised pattern for similarity matching."""
    q = re.sub(r'\s+', ' ', query.lower().strip()).rstrip("?!.,;")
    return q[:300]


# ── Public API ────────────────────────────────────────────────────────────────

async def ensure_schema(driver) -> None:
    """Create Episode uniqueness constraint if it does not exist."""
    async with driver.session() as session:
        try:
            await session.run(_ENSURE_SCHEMA)
        except Exception as exc:
            logger.debug("Episodic memory schema setup: %s", exc)


async def log_episode(driver, state: dict) -> None:
    """Persist a completed task as an Episode node in Neo4j.

    Called as a fire-and-forget task from synthesis.py after the merger
    produces a final response. Never raises — all errors are logged and
    swallowed to avoid interrupting the request pipeline.

    Args:
        driver: Neo4j async driver (graph_manager.driver).
        state:  Full AgentState dict after merger completion.
    """
    if not _ENABLED or driver is None:
        return

    plan        = state.get("plan", [])
    task_type   = _extract_task_type(plan)
    query_pat   = _normalise_query(state.get("input", ""))
    ep_hash     = _episode_hash(query_pat, task_type)
    confidence  = _extract_confidence(state)

    # Only persist episodes with meaningful quality signal.
    if confidence < 0.3:
        logger.debug("Episodic memory: skipping low-confidence episode (%.2f)", confidence)
        return

    routing_path   = [s.get("category", "") for s in plan if s.get("category")]
    tools_used     = _extract_tools_used(state)
    model_sig      = sorted(set(state.get("expert_models_used", [])))
    total_tokens   = state.get("prompt_tokens", 0) + state.get("completion_tokens", 0)
    expires_at_iso = datetime.fromtimestamp(
        time.time() + _TTL_DAYS * 86400, tz=timezone.utc
    ).isoformat()

    try:
        async with driver.session() as session:
            await session.run(
                _STORE_EPISODE,
                {
                    "hash":           ep_hash,
                    "query_pattern":  query_pat,
                    "task_type":      task_type,
                    "routing_path":   routing_path,
                    "tools_used":     tools_used,
                    "model_signature": model_sig,
                    "confidence":     confidence,
                    "total_tokens":   total_tokens,
                    "expires_at":     expires_at_iso,
                    "user_id":        state.get("user_id", "anon"),
                },
            )
        logger.debug(
            "Episodic memory: logged episode task_type=%r confidence=%.2f hash=%s",
            task_type, confidence, ep_hash,
        )
    except Exception as exc:
        logger.warning("Episodic memory: log_episode failed: %s", exc)


async def get_episode_hint(driver, query: str, task_type: str) -> str:
    """Return a formatted hint block from similar past episodes, or "".

    Injected into graph_context alongside Neo4j results so the judge model
    can take past successful routing strategies into account.

    Args:
        driver:    Neo4j async driver.
        query:     Current user query.
        task_type: Primary task category from the current plan.

    Returns:
        Formatted "[Episode Hint]" string, or "" if no relevant episodes found.
    """
    if not _ENABLED or driver is None:
        return ""

    query_pat = _normalise_query(query)
    now_iso   = datetime.now(timezone.utc).isoformat()

    rows = []
    try:
        async with driver.session() as session:
            try:
                result = await session.run(
                    _QUERY_EPISODES,
                    {
                        "task_type":      task_type,
                        "query_pattern":  query_pat,
                        "min_confidence": _MIN_CONFIDENCE,
                        "min_sim":        0.4,
                        "now":            now_iso,
                        "limit":          _MAX_HINTS,
                    },
                )
                rows = [r.data() async for r in result]
            except Exception:
                # APOC not available — use recency fallback
                result = await session.run(
                    _QUERY_EPISODES_FALLBACK,
                    {
                        "task_type":      task_type,
                        "min_confidence": _MIN_CONFIDENCE,
                        "now":            now_iso,
                        "limit":          _MAX_HINTS,
                    },
                )
                rows = [r.data() async for r in result]
    except Exception as exc:
        logger.debug("Episodic memory: get_episode_hint failed: %s", exc)
        return ""

    if not rows:
        return ""

    lines = ["[Episode Hint — past similar tasks]"]
    for row in rows:
        routing  = " → ".join(row.get("routing_path") or [])
        tools    = ", ".join(row.get("tools_used") or []) or "none"
        conf     = row.get("confidence", 0.0)
        recalls  = row.get("recall_count", 0)
        lines.append(
            f"• Routing: {routing} | Tools: {tools} "
            f"| Confidence: {conf:.0%} | Recalled {recalls}×"
        )
    lines.append("[End of Episode Hint]")

    hint = "\n".join(lines)
    logger.debug("Episodic memory: injecting hint (%d episodes, %d chars)", len(rows), len(hint))
    return hint

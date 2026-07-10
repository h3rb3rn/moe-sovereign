"""
services/agent_enrichment.py — Augmented Tool Path for agentic clients.

Agentic tools (Claude Code CLI via /v1/messages, OpenCode via /v1/chat/completions)
always ship a `tools` array / tool_result turns. The tool-call fast path
(_anthropic_tool_handler in services/pipeline/anthropic.py, _handle_tool_calls
in services/pipeline/chat.py) forwards these straight to the configured tool
model, bypassing the MoE pipeline entirely — no L0/L1 cache, no GraphRAG
context, no knowledge ingestion.

This module holds the shared, pipeline-agnostic logic for the opt-in
enrichment layer that sits between those handlers and the tool model:
turn classification, cache read/write, and GraphRAG context injection.
Every public function is safe to call unconditionally — all I/O is wrapped
with timeouts and falls back to a no-op on error, so the tool path degrades
to today's plain passthrough whenever a flag is off or an enrichment call
fails.

Design reference: UMSETZUNGSPLAN_AGENT_ENRICHMENT_2026-07-09.md

All flags governing whether these functions are actually invoked live in
config.py (AGENT_CACHE_*, AGENT_GRAPHRAG_*, AGENT_INGEST_*) and are resolved
per CC profile / expert template — see services/pipeline/cc_session.py and
services/templates.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from config import (
    AGENT_CACHE_L0_TTL, AGENT_CACHE_MAX_LOOKUP_MS, AGENT_CACHE_MIN_CONF,
    AGENT_CACHE_TTL_DAYS, AGENT_INGEST_JUDGE, CACHE_MIN_RESPONSE_LEN,
    KAFKA_TOPIC_INGEST, KNOWLEDGE_BYPASS_THRESHOLD,
)
from metrics import PROM_AGENT_CACHE, PROM_AGENT_WRITEBACK
from services.helpers import _entry_is_fresh
from services.kafka import _kafka_publish

logger = logging.getLogger("MOE-SOVEREIGN")

# Same markers/threshold convention as graph/synthesis.py::merger_node and
# services/helpers.py::_self_evaluate — kept in sync deliberately so agent
# write-backs and interactive-pipeline write-backs classify consistently.
_PROC_MARKERS = {
    "requires", "must", "necessary", "prerequisite", "needed",
    "location", "on-site", "on premises", "physically", "necessitates",
}
_JUDGE_PROMOTE_SCORE_MIN = 4   # 1-5 scale; unlocks cache-serving confidence
_JUDGE_FLAG_SCORE_MAX = 2      # 1-5 scale; marks the entry bad (never served)
_AGENT_CONFIDENCE_INITIAL = 0.6   # below AGENT_CACHE_MIN_CONF (0.85) — not servable yet
_AGENT_CONFIDENCE_PROMOTED = 0.9  # above AGENT_CACHE_MIN_CONF — servable after judge OK


# ── Turn classification ───────────────────────────────────────────────────────

@dataclass
class TurnInfo:
    kind: str          # "initial_task" | "mid_loop"
    query: str          # last user text (best-effort extraction)
    scope: str          # sha256(f"{user_id}|{workspace}")[:16]
    cacheable: bool     # kind == "initial_task" AND is_informational(query)


def _field(msg, name: str, default=None):
    """Read `name` off a message that may be a dict or a pydantic-style object."""
    if isinstance(msg, dict):
        return msg.get(name, default)
    return getattr(msg, name, default)


def _has_tool_result_block(content) -> bool:
    """Anthropic content is either a string or a list of typed content blocks."""
    if not isinstance(content, list):
        return False
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type == "tool_result":
            return True
    return False


def _last_user_text(messages, api: str) -> str:
    """Best-effort extraction of the most recent user-authored text turn."""
    for msg in reversed(list(messages or [])):
        role = _field(msg, "role")
        if role != "user":
            continue
        content = _field(msg, "content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                if block_type == "text":
                    text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
                    if text:
                        parts.append(text)
            if parts:
                return "\n".join(parts)
    return ""


def classify_turn(messages, tools, api: str, user_id: str = "", system_text: str = "") -> TurnInfo:
    """Classify the current tool-path turn as 'initial_task' or 'mid_loop'.

    initial_task: `tools` present AND no message carries a tool result yet
                  (Anthropic: a `tool_result` content block; OpenAI: role=="tool").
    mid_loop:     at least one tool result is already present in the transcript.

    Tool_use/tool_call decisions themselves are never classified as cacheable —
    only a genuine initial informational question is (see is_informational()).
    """
    messages = list(messages or [])
    if api == "anthropic":
        has_tool_result = any(
            _has_tool_result_block(_field(m, "content"))
            for m in messages
        )
    else:  # "openai"
        has_tool_result = any(_field(m, "role") == "tool" for m in messages)

    kind = "mid_loop" if has_tool_result else "initial_task"
    query = _last_user_text(messages, api)
    workspace = extract_workspace(system_text, messages)
    scope = make_scope(user_id, workspace)
    cacheable = kind == "initial_task" and bool(query) and is_informational(query)
    return TurnInfo(kind=kind, query=query, scope=scope, cacheable=cacheable)


# ── Cacheability heuristic ────────────────────────────────────────────────────

_QUESTION_WORDS = re.compile(
    r"^\s*(what|why|how|where|which|who|when|explain|describe"
    r"|was|warum|wie|wo|welche[rs]?|wer|wann|erkl[aä]re|beschreibe)\b",
    re.IGNORECASE,
)

_MUTATION_WORDS = re.compile(
    r"\b(fix|implement|refactor|write|create|delete|remove|edit|add|install"
    r"|deploy|update|upgrade|migrate|rename|move|build"
    r"|[aä]ndere|schreibe|erstelle|l[oö]sche|entferne|bearbeite|f[uü]ge"
    r"|installiere|deploye|aktualisiere|migriere|benenne|baue|behebe|implementiere)\b",
    re.IGNORECASE,
)


def is_informational(query: str) -> bool:
    """Conservative gate: True only for genuine informational questions.

    Requires question form (trailing '?' or a leading question word) AND the
    absence of any file-mutation imperative — "how do I fix ..." is treated
    as a mutation task, not an informational query, because the mutation verb
    signals the client expects a tool_use action, not a cacheable text answer.
    """
    if not query or not query.strip():
        return False
    stripped = query.strip()
    is_question = stripped.endswith("?") or bool(_QUESTION_WORDS.match(stripped))
    if not is_question:
        return False
    if _MUTATION_WORDS.search(stripped):
        return False
    return True


# ── Workspace / scope ─────────────────────────────────────────────────────────

_WORKSPACE_RE = re.compile(r"Working directory:\s*(\S+)", re.IGNORECASE)


def extract_workspace(system_text: str, messages=None) -> str:
    """Parse the CC/OpenCode workspace path out of the system prompt env block.

    Falls back to scanning the first user message (some clients embed the cwd
    there instead of in `system`), then to the literal "global" scope.
    """
    if system_text:
        m = _WORKSPACE_RE.search(system_text)
        if m:
            return m.group(1)
    for msg in list(messages or []):
        if _field(msg, "role") != "user":
            continue
        content = _field(msg, "content")
        text = content if isinstance(content, str) else ""
        if not text and isinstance(content, list):
            first_text_block = next(
                (b for b in content
                 if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "text"),
                None,
            )
            if first_text_block is not None:
                text = first_text_block.get("text", "") if isinstance(first_text_block, dict) \
                    else getattr(first_text_block, "text", "")
        m = _WORKSPACE_RE.search(text or "")
        if m:
            return m.group(1)
    return "global"


def make_scope(user_id: str, workspace: str) -> str:
    """Cache/graph scope key: same user + same workspace share knowledge,
    different workspaces (projects) never leak into each other."""
    basis = f"{user_id or 'anon'}|{workspace or 'global'}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _l0_key(scope: str, query: str) -> str:
    """L0 exact-match cache key — same normalization as graph/router_nodes.py's
    interactive cache_lookup_node, but in the agent-cache namespace."""
    normalized = re.sub(r"\s+", " ", query.lower().strip().rstrip("?!.,;"))
    q_hash = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return f"moe:agent:qcache:{scope}:{q_hash}"


# ── Cache / GraphRAG / write-back ─────────────────────────────────────────────
# agent_graph_context (AP4) and agent_writeback (AP5) are fully implemented
# above. agent_cache_lookup remains a stub until AP6 (Cache-Lesepfad) — kept
# here so callers already wired up have a stable import surface; it is a safe
# no-op (never raises, never blocks) until then.

async def agent_cache_lookup(
    query: str, scope: str, redis_client, collection, path: str = "anthropic",
) -> str | None:
    """Look up a cached final answer for an informational initial-task turn.

    Callers must only call this when TurnInfo.cacheable is True (initial_task
    + is_informational) and the CC profile / expert template does not force
    tool_choice='required' — this function does not re-check either.

    L0 (Valkey exact-match) is checked first, then L1 (ChromaDB semantic,
    scoped to `scope` via a metadata filter — never crosses workspaces/users).
    The L1 gate mirrors the interactive knowledge-bypass in
    graph/router_nodes.py::cache_lookup_node: nearest-neighbour distance <
    KNOWLEDGE_BYPASS_THRESHOLD, stored confidence >= AGENT_CACHE_MIN_CONF,
    entry fresh within AGENT_CACHE_TTL_DAYS, and not flagged. Only the single
    nearest non-flagged neighbour is evaluated (same behaviour as the
    interactive path) — a near-miss on the closest entry is a miss, not a
    fall-through to the second-nearest.

    The whole lookup (both layers) is time-boxed to AGENT_CACHE_MAX_LOOKUP_MS;
    on timeout or any error this returns None (miss) — never raises, never
    blocks the tool-call fast path.
    """
    path_label = path if path in ("anthropic", "openai") else "anthropic"
    if not query or collection is None:
        PROM_AGENT_CACHE.labels(path=path_label, result="miss").inc()
        return None
    try:
        result = await asyncio.wait_for(
            _agent_cache_lookup_impl(query, scope, redis_client, collection),
            timeout=AGENT_CACHE_MAX_LOOKUP_MS / 1000,
        )
    except asyncio.TimeoutError:
        logger.debug("agent_cache_lookup: timed out after %d ms", AGENT_CACHE_MAX_LOOKUP_MS)
        PROM_AGENT_CACHE.labels(path=path_label, result="timeout").inc()
        return None
    except Exception as e:
        logger.debug("agent_cache_lookup: lookup failed: %s", e)
        PROM_AGENT_CACHE.labels(path=path_label, result="miss").inc()
        return None
    PROM_AGENT_CACHE.labels(path=path_label, result="hit" if result else "miss").inc()
    return result


async def _agent_cache_lookup_impl(query: str, scope: str, redis_client, collection) -> str | None:
    if redis_client:
        try:
            l0_hit = await redis_client.get(_l0_key(scope, query))
            if l0_hit:
                text = l0_hit if isinstance(l0_hit, str) else l0_hit.decode()
                if len(text) > 50:
                    return text
        except Exception as e:
            logger.debug("agent_cache_lookup: L0 check failed: %s", e)

    res = await asyncio.to_thread(
        collection.query, query_texts=[query], n_results=3, where={"scope": scope},
    )
    docs = (res.get("documents") or [[]])[0]
    if not docs:
        return None
    dists = (res.get("distances") or [[1.0] * len(docs)])[0]
    metas = (res.get("metadatas") or [[{}] * len(docs)])[0]
    for doc, dist, meta in zip(docs, dists, metas):
        if meta.get("flagged"):
            continue
        if (
            dist < KNOWLEDGE_BYPASS_THRESHOLD
            and float(meta.get("confidence", 0.0) or 0.0) >= AGENT_CACHE_MIN_CONF
            and _entry_is_fresh(meta.get("ts", ""), AGENT_CACHE_TTL_DAYS)
        ):
            return doc
        break  # only the nearest non-flagged neighbour is considered — matches
               # the interactive cache_lookup_node behaviour.
    return None


async def agent_graph_context(
    query: str, tenant_ids, session_id: str, redis_client, graph_manager,
    max_chars: int, timeout_s: float,
) -> str:
    """Fetch (or reuse cached) GraphRAG context for injection into the tool
    model's system prompt.

    Replaces the inline Tier-4 block previously duplicated in
    services/pipeline/anthropic.py, fixing three gaps in the original:
    tenant_ids is honoured (was always None), the L2 Valkey cache key is
    scoped by tenant (was query-only — cross-tenant leak), and the result is
    capped to max_chars (the caller is expected to pass
    min(AGENT_GRAPHRAG_MAX_CHARS, graphrag_budget_chars(tool_model, ...))).

    Only called on initial_task turns by the caller — mid_loop turns should
    instead re-read the per-session cc:graphctx:{session_id} Redis key that
    the caller writes after a successful call here, at zero Neo4j/L2 cost.
    Never raises; times out to "" so the tool path degrades to plain
    passthrough.
    """
    if not query or graph_manager is None:
        return ""

    tenant_key = ",".join(sorted(tenant_ids or []))
    cache_key = (
        "moe:graph:cc:"
        + hashlib.sha256(f"{tenant_key}|{query[:200]}".encode()).hexdigest()[:16]
    )

    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                text = cached if isinstance(cached, str) else cached.decode()
                return text[:max_chars] if max_chars > 0 else text
        except Exception as e:
            logger.debug("agent_graph_context: L2 cache read failed: %s", e)

    async def _fetch() -> str:
        ctx = await graph_manager.query_context(query[:500], ["general"], tenant_ids=tenant_ids) or ""
        try:
            from episodic_memory import get_episode_hint as _get_ep_hint
            hint = await _get_ep_hint(graph_manager.driver, query[:500], "general")
            if hint:
                ctx = (ctx + "\n\n" + hint) if ctx else hint
        except Exception as e:
            logger.debug("agent_graph_context: episode hint skipped: %s", e)
        return ctx

    try:
        ctx = await asyncio.wait_for(_fetch(), timeout=timeout_s)
    except Exception as e:
        logger.warning("agent_graph_context: query_context failed/timed out: %s", e)
        return ""

    if ctx and redis_client:
        try:
            asyncio.create_task(redis_client.setex(cache_key, 3600, ctx))
        except Exception:
            pass

    if max_chars > 0 and len(ctx) > max_chars:
        ctx = ctx[:max_chars]
    return ctx


async def agent_writeback(
    query: str, answer: str, scope: str, tenant_id: str, user_id: str,
    source_model: str, session_id: str, redis_client, collection,
    path: str = "anthropic",
) -> None:
    """Fire-and-forget write of a clean final agent answer into the agent
    cache (moe_agent_cache) and the Kafka ingestion topic.

    Callers must only invoke this for a genuinely clean session end (no
    stream error, no is_error tool_result, no tool_use/tool_calls in the
    final answer — see classify_turn) — this function itself does not
    re-derive that; it only re-checks the cheap length gate.

    Writes at initial confidence 0.6 — deliberately below AGENT_CACHE_MIN_CONF
    (0.85, see config.py), so a fresh answer is never immediately servable
    from the cache. If AGENT_INGEST_JUDGE is on, an async judge call scores
    the answer and either promotes the confidence to 0.9 (and writes the L0
    exact-match key, unlocking cache-serving) or flags the entry as bad.

    Never raises — every step is independently wrapped so a Kafka/Chroma/judge
    failure cannot affect the response already sent to the client. Always
    returns None; call via asyncio.create_task() for zero added latency.
    """
    path_label = path if path in ("anthropic", "openai") else "anthropic"
    if not query or not answer or len(answer) <= CACHE_MIN_RESPONSE_LEN or collection is None:
        PROM_AGENT_WRITEBACK.labels(path=path_label, result="skipped").inc()
        return None

    doc_id = hashlib.sha256(answer.encode()).hexdigest()[:32]
    metadata = {
        "scope": scope,
        "ts": datetime.now().isoformat(),
        "input": query[:500],
        "confidence": _AGENT_CONFIDENCE_INITIAL,
        "flagged": False,
        "session_id": session_id or "",
    }
    try:
        await asyncio.to_thread(
            collection.upsert, ids=[doc_id], documents=[answer], metadatas=[metadata],
        )
    except Exception as e:
        logger.warning("agent_writeback: L1 upsert failed: %s", e)
        PROM_AGENT_WRITEBACK.labels(path=path_label, result="error").inc()
        return None

    knowledge_type = "procedural" if any(kw in answer for kw in _PROC_MARKERS) else "factual"
    try:
        await _kafka_publish(KAFKA_TOPIC_INGEST, {
            "response_id":       session_id or "",
            "input":             query,
            "answer":            answer,
            "domain":            "technical_support",
            "source_expert":     "agent_session",
            "source_model":      source_model,
            "template_name":     "",
            "confidence":        _AGENT_CONFIDENCE_INITIAL,
            "knowledge_type":    knowledge_type,
            "synthesis_insight": None,
            "tenant_id":         tenant_id,
        })
    except Exception as e:
        logger.warning("agent_writeback: Kafka publish failed: %s", e)

    PROM_AGENT_WRITEBACK.labels(path=path_label, result="written").inc()

    if AGENT_INGEST_JUDGE:
        asyncio.create_task(
            _agent_judge_promote(query, answer, doc_id, metadata, scope, redis_client, collection)
        )
    return None


async def _agent_judge_promote(
    query: str, answer: str, doc_id: str, metadata: dict, scope: str,
    redis_client, collection,
) -> None:
    """Async, fire-and-forget: judge-scores a freshly written agent answer and
    promotes its confidence (unlocking cache-serving) or flags it as bad.

    No session/response_id in the interactive pipeline's tracking tables is
    touched here — this is a self-contained scoring pass, deliberately not
    reusing services/helpers.py::_self_evaluate (which writes to
    moe:response:{response_id} state that belongs to the merger/judge
    pipeline, not agent tool-path sessions).
    """
    try:
        from services.inference import ainvoke_judge_llm
        eval_prompt = (
            "Rate the following answer on a scale of 1-5.\n"
            "1=incomplete/wrong, 3=adequate, 5=complete/correct\n\n"
            f"QUESTION: {query[:200]}\n\n"
            f"ANSWER: {answer[:600]}\n\n"
            "Reply ONLY with: SELF_RATING: N"
        )
        eval_res = await ainvoke_judge_llm(eval_prompt)
        m = re.search(r"SELF_RATING:\s*([1-5])", eval_res.content)
        score = int(m.group(1)) if m else 3
    except Exception as e:
        logger.debug("agent_writeback: judge scoring failed: %s", e)
        return None

    try:
        if score >= _JUDGE_PROMOTE_SCORE_MIN:
            promoted = {**metadata, "confidence": _AGENT_CONFIDENCE_PROMOTED}
            await asyncio.to_thread(
                collection.upsert, ids=[doc_id], documents=[answer], metadatas=[promoted],
            )
            if redis_client:
                await redis_client.setex(_l0_key(scope, query), AGENT_CACHE_L0_TTL, answer)
        elif score <= _JUDGE_FLAG_SCORE_MAX:
            flagged = {**metadata, "flagged": True}
            await asyncio.to_thread(
                collection.upsert, ids=[doc_id], documents=[answer], metadatas=[flagged],
            )
    except Exception as e:
        logger.warning("agent_writeback: judge promotion write failed: %s", e)
    return None

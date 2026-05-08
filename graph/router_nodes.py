"""graph/router_nodes.py — gatekeeper nodes (cache, semantic/fuzzy router, prototype seeding)."""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

import state
from config import (
    MODES, _MODEL_ID_TO_MODE, EXPERTS, EXPERT_TIMEOUT, JUDGE_TIMEOUT,
    PLANNER_TIMEOUT, MAX_EXPERT_OUTPUT_CHARS, JUDGE_MODEL,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    ROUTE_THRESHOLD, ROUTE_GAP, CACHE_MIN_RESPONSE_LEN,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    MCP_URL, GRAPH_VIA_MCP, MAX_GRAPH_CONTEXT_CHARS,
    LITELLM_URL, _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
    CORRECTION_MEMORY_ENABLED, THOMPSON_SAMPLING_ENABLED,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    _CUSTOM_EXPERT_PROMPTS, PLANNER_MAX_TASKS, PLANNER_RETRIES,
    KAFKA_TOPIC_INGEST, NEO4J_URI, NEO4J_USER, NEO4J_PASS,
    _FALLBACK_ENABLED,
)
from metrics import (
    PROM_EXPERT_CALLS, PROM_CONFIDENCE, PROM_CACHE_HITS, PROM_CACHE_MISSES,
    PROM_SELF_EVAL, PROM_COMPLEXITY, PROM_ACTIVE_REQUESTS,
    PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS, PROM_TOOL_FORMAT_ERRORS,
    PROM_TOOL_CALL_SUCCESS, PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES, PROM_SYNTHESIS_CREATED,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
)
from services.inference import (
    _select_node, _invoke_llm_with_fallback, _invoke_judge_with_retry,
    _get_judge_llm, _get_planner_llm, _get_expert_score, _record_expert_outcome,
    _infer_tier, assign_gpu, _ollama_unload, _refine_expert_response,
    _estimate_model_vram_gb, _mark_endpoint_degraded, _endpoint_is_degraded,
)
from services.routing import (
    _resolve_user_experts, _resolve_template_prompts, _server_info, _is_endpoint_error,
)
from services.kafka import _kafka_publish
from services.tracking import _increment_user_budget
from services.llm_instances import judge_llm, planner_llm, ingest_llm, search
from services.helpers import (
    _log_tool_eval,
    _update_rate_limit_headers, _check_rate_limit_exhausted,
    _conf_format_for_mode, _get_expert_prompt,
    _truncate_history, _apply_semantic_memory,
    _web_search_with_citations,
    _store_response_metadata, _self_evaluate, _neo4j_terms_exist,
    _report,
    _shadow_request, _shadow_lock,
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.skills import _build_skill_catalog
from prompts import (
    SYNTHESIS_PERSISTENCE_INSTRUCTION,
    PROVENANCE_INSTRUCTION,
    DEFAULT_PLANNER_ROLE,
)
from prompts import _ROUTE_PROTOTYPES, _RESEARCH_DETECT
from parsing import (
    _oai_content_to_str, _anthropic_content_to_text,
    _extract_images, _extract_oai_images,
    _anthropic_to_openai_messages, _anthropic_tools_to_openai,
)

logger = logging.getLogger("MOE-SOVEREIGN")

# AgentState import — defined in pipeline/state.py
from pipeline.state import AgentState


async def _seed_task_type_prototypes() -> None:
    """
    Fills the ChromaDB collection 'task_type_prototypes' with prototypical queries.
    Idempotent — already-present IDs are skipped.
    Called once at startup.
    """
    try:
        # Use upsert (add-or-update) directly — eliminates the TOCTOU race between
        # the existence check and the subsequent add when multiple instances start up.
        docs, ids, metas = [], [], []
        for category, queries in _ROUTE_PROTOTYPES.items():
            for i, query in enumerate(queries):
                docs.append(query)
                ids.append(f"proto_{category}_{i}")
                metas.append({"category": category})
        if docs:
            await asyncio.to_thread(state.route_collection.upsert, documents=docs, ids=ids, metadatas=metas)
            logger.info(f"🧭 Semantic Router: {len(docs)} prototypes upserted in ChromaDB")
    except Exception as e:
        logger.warning(f"⚠️ Semantic Router seeding failed: {e}")


# --- NODES ---

async def cache_lookup_node(state_: AgentState):
    logger.debug("--- [NODE] CACHE LOOKUP ---")
    # Template toggle: skip cache if disabled
    if not state_.get("enable_cache", True):
        logger.info("Cache disabled by template toggle")
        return {"cached_facts": "", "cache_hit": False}
    # Non-default modes bypass the cache — format mismatch would deliver wrong answers
    if state_.get("mode", "default") != "default":
        return {"cached_facts": "", "cache_hit": False}
    await _report("🔍 Cache-Lookup...")
    # Normalized query for similarity search — pipeline input stays unchanged
    _cache_query = re.sub(r'\s+', ' ', state_["input"].lower().strip().rstrip('?!.,;'))

    # L0: Exact query hash cache (Valkey, instant, before ChromaDB)
    if state_.get("no_cache"):
        pass  # skip L0 cache — no_cache flag set by client
    elif state.redis_client:
        try:
            import hashlib as _hl
            _q_hash = _hl.sha256(_cache_query.encode()).hexdigest()[:24]
            _l0_key = f"moe:qcache:{_q_hash}"
            _l0_hit = await state.redis_client.get(_l0_key)
            if _l0_hit:
                _l0_text = _l0_hit if isinstance(_l0_hit, str) else _l0_hit.decode()
                if len(_l0_text) > 50:
                    PROM_CACHE_HITS.inc()
                    logger.info(f"⚡ L0 query-hash cache hit ({len(_l0_text)} chars)")
                    await _report(f"⚡ L0 cache hit — instant response")
                    return {"cached_facts": _l0_text, "cache_hit": True}
        except Exception as _l0e:
            logger.debug(f"L0 cache check failed: {_l0e}")

    # L1: Semantic similarity cache (ChromaDB)
    res = await asyncio.to_thread(state.cache_collection.query, query_texts=[_cache_query], n_results=3)
    cached = ""
    hit = False
    if not state_.get("no_cache") and res['documents'] and res['documents'][0]:
        docs  = res['documents'][0]
        dists = res.get('distances', [[1.0] * len(docs)])[0]
        metas = res.get('metadatas', [[{}]  * len(docs)])[0]
        for doc, dist, meta in zip(docs, dists, metas):
            if meta.get("flagged"):
                continue  # skip negatively-rated entry
            cached = doc
            if dist < CACHE_HIT_THRESHOLD:
                hit = True
                PROM_CACHE_HITS.inc()
                logger.info(f"✅ Cache hit (distance={dist:.3f}) — skipping pipeline")
                await _report(f"✅ Cache hit (similarity {1-dist:.2f}) — pipeline skipped")
            break
    if not hit:
        PROM_CACHE_MISSES.inc()
        await _report("📭 No cache hit — starting full pipeline")
    # Soft hits (0.15 < dist < 0.50): collect as few-shot examples
    soft_examples = []
    if res['documents'] and res['documents'][0]:
        for doc, dist, meta in zip(
            res['documents'][0], res.get('distances', [[]])[0], res.get('metadatas', [[]])[0]
        ):
            if meta.get("flagged"):
                continue
            if CACHE_HIT_THRESHOLD < dist < SOFT_CACHE_THRESHOLD:
                q = meta.get("input", "")[:120]
                a = doc[:400]
                soft_examples.append(f"Question: {q}\nAnswer: {a}")
            if len(soft_examples) >= SOFT_CACHE_MAX_EXAMPLES:
                break
    soft_ctx = "\n\n---\n\n".join(soft_examples) if soft_examples else ""
    if soft_ctx:
        await _report(f"💡 {len(soft_examples)} similar previous answer(s) loaded as context")
    return {"cached_facts": cached, "cache_hit": hit, "soft_cache_examples": soft_ctx}


async def semantic_router_node(state_: AgentState):
    """
    Semantic pre-router — runs after cache_lookup_node, before planner_node.
    Compares the user query semantically against prototypical task queries per category.
    If a clear match is found (dist < ROUTE_THRESHOLD, gap > ROUTE_GAP),
    'direct_expert' is set and a synthetic single-task plan is created.
    planner_node then skips the LLM call and uses this plan directly.
    On ambiguity or cache hit: no intervention.
    """
    # Don't route if cache hit (will be skipped anyway) or non-default mode
    if state_.get("cache_hit") or state_.get("mode", "default") != "default":
        return {"direct_expert": ""}

    _query = re.sub(r'\s+', ' ', state_["input"].lower().strip().rstrip('?!.,;'))
    try:
        res = await asyncio.to_thread(state.route_collection.query, query_texts=[_query], n_results=2)
        docs  = res.get("documents",  [[]])[0]
        dists = res.get("distances",  [[1.0, 1.0]])[0]
        metas = res.get("metadatas",  [[{}, {}]])[0]

        if len(dists) < 2 or not docs:
            return {"direct_expert": ""}

        top_dist  = dists[0]
        gap       = dists[1] - dists[0]
        category  = metas[0].get("category", "")

        if top_dist < ROUTE_THRESHOLD and gap > ROUTE_GAP and category:
            synthetic_plan = [{"task": state_["input"], "category": category}]
            logger.info(
                f"🧭 Semantic Router: direct routing → '{category}' "
                f"(dist={top_dist:.3f}, gap={gap:.3f})"
            )
            await _report(
                f"🧭 Semantic Router: Fast-Path → expert '{category}' "
                f"(similarity {1-top_dist:.2f}, uniqueness {gap:.2f})"
            )
            return {"direct_expert": category, "plan": synthetic_plan}
    except Exception as e:
        logger.debug(f"Semantic Router error: {e}")

    return {"direct_expert": ""}


async def fuzzy_router_node(state_: AgentState):
    """Replace heuristic binary routing flags with fuzzy t-norm conjunction scores.

    The planner currently sets skip_research and enable_graphrag as binary flags
    derived from complexity heuristics. This node replaces that decision with a
    quantitative approach: independent confidence scores are computed from the
    plan content and combined via the Godel t-norm (minimum) for a conservative
    gate — both signals must be strong to activate a retrieval node.

    Mathematical foundation:
        Fuzzy logics as the most general framework — de Vries (2007),
        arXiv:0707.2161. T-norm conjunction over [0,1]-valued truth degrees
        replaces Boolean routing. Godel t-norm T_G(a,b) = min(a,b) (Godel
        1932, discussed in de Vries 2007, §4); Lukasiewicz t-norm
        T_L(a,b) = max(0, a+b-1) (Lukasiewicz 1920, de Vries 2007, §4).

    Thresholds (configurable via env):
        FUZZY_VECTOR_THRESHOLD (default 0.30): below -> skip_research=True
        FUZZY_GRAPH_THRESHOLD  (default 0.35): below -> enable_graphrag=False
    """
    if state_.get("cache_hit"):
        return {}

    from pipeline.logic_types import goedel_tnorm
    from parsing import _compute_routing_confidence

    plan             = state_.get("plan") or []
    complexity_level = state_.get("complexity_level") or "moderate"
    enable_graphrag  = state_.get("enable_graphrag", True)
    skip_research    = state_.get("skip_research", False)

    vector_conf, graph_conf = _compute_routing_confidence(
        plan, complexity_level, enable_graphrag
    )

    # Complexity as a second signal: map to [0,1] for t-norm input
    _complexity_weight = {"trivial": 0.1, "memory_recall": 0.0, "moderate": 0.5, "complex": 1.0}
    complexity_score   = _complexity_weight.get(complexity_level, 0.5)

    # Godel t-norm: conservative gate — both signals must be strong
    tnorm_vector = goedel_tnorm(vector_conf, complexity_score)
    tnorm_graph  = goedel_tnorm(graph_conf,  complexity_score)

    new_skip_research   = tnorm_vector < _FUZZY_VECTOR_THRESHOLD
    new_enable_graphrag = tnorm_graph  >= _FUZZY_GRAPH_THRESHOLD

    scores = {
        "vector_confidence": vector_conf,
        "graph_confidence":  graph_conf,
        "tnorm_vector":      round(tnorm_vector, 3),
        "tnorm_graph":       round(tnorm_graph, 3),
        "method":            "goedel",
        "vector_threshold":  _FUZZY_VECTOR_THRESHOLD,
        "graph_threshold":   _FUZZY_GRAPH_THRESHOLD,
    }

    logger.info(
        f"🔀 Fuzzy Router: vector={vector_conf:.2f}→T={tnorm_vector:.2f} "
        f"({'skip' if new_skip_research else 'fetch'}) | "
        f"graph={graph_conf:.2f}→T={tnorm_graph:.2f} "
        f"({'skip' if not new_enable_graphrag else 'fetch'})"
    )
    await _report(
        f"🔀 Fuzzy Router (Godel t-norm): "
        f"web={'✓' if not new_skip_research else '✗'} (score={tnorm_vector:.2f}) | "
        f"graph={'✓' if new_enable_graphrag else '✗'} (score={tnorm_graph:.2f})"
    )

    return {
        "vector_confidence":    vector_conf,
        "graph_confidence":     graph_conf,
        "fuzzy_routing_scores": scores,
        "skip_research":        new_skip_research,
        "enable_graphrag":      new_enable_graphrag,
    }


# --- GRAPH ROUTER ---
def _route_cache(state_: AgentState) -> str:
    """On cache hit go directly to merger — entire pipeline is skipped."""
    return "merger" if state_.get("cache_hit") else "semantic_router"

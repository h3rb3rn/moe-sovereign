"""graph/nodes.py — LangGraph node functions for the MoE orchestration pipeline."""

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
import main as _m
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
        for category, queries in _m._ROUTE_PROTOTYPES.items():
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
    await _m._report("🔍 Cache-Lookup...")
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
                    await _m._report(f"⚡ L0 cache hit — instant response")
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
                await _m._report(f"✅ Cache hit (similarity {1-dist:.2f}) — pipeline skipped")
            break
    if not hit:
        PROM_CACHE_MISSES.inc()
        await _m._report("📭 No cache hit — starting full pipeline")
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
        await _m._report(f"💡 {len(soft_examples)} similar previous answer(s) loaded as context")
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
            await _m._report(
                f"🧭 Semantic Router: Fast-Path → expert '{category}' "
                f"(similarity {1-top_dist:.2f}, uniqueness {gap:.2f})"
            )
            return {"direct_expert": category, "plan": synthetic_plan}
    except Exception as e:
        logger.debug(f"Semantic Router error: {e}")

    return {"direct_expert": ""}


def _validate_tool_result(result_str: str, tool: str) -> tuple[bool, str]:
    """Sanity-check MCP tool output before it enters working memory."""
    if not result_str or len(result_str.strip()) < 3:
        return False, "empty_result"
    lower = result_str.lower()
    if lower.startswith("[") and "error" in lower[:30]:
        return False, "error_prefix"
    return True, ""


async def mcp_node(state_: AgentState):
    """Executes precision tool calls via MCP server — all in parallel."""
    if state_.get("cache_hit"):
        return {"mcp_result": ""}

    precision_tasks = [
        t for t in state_.get("plan", [])
        if isinstance(t, dict) and t.get("category") == "precision_tools" and t.get("mcp_tool")
    ]
    if not precision_tasks:
        return {"mcp_result": ""}

    # Per-User MCP-Tool Permission-Check
    allowed_mcp = state_.get("user_permissions", {}).get("mcp_tool")
    if allowed_mcp is not None and "*" not in allowed_mcp:
        precision_tasks = [t for t in precision_tasks if t.get("mcp_tool") in allowed_mcp]
        if not precision_tasks:
            logger.info("⛔ MCP tools not enabled for this user")
            return {"mcp_result": ""}

    tool_names = [t.get("mcp_tool") for t in precision_tasks]
    await _m._report(f"⚙️ MCP Precision Tools: {', '.join(tool_names)}")
    logger.info(f"--- [NODE] MCP ({len(precision_tasks)} Tools parallel) ---")

    # Working Memory accumulators — carry over facts from previous iterations
    _wm: dict = dict(state_.get("working_memory") or {})
    _log: list = list(state_.get("tool_calls_log") or [])
    _failures: list = list(state_.get("tool_failures") or [])
    _ts_now = lambda: __import__('datetime').datetime.utcnow().isoformat() + "Z"

    async def call_tool(client: httpx.AsyncClient, task: dict) -> str:
        tool = task.get("mcp_tool")
        args = task.get("mcp_args", {})
        desc = task.get("task", tool)
        # Fix common planner argument-naming mismatches so MCP tools don't
        # reject the call and fall back to LLM hallucination.
        if tool == "calculate" and "operation" in args and "expression" not in args:
            args["expression"] = args.pop("operation")
        if tool == "calculate" and "formula" in args and "expression" not in args:
            args["expression"] = args.pop("formula")
        # Pre-call schema validation — catch missing required args before HTTP round-trip
        _schema = _m.MCP_TOOL_SCHEMAS.get(tool, {})
        _missing = [f for f in _schema.get("required", []) if f not in args]
        if _missing:
            logger.info(f"🔧 MCP pre-validation: {tool} missing {_missing} — asking judge to fix")
            _pre_fix_prompt = (
                f"The MCP tool '{tool}' requires these arguments: {_schema.get('required', [])}\n"
                f"Current args (missing {_missing}): {json.dumps(args)}\n"
                f"Task context: {desc}\n"
                f"Return ONLY a corrected JSON object with all required args filled. No explanation."
            )
            try:
                from parsing import _extract_json
                _pre_fix_res = await _invoke_judge_with_retry(state_, _pre_fix_prompt, max_retries=1, temperature=0.05)
                _fixed = _extract_json(_pre_fix_res.content or "")
                if isinstance(_fixed, dict) and all(f in _fixed for f in _missing):
                    args = _fixed
                    logger.info(f"🔧 MCP pre-validation: args corrected for {tool}")
            except Exception as _pve:
                logger.debug(f"MCP pre-validation fix failed for {tool}: {_pve}")
        await _m._report(f"⚙️ MCP-Call: {tool}\nArgs: {json.dumps(args, ensure_ascii=False, indent=2)}")
        _mcp_t0 = time.monotonic()
        try:
            resp = await client.post(f"{MCP_URL}/invoke", json={"tool": tool, "args": args})
            resp.raise_for_status()
            data = resp.json()
            _mcp_dt = round(time.monotonic() - _mcp_t0, 3)
            if "error" in data:
                err_str = data['error']
                await _m._report(f"⚙️ MCP error [{tool}]: {err_str}")
                _m._log_tool_eval({
                    "ts": _ts_now(), "source": "mcp_node",
                    "chat_id": state_.get("chat_id", ""), "user_id": state_.get("user_id", ""),
                    "tool": tool, "args": args, "task": desc, "result": None,
                    "error": err_str, "latency_s": _mcp_dt,
                    "caller": "orchestrator_pipeline", "template": state_.get("template_name", ""),
                })
                _entry = {"tool": tool, "args": args, "result": None, "status": "error", "error": err_str, "ts": _ts_now()}
                _log.append(_entry)
                _failures.append(_entry)
                # Attempt arg-correction retry via judge LLM
                fix_prompt = (
                    f"The MCP tool '{tool}' returned an error: {err_str}\n"
                    f"Original args: {json.dumps(args)}\n"
                    f"Return ONLY a corrected JSON object for the args. No explanation."
                )
                try:
                    from parsing import _extract_json
                    fix_res = await _invoke_judge_with_retry(state_, fix_prompt, max_retries=1)
                    corrected_args = _extract_json(fix_res.content or "")
                    if not isinstance(corrected_args, dict):
                        raise ValueError("judge returned non-dict JSON")
                    logger.info(f"🔄 MCP retry [{tool}] with corrected args: {corrected_args}")
                    resp2 = await client.post(f"{MCP_URL}/invoke", json={"tool": tool, "args": corrected_args})
                    resp2.raise_for_status()
                    data2 = resp2.json()
                    if "error" not in data2:
                        result_str2 = data2.get("result", "")
                        await _m._report(f"⚙️ MCP retry OK [{tool}]:\n{result_str2}")
                        valid, _ = _validate_tool_result(result_str2, tool)
                        if valid:
                            wm_key = f"{tool}:{json.dumps(corrected_args)[:60]}"
                            _wm[wm_key] = {"value": result_str2[:500], "source": "mcp_node", "confidence": 0.8, "ts": _ts_now()}
                        _log.append({"tool": tool, "args": corrected_args, "result": result_str2[:200], "status": "ok_retry", "ts": _ts_now()})
                        return f"[{desc}] {result_str2}"
                except Exception as retry_exc:
                    logger.debug(f"MCP arg-correction retry failed for {tool}: {retry_exc}")
                return f"[{desc}] Error: {err_str}"
            result_str = data.get('result', '')
            await _m._report(f"⚙️ MCP result [{tool}]:\n{result_str}")
            logger.info(f"🔧 MCP: [{desc}] {result_str[:120]}")
            _m._log_tool_eval({
                "ts": _ts_now(), "source": "mcp_node",
                "chat_id": state_.get("chat_id", ""), "user_id": state_.get("user_id", ""),
                "tool": tool, "args": args, "task": desc, "result": result_str[:500],
                "error": None, "latency_s": _mcp_dt,
                "caller": "orchestrator_pipeline", "template": state_.get("template_name", ""),
            })
            _log.append({"tool": tool, "args": args, "result": result_str[:200], "status": "ok", "ts": _ts_now()})
            # Write validated results to working memory
            valid, reason = _validate_tool_result(result_str, tool)
            if valid:
                wm_key = f"{tool}:{json.dumps(args)[:60]}"
                _wm[wm_key] = {"value": result_str[:500], "source": "mcp_node", "confidence": 0.9, "ts": _ts_now()}
            else:
                logger.debug(f"MCP result for {tool} failed validation: {reason}")
            return f"[{desc}] {result_str}"
        except Exception as e:
            _mcp_dt = round(time.monotonic() - _mcp_t0, 3)
            logger.error(f"MCP Tool '{tool}' failed: {e}")
            await _m._report(f"⚙️ MCP exception [{tool}]: {e}")
            _m._log_tool_eval({
                "ts": _ts_now(), "source": "mcp_node",
                "chat_id": state_.get("chat_id", ""), "user_id": state_.get("user_id", ""),
                "tool": tool, "args": args, "task": desc, "result": None,
                "error": str(e)[:300], "latency_s": _mcp_dt,
                "caller": "orchestrator_pipeline", "template": state_.get("template_name", ""),
            })
            _entry = {"tool": tool, "args": args, "result": None, "status": "exception", "error": str(e)[:200], "ts": _ts_now()}
            _log.append(_entry)
            _failures.append(_entry)
            return f"[{desc}] MCP error: {e}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        results = await asyncio.gather(*[call_tool(client, t) for t in precision_tasks])

    combined = "\n".join(results)
    await _m._report(f"⚙️ MCP: {len(results)} result(s) received")
    logger.info(f"🔧 MCP: {combined[:300]}")
    if _wm:
        logger.info(f"📝 Working Memory: {len(_wm)} facts extracted")
    return {
        "mcp_result": combined,
        "working_memory": _wm,
        "tool_calls_log": _log,
        "tool_failures": _failures,
    }


async def graph_rag_node(state_: AgentState):
    """Fetch structured graph context from Neo4j — parallel to LLM experts.
    When GRAPH_VIA_MCP=true, the MCP server is used as interface (graph-as-a-tool),
    otherwise direct access to graph_manager (fallback, backwards compatible).
    """
    if state_.get("cache_hit"):
        return {"graph_context": ""}
    # Template toggle: skip GraphRAG if disabled
    if not state_.get("enable_graphrag", True):
        logger.info("GraphRAG disabled by template toggle")
        return {"graph_context": ""}
    # Complexity routing: skip for trivial requests (complexity_estimator sets skip_graph)
    if state_.get("skip_graph"):
        logger.info("⚡ GraphRAG skipped (complexity routing: trivial/skip_graph)")
        return {"graph_context": ""}
    if not GRAPH_VIA_MCP and state.graph_manager is None:
        return {"graph_context": ""}
    plan = state_.get("plan", [])
    categories = [t.get("category", "") for t in plan if isinstance(t, dict)]

    # GraphRAG on-demand: skip the Neo4j query for queries that are clearly about
    # public external facts (papers, databases, media) rather than internal ontology.
    # We still run if the plan explicitly includes knowledge_healing (graph needed).
    _has_knowledge_healing = "knowledge_healing" in categories
    _is_public_fact_query = bool(_m._RESEARCH_DETECT.search(state_.get("input", "")))
    if _is_public_fact_query and not _has_knowledge_healing:
        logger.info("⚡ GraphRAG skipped (public-fact query — internal graph not relevant)")
        await _m._report("⚡ GraphRAG: skipped (external research query)")
        return {"graph_context": ""}

    # GraphRAG-Cache (Valkey, TTL=3600s)
    import hashlib as _hashlib
    _graph_cache_key = f"moe:graph:{_hashlib.sha256((state_['input'][:200] + ''.join(sorted(categories))).encode()).hexdigest()[:16]}"
    if state.redis_client is not None:
        try:
            _cached_ctx = await state.redis_client.get(_graph_cache_key)
            if _cached_ctx:
                _cached_ctx_str = _cached_ctx if isinstance(_cached_ctx, str) else _cached_ctx.decode()
                logger.info(f"🔗 GraphRAG cache hit (Valkey) — {len(_cached_ctx_str)} chars")
                await _m._report(f"🔗 GraphRAG: context from Valkey cache ({len(_cached_ctx_str)} chars)")
                return {"graph_context": _cached_ctx_str}
        except Exception as _ge:
            logger.debug(f"GraphRAG cache read error: {_ge}")

    await _m._report("🔗 GraphRAG — knowledge graph query (Neo4j)...")
    try:
        if GRAPH_VIA_MCP:
            # Flange: MCP server as graph-as-a-tool (accessible to external agents)
            async with httpx.AsyncClient(timeout=15.0) as _client:
                _resp = await _client.post(
                    f"{MCP_URL}/invoke",
                    json={"tool": "graph_query", "args": {"query": state_["input"], "categories": categories}},
                )
                _resp.raise_for_status()
                ctx = _resp.json().get("result", "")
        else:
            # Direct access (default, backwards compatible)
            _tenant_ids = state_.get("tenant_ids", [])
            ctx = await state.graph_manager.query_context(
                state_["input"], categories, tenant_ids=_tenant_ids or None,
            )

        if ctx:
            # Annotate procedural requirements so the merger treats them as hard facts.
            if "[Procedural Requirements]" in ctx:
                ctx = (
                    "[Note: The following knowledge graph facts describe physical or "
                    "procedural requirements. Include these requirements explicitly in "
                    "your answer.]\n\n" + ctx
                )
            logger.info(f"📊 GraphRAG: {len(ctx)} chars context found (via_mcp={GRAPH_VIA_MCP})")
            await _m._report(f"🔗 GraphRAG: {len(ctx)} chars structured context")
            if state.redis_client is not None:
                asyncio.create_task(state.redis_client.setex(_graph_cache_key, 3600, ctx))
        else:
            await _m._report("🔗 GraphRAG: no matching context found")

        # Domain-filtered ChromaDB retrieval using planner-extracted metadata_filters
        _meta_filters = state_.get("metadata_filters") or {}
        if _meta_filters and state.cache_collection is not None:
            try:
                _where: Dict = {k: {"$eq": v} for k, v in _meta_filters.items() if isinstance(v, str) and v}
                if len(_where) > 1:
                    _where = {"$and": [{k: v} for k, v in _where.items()]}
                _chroma_res = await asyncio.to_thread(
                    state.cache_collection.query,
                    query_texts=[state_["input"]],
                    n_results=3,
                    where=_where,
                )
                _chroma_docs = (_chroma_res.get("documents") or [[]])[0]
                _chroma_docs = [d for d in _chroma_docs if d]
                if _chroma_docs:
                    _chroma_snippet = "\n---\n".join(_chroma_docs)
                    _filter_label = ", ".join(f"{k}={v}" for k, v in _meta_filters.items())
                    ctx = (ctx + f"\n\n[Domain-Filtered Memory ({_filter_label})]\n{_chroma_snippet}"
                           if ctx else f"[Domain-Filtered Memory ({_filter_label})]\n{_chroma_snippet}")
                    logger.info(f"🔎 Filtered ChromaDB: {len(_chroma_docs)} docs ({_filter_label})")
            except Exception as _cf_exc:
                logger.debug(f"Filtered ChromaDB lookup skipped: {_cf_exc}")

        # Extract entity metadata for causal-path logging.
        # Neo4j results contain lines like: "Entity: <name> (<type>) confidence=<val>"
        _entity_meta: list = []
        if ctx:
            _ent_re = re.compile(
                r"Entity:\s*([^\(]+?)\s*\(([^)]+)\).*?confidence[=:]\s*([0-9.]+)",
                re.IGNORECASE,
            )
            for _match in _ent_re.finditer(ctx[:4000]):
                _entity_meta.append({
                    "name": _match.group(1).strip(),
                    "type": _match.group(2).strip(),
                    "confidence": float(_match.group(3)),
                })

        return {"graph_context": ctx, "graphrag_entities": _entity_meta}
    except Exception as e:
        logger.warning(f"GraphRAG query_context error: {e}")
        return {"graph_context": ""}


async def math_node_wrapper(state_: AgentState):
    if state_.get("cache_hit"):
        return {"math_result": ""}
    plan = state_.get("plan", [])
    has_math = any(t.get("category") == "math" for t in plan if isinstance(t, dict))
    has_precision = any(t.get("category") == "precision_tools" for t in plan if isinstance(t, dict))
    # Skip if no math task or precision_tools already covers the math
    if not has_math or has_precision:
        return {"math_result": ""}
    logger.debug("--- [NODE] MATH CALCULATION ---")
    await _m._report("🧮 Math module (SymPy)...")
    from math_node import math_node
    result = await math_node(state_)
    await _m._report("🧮 Math computation complete")
    return {"math_result": result["math_result"]}


def _sanitize_plan(raw: list, fallback_input: str) -> list:
    """
    Ensures all plan entries are valid task dicts.
    Strings, empty dicts or dicts without 'task' key are discarded.
    Returns at least one fallback task.
    """
    NON_EXPERT_CATEGORIES = {"precision_tools", "research"}
    valid_cats = set(EXPERTS.keys()) | NON_EXPERT_CATEGORIES | {"agentic_coder", "memory_recall"}
    result = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning(f"⚠️ Planner: invalid task entry skipped: {item!r}")
            continue
        task_text = item.get("task", "").strip()
        if not task_text:
            continue
        cat = item.get("category", "general")
        if cat not in valid_cats:
            logger.warning(f"⚠️ Planner: unknown category '{cat}' → 'general'")
            cat = "general"
        item["category"] = cat
        result.append(item)
    if not result:
        logger.warning("⚠️ Planner: no valid task after sanitization — fallback")
        return [{"task": fallback_input, "category": "general"}]
    if len(result) > PLANNER_MAX_TASKS:
        logger.warning(f"⚠️ Planner: {len(result)} tasks → limited to {PLANNER_MAX_TASKS}")
        result = result[:PLANNER_MAX_TASKS]
    return result


_MATH_TEMP_PATTERN = re.compile(
    r'\b(berechne?|berechnung|integral|ableitung|differentialgleichung|löse?|solve|'
    r'calculate|calculation|subnet|cidr|bgp|ospf|hash|checksum|statistics|statistik|'
    r'wie viel|how much|how many|wie viele|convert|umrechnen|prozent|percent)\b',
    re.I,
)
_CREATIVE_TEMP_PATTERN = re.compile(
    r'\b(entwirf|erstelle?|schreibe?|gestalte?|verfasse?|dichte?|erdichte?|'
    r'create|write|generate|design|compose|brainstorm|ideen|kreativ|story|poem|'
    r'imagine|vorstellen|erfinde?|invent)\b',
    re.I,
)


def _detect_query_temperature(query: str) -> float:
    """Infer optimal sampling temperature from query type.

    Math/factual queries need deterministic output (low temp).
    Creative queries benefit from variability (high temp).
    """
    if _MATH_TEMP_PATTERN.search(query):
        return 0.05
    if _CREATIVE_TEMP_PATTERN.search(query):
        return 0.70
    return 0.20  # factual / neutral default


async def planner_node(state_: AgentState):
    _output_skill = ""  # Initialize early to prevent UnboundLocalError
    # Cache hit: no LLM call needed
    if state_.get("cache_hit"):
        logger.info("📋 Planner skipped (cache hit)")
        return {"plan": []}
    # Semantic pre-routing: direct expert path without LLM call
    if state_.get("direct_expert") and state_.get("plan"):
        logger.info(f"📋 Planner skipped (semantic router → '{state_['direct_expert']}')")
        return {"plan": state_["plan"]}

    # Emit pending reports (e.g. skill resolution) from state
    for _pr in (state_.get("pending_reports") or []):
        await _m._report(_pr)

    # ── Agentic loop: read config from template state ───────────────────────
    _agentic_iteration  = state_.get("agentic_iteration") or 0
    _agentic_max_rounds = state_.get("agentic_max_rounds") or 0
    _is_agentic_replan  = _agentic_iteration > 0 and _agentic_max_rounds > 0

    # Planner result cache: same request → same plan (Valkey, TTL=30 min)
    # Skip cache entirely during agentic re-planning or when the caller requests no cache.
    # no_cache=True bypasses both L0 LLM cache and planner cache to ensure a fresh plan
    # is generated — important for benchmark runs that follow cache pre-warming.
    import hashlib as _hashlib
    _no_cache_flag  = state_.get("no_cache", False)
    # Include a short config fingerprint so the plan cache auto-invalidates when
    # the MCP tool set or planner prompt changes between deployments.
    _cfg_fp = _hashlib.md5(
        f"{len(_m.MCP_TOOLS_DESCRIPTION)}:{(state_.get('planner_prompt') or '')[:80]}"
        .encode()
    ).hexdigest()[:6]
    # Include chat_history presence in key: same query needs different plan
    # in conversation context (memory_recall) vs. standalone (research).
    _has_history = "h" if len(state_.get("chat_history") or []) >= 2 else "n"
    _plan_cache_key = f"moe:plan:{_cfg_fp}:{_has_history}:{_hashlib.sha256(state_['input'][:300].encode()).hexdigest()[:16]}"
    if state.redis_client is not None and not _is_agentic_replan and not _no_cache_flag:
        try:
            _cached_plan_raw = await state.redis_client.get(_plan_cache_key)
            if _cached_plan_raw:
                _cached_plan = json.loads(_cached_plan_raw)
                logger.info(f"📋 Planner cache hit (Valkey) — skipping LLM")
                await _m._report("📋 Planner: plan loaded from Valkey cache")
                return {"plan": _cached_plan, "prompt_tokens": 0, "completion_tokens": 0}
        except Exception as _pe:
            logger.debug(f"Planner cache read error: {_pe}")

    # Complexity estimation: determine routing hints before LLM planner call
    from complexity_estimator import estimate_complexity, complexity_routing_hint
    _complexity = estimate_complexity(state_["input"])
    # Day-2 upgrade: factual questions inside a multi-turn conversation are
    # almost always asking about something the user stated earlier — not
    # web-searchable facts. Upgrade trivial AND moderate to memory_recall
    # when substantive chat_history is present. Complex/research queries are
    # already routed differently by estimate_complexity before we reach here,
    # so upgrading moderate is safe for recall-heavy conversation patterns.
    _chat_hist = state_.get("chat_history") or []
    if _complexity in ("trivial", "moderate") and len(_chat_hist) >= 2:
        _prev_complexity = _complexity
        _complexity = "memory_recall"
        logger.info("🧠 Day-2 upgrade: %s→memory_recall (chat_history present)", _prev_complexity)
    _routing    = complexity_routing_hint(_complexity)
    # Multi-fact memory_recall: when the question asks for multiple facts
    # (contains conjunctions like "und X und Y" or multiple interrogatives),
    # allow 2 tasks so the planner can create separate recall tasks per fact.
    if _complexity == "memory_recall":
        _multi_fact = bool(re.search(
            r'\b(und|and|sowie|als auch|außerdem|additionally|also)\b',
            state_["input"], re.I,
        ))
        if _multi_fact and _routing.get("max_tasks", 1) < 2:
            _routing = dict(_routing)
            _routing["max_tasks"] = 2
            logger.info("🧠 Multi-fact memory_recall: max_tasks=2")
    PROM_COMPLEXITY.labels(level=_complexity).inc()
    logger.info(f"📊 Complexity: {_complexity} → {_routing}")
    # Map complexity to cost tier for OpEx tracking and expert-tier enforcement.
    # local_7b → trivial tasks: single T1 expert max, no research, no thinking node
    # mid_tier  → moderate tasks: standard MoE, no thinking node
    # full      → complex tasks: all capabilities active
    _cost_tier_map = {"trivial": "local_7b", "moderate": "mid_tier", "complex": "full"}
    _cost_tier = _cost_tier_map.get(_complexity, "mid_tier")
    logger.info(f"💰 Cost-Tier: {_cost_tier} (complexity={_complexity})")

    # Store routing hints in state for downstream nodes
    # Use explicit request temperature when set (e.g. GAIA benchmark temperature=0.0);
    # fall back to query-adaptive detection when None.
    _explicit_temp = state_.get("query_temperature")  # set by HTTP handler from request
    _query_temp    = _explicit_temp if _explicit_temp is not None else _detect_query_temperature(state_["input"])
    # memory_recall: T=0 for deterministic exact-value recall (prevents
    # stochastic drift where model picks old vs. new value unpredictably).
    if _complexity == "memory_recall" and _explicit_temp is None:
        _query_temp = 0.0
    logger.info(f"🌡️ Temperature: {_query_temp} ({'explicit' if _explicit_temp is not None else 'adaptive'})")
    _complexity_state_update = {
        "complexity_level":   _complexity,
        "skip_research":      _routing["skip_research"],
        "skip_thinking":      _routing["skip_thinking"],
        "cost_tier":          _cost_tier,
        "force_tier1":        _routing.get("force_tier1", False),
        "query_temperature":  _query_temp,
    }

    # Agent mode: force code_reviewer + technical_support directly, no LLM planner
    if state_.get("mode") == "agent":
        logger.info("📋 Agent mode: forcing code_reviewer + technical_support")
        await _m._report("📋 Agent mode: code experts activated...")
        return {
            "plan": [
                {"task": state_["input"], "category": "code_reviewer"},
                {"task": state_["input"], "category": "technical_support"},
            ],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    # memory_recall fast-path: if complexity is memory_recall AND the template has a
    # dedicated memory_recall expert configured, skip the LLM planner entirely.
    # The planner LLM would misroute recall questions (e.g. routing to "research")
    # because it cannot distinguish facts from this conversation vs. external knowledge.
    # This bypass is template-driven — only activates when memory_recall is in user_experts.
    _user_experts_map = state_.get("user_experts") or {}
    if _complexity == "memory_recall" and "memory_recall" in _user_experts_map:
        logger.info("🧠 memory_recall fast-path: dedicated expert configured, LLM planner skipped")
        await _m._report("🧠 Memory Expert: Analysiere Konversationshistorie...")
        return {
            **_complexity_state_update,
            "plan": [{"task": state_["input"], "category": "memory_recall"}],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    logger.debug("--- [NODE] PLANNER ---")
    await _m._report("📋 Planner analyzing request...")
    # agentic_coder is an optional category — it only appears when the template has it enabled
    # or the mode requires it. DEFAULT_EXPERT_PROMPTS contains the fallback prompt.
    expert_categories = list(EXPERTS.keys())
    if "agentic_coder" not in expert_categories and (
        state_.get("mode") in ("agent_orchestrated", "code")
        or "agentic_coder" in (state_.get("user_experts") or {})
    ):
        expert_categories = expert_categories + ["agentic_coder"]

    # Annotate images in planner input so routing triggers 'vision'
    # The marker must be exactly "[BILD-EINGABE vorhanden]" — as specified in the planner rule
    images = state_.get("images") or []
    if images:
        state_ = dict(state_)
        _img_hint = f"[BILD-EINGABE vorhanden] ({len(images)} Bild(er))"
        if "[BILD-EINGABE vorhanden]" not in state_["input"]:
            state_["input"] = f"{_img_hint} {state_['input']}"

    # SELF_EVAL quality hint — informs planner about historical performance
    _quality_hint = ""
    try:
        from telemetry import get_quality_hint as _get_quality_hint
        _quality_hint = await _get_quality_hint(
            state._userdb_pool, state_.get("template_name", ""), _complexity
        )
        if _quality_hint:
            _quality_hint = f"\n{_quality_hint}\n"
    except Exception:
        pass

    # Load proven plan patterns from positive user feedback
    success_hint = ""
    if state.redis_client is not None:
        try:
            patterns = await state.redis_client.zrevrange("moe:planner_success", 0, 4, withscores=True)
            if patterns:
                top = [f"  {sig} ({int(score)}×)" for sig, score in patterns]
                success_hint = (
                    "\nPROVEN EXPERT COMBINATIONS (from positive user feedback — prefer these):\n"
                    + "\n".join(top)
                    + "\n"
                )
        except Exception:
            pass

    # Load few-shot context from self-correction loop (OBJ 3)
    _few_shot_hint = ""
    try:
        from self_correction import get_few_shot_context as _get_fsc
        _plan_categories = list(EXPERTS.keys())  # All categories as hint sources
        _few_shot_hint = await _get_fsc(_plan_categories, state.redis_client, max_per_cat=2)
    except Exception:
        pass

    # Show agentic code tools only when mode matches or agentic_coder category is active
    _inject_agentic = (
        state_.get("mode") in ("agent_orchestrated", "code")
        or "agentic_coder" in (state_.get("user_experts") or {})
    )
    _agentic_code_block = (
        f"\nCODE NAVIGATION TOOLS (only for 'agentic_coder' category — NOT for other experts!):\n"
        f"Use these tools when code files are to be analyzed/edited.\n"
        f"{_m.AGENTIC_CODE_TOOLS_DESCRIPTION}\n"
        f"Format: {{\"task\": \"...\", \"category\": \"precision_tools\", "
        f"\"mcp_tool\": \"repo_map|read_file_chunked|lsp_query\", \"mcp_args\": {{...}}}}\n"
        f"THEN use agentic_coder expert for analysis/implementation.\n"
    ) if _inject_agentic and _m.AGENTIC_CODE_TOOLS_DESCRIPTION else ""

    _planner_role = (state_.get("planner_prompt") or "").strip() or _m.DEFAULT_PLANNER_ROLE

    # ── Agentic re-plan: inject gap context and clear stale single-string results ──
    _agentic_context_block = ""
    _agentic_state_reset: dict = {}
    if _is_agentic_replan:
        _gap            = (state_.get("agentic_gap") or "").strip()
        _history        = state_.get("agentic_history") or []
        _prev_found     = _history[-1].get("findings", "") if _history else ""
        _wm             = state_.get("working_memory") or {}
        _failures       = state_.get("tool_failures") or []
        _tried_queries  = state_.get("attempted_queries") or []
        _strategy_hint  = (state_.get("search_strategy_hint") or "").strip()

        # Prefer structured working memory over truncated prose when available
        if _wm:
            _context_facts = "ESTABLISHED FACTS (structured):\n" + json.dumps(_wm)[:2000]
        else:
            _context_facts = f"Previously established facts:\n{_prev_found[:1500]}"

        # Build search-history block to prevent query repetition
        if _tried_queries:
            _query_lines = "\n".join(
                f"  • [{q.get('quality','?')}] {q.get('query','?')[:120]}"
                for q in _tried_queries[-10:]  # last 10 queries max
            )
            _search_history_block = (
                f"\nSEARCH QUERIES ALREADY TRIED (do NOT repeat these or near-identical variants):\n"
                f"{_query_lines}\n"
            )
        else:
            _search_history_block = ""

        _fail_block = (
            f"\nFAILED TOOL CALLS (do NOT retry with identical args):\n{json.dumps(_failures[-5:])}"
            if _failures else ""
        )

        # Progressive depth hints based on iteration number
        _depth = _agentic_iteration
        if _depth == 1:
            _depth_hint = (
                "SEARCH STRATEGY (Depth 1 — be more specific):\n"
                "  • Use domain-restricted queries: add 'site:wikipedia.org', 'site:github.com', 'site:arxiv.org', 'site:pubchem.ncbi.nlm.nih.gov'\n"
                "  • Try the exact title/name in quotes for precise matches\n"
                "  • Use wikipedia_get_section MCP tool for Wikipedia data with exact section names\n"
                "  • Use github_search_issues MCP tool if querying GitHub repositories\n"
            )
        elif _depth == 2:
            _depth_hint = (
                "SEARCH STRATEGY (Depth 2 — use specialized tools directly):\n"
                "  • Use youtube_transcript MCP tool for video content\n"
                "  • Use chess_analyze_position MCP tool for chess positions — extract FEN from image first, then call the tool\n"
                "  • Use pubchem_compound_search MCP tool for chemical/compound data\n"
                "  • Use orcid_works_count MCP tool for academic publication counts\n"
                "  • Use fetch_pdf_text MCP tool with a direct DOI or PDF URL\n"
                "  • Use python_sandbox MCP tool to run calculations if needed\n"
            )
        else:
            _depth_hint = (
                "SEARCH STRATEGY (Depth 3 — alternative angles):\n"
                "  • Try synonyms, abbreviations, or alternative spellings of the key term\n"
                "  • Search for the source publication/author directly\n"
                "  • Use fetch_pdf_text with any relevant paper URL found\n"
            )

        if _strategy_hint:
            _depth_hint += f"  • Suggested approach from gap analysis: {_strategy_hint}\n"

        # Domains discovered in previous searches — offered as targeted follow-up targets
        _disc_domains: list = state_.get("discovered_domains") or []
        if _disc_domains:
            _domain_lines = "\n".join(
                f"  • {d['domain']}" + (f" — {d['context']}" if d.get("context") else "")
                for d in _disc_domains[:8]
            )
            _discovered_block = (
                "\nSOURCES FOUND IN PREVIOUS SEARCHES (consider using web_search_domain with these):\n"
                f"{_domain_lines}\n"
            )
        else:
            _discovered_block = ""

        _agentic_context_block = (
            f"\n=== AGENTIC ITERATION {_agentic_iteration}/{_agentic_max_rounds} ===\n"
            f"{_context_facts}\n\n"
            f"Still unresolved:\n{_gap[:800]}\n"
            f"{_search_history_block}"
            f"{_discovered_block}"
            f"{_fail_block}\n"
            f"{_depth_hint}\n"
            "Instructions: Focus ONLY on resolving the gap above. "
            "Do NOT repeat subtasks already answered. "
            "Generate DIFFERENT search queries or use specialized MCP tools instead of repeating web search.\n"
            "=== END AGENTIC CONTEXT ===\n"
        )
        await _m._report(
            f"🔄 Agentic Loop — Iteration {_agentic_iteration}/{_agentic_max_rounds} (Depth {_depth})\n"
            f"📌 Still open: {_gap[:120]}"
        )
        # Clear single-string result fields so old results don't bleed into new iteration.
        # NOTE: working_memory / tool_calls_log / tool_failures / attempted_queries are intentionally preserved.
        _agentic_state_reset = {"web_research": "", "mcp_result": "", "math_result": ""}
        logger.info(f"🔄 Agentic re-plan iteration {_agentic_iteration}/{_agentic_max_rounds} depth={_depth}: gap={_gap[:80]}")

    prompt = f"""{_planner_role}{_agentic_context_block}

IMPORTANT: Answer EXCLUSIVELY with a JSON array of objects. No text, no explanations, no markdown.
Each object MUST contain the fields "task" (string) and "category" (string).

VALID CATEGORIES FOR LLM EXPERTS: {expert_categories}

WEB RESEARCH — for current/external info OR for domain specifications in implementation tasks:
{{"task": "task description", "category": "research", "search_query": "short optimized search term"}}
Use for: game rules · algorithm specifications · protocols/standards · anything where correct logic is critical for implementation.

PRECISION TOOLS — MANDATORY for all exact calculations (LLMs calculate WRONG!):
REQUIRED for: arithmetic · subnet/IP/CIDR · date/time · units · hashes · regex · statistics
{_m._build_filtered_tool_desc(state_["input"], enable_graphrag=state_.get("enable_graphrag", False)) if state_.get("complexity_level") != "trivial" else "  - calculate: arithmetic and math  - date_diff: date calculations  - unit_convert: unit conversions"}
Format: {{"task": "task description", "category": "precision_tools", "mcp_tool": "<toolname>", "mcp_args": {{<args>}}}}
{_agentic_code_block}
LEGAL RESEARCH — for questions about German law (laws, paragraphs, legal norms):
Use the legal_* tools to retrieve exact legal texts; ALWAYS combine with legal_advisor expert for interpretation.
Typical pattern:
  1. legal_search_laws → finds relevant laws when abbreviation is unknown
  2. legal_get_law_overview → shows all §§ when paragraph is unknown
  3. legal_get_paragraph → retrieves exact legal text (REQUIRED for §-questions!)
  4. legal_fulltext_search → keyword search within a law
  5. legal_advisor expert → interprets, explains, applies

EXAMPLE legal question:
Request: "What does § 242 BGB say?"
Correct: [{{"task": "Get § 242 BGB legal text", "category": "precision_tools", "mcp_tool": "legal_get_paragraph", "mcp_args": {{"law": "BGB", "paragraph": "242"}}}}, {{"task": "Explain § 242 BGB (good faith) — meaning, elements, legal consequences, case examples", "category": "legal_advisor"}}]
WRONG: [{{"task": "What does § 242 BGB say?", "category": "legal_advisor"}}]
← ERROR: legal text missing — LLM hallucinate legal text!

VISION EXPERT — for image and document processing:
- "vision": REQUIRED when [IMAGE INPUT present] is in the input or the user explicitly wants images/photos/screenshots/diagrams/documents analyzed.
- For combined requests (image + code/text): vision task FIRST, then further experts with vision task result as context.

RULES:
- precision_tools has ABSOLUTE PRIORITY — NEVER use "math" or "technical_support" for calculations!
- Legal questions → ALWAYS get legal_get_paragraph AND legal_advisor expert for interpretation
- Subnet mask / IP / CIDR / gateway → ALWAYS subnet_calc, NEVER technical_support
- Regex extraction from text → ALWAYS regex_extract, NEVER technical_support
- For implementations with domain-specific logic (games, algorithms, protocols): research task FIRST, then code tasks
- Task descriptions for code experts MUST contain all known rules/specifications (logic, constraints, algorithm details) — experts only see their task description!
- Simple requests → exactly one task, no overengineering
- NEVER just keywords or questions as tasks — always concrete task descriptions!
- OPTIONAL: Add a "metadata_filters" key to the FIRST task object when the domain is unambiguous, to scope downstream memory retrieval. Use string values only. Omit when unsure.
  Example: {{"task": "...", "category": "code_reviewer", "metadata_filters": {{"expert_domain": "code_reviewer", "project": "frontend"}}}}
{_m._build_skill_catalog()}
{_quality_hint}{success_hint}{_few_shot_hint}
EXAMPLE calculation:
Request: "What subnet mask for 10.42.155.160/27 with 14 hosts?"
Correct: [{{"task": "Subnet info for 10.42.155.160/27", "category": "precision_tools", "mcp_tool": "subnet_calc", "mcp_args": {{"cidr": "10.42.155.160/27"}}}}]
WRONG:   [{{"task": "Calculate subnet mask", "category": "technical_support"}}]

EXAMPLE game implementation with domain logic:
Request: "Create a Connect Four game as HTML5 page"
Correct: [
  {{"task": "Research Connect Four rules and correct implementation details (column click, gravity, win detection)", "category": "research", "search_query": "Connect Four rules implementation falling pieces column click win detection algorithm"}},
  {{"task": "Implement Connect Four in HTML5/CSS/JS. MANDATORY RULES: 7 columns × 6 rows; click on column → piece falls to LOWEST free row (not inserted at top!); win = 4 in a row horizontal/vertical/diagonal; move invalid when column full", "category": "code_reviewer"}}
]
WRONG: [{{"task": "Implement HTML5 base structure", "category": "code_reviewer"}}, {{"task": "Write JS game logic", "category": "code_reviewer"}}]
← ERROR: game rules missing from task description, no research task, logic will be implemented incorrectly

EXAMPLE simple request:
Request: "What is Docker?"
Correct: [{{"task": "Explain what Docker is and what it is used for", "category": "technical_support"}}]
WRONG:   ["Docker", "Container", "Virtualization"]

Request: {state_['input']}

JSON array:"""
    await _m._report(f"📋 Planner prompt ({len(prompt)} chars):\n{prompt}")
    total_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    plan: Optional[list] = None
    from parsing import _extract_usage, _extract_json
    from config import PLANNER_URL, PLANNER_MODEL, PLANNER_TOKEN
    for attempt in range(PLANNER_RETRIES):
        _planner_llm_inst = await _get_planner_llm(state_)
        _planner_url = (state_.get("planner_url_override") or PLANNER_URL or "").rstrip("/")
        _planner_llm_inst = _planner_llm_inst.bind(temperature=_query_temp)
        res, _planner_fb = await _invoke_llm_with_fallback(
            _planner_llm_inst, _planner_url, prompt,
            timeout=PLANNER_TIMEOUT, label="Planner",
        )
        if _planner_fb:
            await _m._report("⚠️ Planner: used local fallback (primary endpoint degraded)")
        u = _extract_usage(res)
        total_usage["prompt_tokens"]     += u["prompt_tokens"]
        total_usage["completion_tokens"] += u["completion_tokens"]
        try:
            # Strip markdown code fences that some models wrap around JSON
            _plan_text = res.content.strip()
            _plan_text = re.sub(r'^```\w*\n?', '', _plan_text)
            _plan_text = re.sub(r'\n?```$', '', _plan_text)
            raw  = json.loads(re.search(r'\[.*\]', _plan_text, re.S).group())
            # Extract optional metadata_filters from first task before sanitizing
            _extracted_filters: Dict = {}
            if raw and isinstance(raw[0], dict) and "metadata_filters" in raw[0]:
                _extracted_filters = raw[0].pop("metadata_filters", {})
                if not isinstance(_extracted_filters, dict):
                    _extracted_filters = {}
            # Extract optional output_skill suggestion from any task
            _output_skill = ""
            for _raw_task in raw:
                if isinstance(_raw_task, dict) and _raw_task.get("output_skill"):
                    _skill_name = str(_raw_task.pop("output_skill")).strip().lstrip("/")
                    from services.skills import _load_skill_body
                    _skill_body = _load_skill_body(_skill_name)
                    if _skill_body:
                        _output_skill = _skill_body
                        logger.info(f"🎯 Planner suggested skill: /{_skill_name}")
                    break
            plan = _sanitize_plan(raw, state_["input"])
            categories = [t.get("category", "?") for t in plan]
            logger.info(f"📋 Plan ({len(plan)} Tasks): {json.dumps(plan, ensure_ascii=False)}")
            await _m._report(f"📋 Plan: {len(plan)} Task(s) → {', '.join(categories)}")
            for _pt in plan:
                _desc = (_pt.get("task") or "")[:80]
                _ptcat = _pt.get("category", "?")
                _extra = "…" if len(_pt.get("task", "")) > 80 else ""
                await _m._report(f"  • [{_ptcat}] {_desc}{_extra}")
            await _m._report(
                f"📋 Planner done — {total_usage['prompt_tokens']} prompt tok / "
                f"{total_usage['completion_tokens']} completion tok"
            )
            break
        except Exception:
            if attempt == 0:
                logger.warning(f"Planner parse error (attempt 1) — retry. Output: {res.content[:200]!r}")
                await _m._report("⚠️ Planner: JSON error — retrying...")
                continue
            logger.warning(f"Planner could not parse JSON — fallback. Output: {res.content[:200]!r}")
            await _m._report("⚠️ Planner-Fallback: general")
            plan = [{"task": state_["input"], "category": "general"}]
            _extracted_filters = {}
    # Unload planner model — unless the same model is immediately needed as expert.
    # Use the template-specific planner model/URL when the template overrides them,
    # so we unload from the correct node instead of always hitting the global default.
    _actual_planner_model = (state_.get("planner_model_override") or PLANNER_MODEL).strip()
    _actual_planner_url   = (state_.get("planner_url_override")   or PLANNER_URL or "").strip()
    _actual_planner_base  = _actual_planner_url.rstrip("/").removesuffix("/v1")
    _upcoming_expert_models: set = set()
    for _task_item in plan:
        _cat = _task_item.get("category", "general")
        _experts_for_cat = (state_.get("user_experts") or {}).get(_cat) or EXPERTS.get(_cat, [])
        for _e in _experts_for_cat:
            if _e.get("model"):
                _upcoming_expert_models.add(_e["model"])
    # Strip @endpoint suffix from expert model names for comparison
    _upcoming_base_models = {m.split("@")[0] for m in _upcoming_expert_models}
    if _actual_planner_model in _upcoming_base_models:
        logger.debug(f"⏭️ VRAM unload skipped: {_actual_planner_model} will be reused as expert")
    elif _actual_planner_base:
        asyncio.create_task(_ollama_unload(_actual_planner_model, _actual_planner_base))
    # Cache plan in Valkey for reuse (fail-safe)
    if state.redis_client is not None and plan:
        asyncio.create_task(state.redis_client.setex(_plan_cache_key, 1800, json.dumps(plan)))
    if _extracted_filters:
        logger.info(f"📋 Planner metadata_filters: {_extracted_filters}")
    _skill_state = {"output_skill_body": _output_skill} if _output_skill else {}
    return {"plan": plan, "metadata_filters": _extracted_filters,
            **total_usage, **_complexity_state_update, **_skill_state, **_agentic_state_reset}


def _topological_levels(tasks: list[tuple[int, dict]]) -> list[list[tuple[int, dict]]]:
    """Group (index, task) pairs into dependency levels for mixed parallel/sequential execution.

    Tasks within the same level have no dependency on each other and run in parallel.
    A task in level N+1 depends on at least one task in level <= N.

    Tasks with no 'depends_on' field (or with an unresolvable dependency) are placed
    in level 0 and run immediately in parallel with other independent tasks.
    """
    id_to_idx: dict[str, int] = {}
    for orig_idx, t in tasks:
        tid = t.get("id", "")
        if tid:
            id_to_idx[tid] = orig_idx

    levels: list[list[tuple[int, dict]]] = []
    placed: set[str] = set()          # task IDs that have been scheduled
    placed_orig: set[int] = set()     # original indices of placed tasks
    remaining = list(tasks)

    while remaining:
        # A task is ready if its dependency is already placed (or it has none)
        ready = []
        still_waiting = []
        for item in remaining:
            orig_idx, t = item
            dep = t.get("depends_on", "")
            if not dep or dep in placed:
                ready.append(item)
            else:
                still_waiting.append(item)

        if not ready:
            # Circular or unresolvable dependency — place all remaining as independent
            levels.append(still_waiting)
            break

        levels.append(ready)
        for orig_idx, t in ready:
            tid = t.get("id", "")
            if tid:
                placed.add(tid)
            placed_orig.add(orig_idx)
        remaining = still_waiting

    return levels


def _inject_prior_results(task: dict, prior_outputs: dict[str, str]) -> dict:
    """Substitute {result_of:task_id} placeholders in task fields with prior expert outputs.

    Creates a shallow copy of task with placeholders replaced so the dependent
    expert receives concrete context from its predecessor.

    prior_outputs: {task_id: expert_output_text (trimmed to ~400 chars)}
    """
    if not prior_outputs:
        return task

    def _sub(text: str) -> str:
        import re as _re
        def _replace(m: re.Match) -> str:
            tid = m.group(1).strip()
            val = prior_outputs.get(tid, "")
            return val[:400] if val else f"[result_of:{tid} — not available]"
        return _re.sub(r'\{result_of:([^}]+)\}', _replace, text)

    out = dict(task)
    for field in ("task", "search_query", "mcp_args"):
        if isinstance(out.get(field), str):
            out[field] = _sub(out[field])
        elif isinstance(out.get(field), dict):
            out[field] = {k: _sub(v) if isinstance(v, str) else v
                          for k, v in out[field].items()}
    return out


# _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD imported from config.py


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
    await _m._report(
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


async def expert_worker(state_: AgentState):
    if state_.get("cache_hit"):
        return {"expert_results": []}

    NON_EXPERT_CATEGORIES = {"precision_tools", "research"}
    plan         = state_.get("plan", [])
    chat_history = state_.get("chat_history") or []
    expert_tasks = [
        (i, t) for i, t in enumerate(plan)
        if isinstance(t, dict) and t.get("category", "general") not in NON_EXPERT_CATEGORIES
    ]
    if not expert_tasks:
        return {"expert_results": []}

    logger.info(f"--- [NODE] EXPERTS ({len(expert_tasks)} Tasks, Two-Tier) ---")

    from langchain_openai import ChatOpenAI
    from parsing import _extract_usage, _parse_expert_confidence
    from config import LITELLM_URL, INFERENCE_SERVERS_LIST, URL_MAP
    from services.inference import _endpoint_semaphores

    async def run_single(model_cfg: dict, task_item: dict, t_idx: int, e_idx: int) -> dict:
        model_name = model_cfg["model"]
        if model_cfg.get("_user_conn_url"):
            # User-owned API connection: URL/token pre-resolved, bypass node selection
            url       = model_cfg["_user_conn_url"]
            token     = model_cfg["_user_conn_token"]
            api_type  = model_cfg.get("_user_conn_api_type", "openai")
            endpoint  = model_cfg.get("endpoint", "user-conn")
            semaphore = asyncio.Semaphore(4)
        elif LITELLM_URL:
            # Flange: LiteLLM gateway handles endpoint selection, retries, circuit breaker
            url        = f"{LITELLM_URL}/v1"
            token      = "sk-litellm-internal"
            api_type   = "openai"
            endpoint   = "litellm-proxy"
            semaphore  = asyncio.Semaphore(999)  # LiteLLM manages its own concurrency
        else:
            # Direct access (default): _select_node() selects based on tier/warm/load
            raw_ep     = model_cfg.get("endpoints") or [model_cfg.get("endpoint", "")]
            # Floating mode: if endpoint is empty, search ALL nodes for the model
            if not raw_ep or raw_ep == [""] or raw_ep == [""]:
                raw_ep = [s["name"] for s in INFERENCE_SERVERS_LIST]
                logger.info(f"🌐 Floating mode: searching all {len(raw_ep)} nodes for {model_name}")
            selected   = await _select_node(model_name, raw_ep, user_id=state_.get("user_id", ""))
            endpoint   = selected["name"]
            url        = selected.get("url") or URL_MAP.get(endpoint)
            token      = selected.get("token", "ollama")
            api_type   = selected.get("api_type", "ollama")
            semaphore  = _endpoint_semaphores.get(endpoint, asyncio.Semaphore(1))
        async with semaphore:
            task_text  = task_item.get("task", str(task_item))
            cat        = task_item.get("category", "general")
            gpu        = await assign_gpu(endpoint)
            PROM_EXPERT_CALLS.labels(model=model_name, category=cat, node=endpoint).inc()

            mode        = state_.get("mode", "default")
            mode_cfg    = MODES.get(mode, MODES["default"])
            sys_prompt = (
                _m._get_expert_prompt(cat, state_.get("user_experts"))
                + mode_cfg["expert_suffix"]
                + _m._conf_format_for_mode(mode)
            )
            # Agent mode: embed file/code context from the client's system message
            agent_ctx = state_.get("system_prompt", "")
            if agent_ctx and mode in ("agent", "agent_orchestrated"):
                sys_prompt += f"\n\n--- USER CODE CONTEXT ---\n{agent_ctx[:4000]}"
            # Inject correction memory for this category (avoids repeat mistakes)
            if CORRECTION_MEMORY_ENABLED and state.graph_manager is not None:
                try:
                    from graph_rag.corrections import query_corrections as _query_corrections, format_correction_context as _format_correction_context
                    _driver = state.graph_manager.driver if hasattr(state.graph_manager, 'driver') else None
                    _corr = await _query_corrections(_driver, state_.get("input", ""), cat)
                    _corr_ctx = _format_correction_context(_corr)
                    if _corr_ctx:
                        PROM_CORRECTIONS_INJECTED.labels(category=cat).inc(len(_corr))
                        sys_prompt += f"\n\n{_corr_ctx}"
                except Exception:
                    pass
            # Resolve per-expert context window: template override → Ollama API (cached) → static table
            from context_budget import get_model_ctx_async as _ctx_async
            _expert_ctx_override = int(model_cfg.get("context_window", 0) or 0)
            _expert_ctx_window = await _ctx_async(
                model=model_name,
                base_url=url or "",
                token=token,
                redis_client=state.redis_client,
                override=_expert_ctx_override,
            )
            # Derive per-expert output and input limits from context window
            _expert_max_output = MAX_EXPERT_OUTPUT_CHARS
            if _expert_ctx_window > 0:
                # Reserve 25% of window for output, capped at global MAX_EXPERT_OUTPUT_CHARS
                _expert_max_output = min(MAX_EXPERT_OUTPUT_CHARS, max(1000, _expert_ctx_window // 4))
                # Truncate task_text if sys_prompt + task would overflow the context
                _max_input_chars = max(2000, _expert_ctx_window * 3)  # 3 chars/token estimate
                _available_task_chars = _max_input_chars - len(sys_prompt)
                if len(task_text) > _available_task_chars > 0:
                    task_text = task_text[:_available_task_chars] + "\n[…truncated for context window]"

            logger.info(f"🚀 Expert {t_idx}.{e_idx} GPU#{gpu} [{model_name} / {cat}]")

            # Build messages list: system + history + user turn (with optional image blocks)
            messages: List[Dict] = [{"role": "system", "content": sys_prompt}]
            if chat_history:
                messages.extend(chat_history)

            # Attach images as multimodal content when present (OpenAI image_url format).
            expert_images = state_.get("images") or []
            if expert_images:
                user_content: List[Dict] = [{"type": "text", "text": task_text}]
                for img in expert_images:
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"},
                    })
                messages.append({"role": "user", "content": user_content})
            else:
                messages.append({"role": "user", "content": task_text})

            await _m._report(
                f"🚀 Expert [{model_name} / {cat}] GPU#{gpu}\n"
                f"  Task: {task_text}"
                + (f" | ctx={_expert_ctx_window//1024}K" if _expert_ctx_window else "")
            )
            await _m._report(
                f"📤 Expert [{model_name} / {cat}] System-Prompt:\n{sys_prompt}"
            )
            expert_base_url = url.rstrip("/").removesuffix("/v1")
            _expert_node_timeout = float(
                selected.get("timeout", EXPERT_TIMEOUT)
                if not LITELLM_URL and not model_cfg.get("_user_conn_url")
                else EXPERT_TIMEOUT
            )
            _expert_temp = state_.get("query_temperature")  # None = API default
            _llm_kwargs: dict = {"model": model_name, "base_url": url, "api_key": token,
                                 "timeout": _expert_node_timeout}
            if _expert_temp is not None:
                _llm_kwargs["temperature"] = _expert_temp
            llm = ChatOpenAI(**_llm_kwargs)
            from metrics import PROM_TOKENS
            try:
                _primary_url = url.rstrip("/")
                res, _used_fallback = await _invoke_llm_with_fallback(
                    llm, _primary_url, messages,
                    timeout=_expert_node_timeout,
                    label=f"Expert[{cat}]",
                )
                if _used_fallback:
                    await _m._report(f"⚠️ Expert [{cat}]: used local fallback (primary endpoint degraded)")
                usage = _extract_usage(res)
                content = res.content[:_expert_max_output]
                if len(res.content) > _expert_max_output:
                    content += "\n[…truncated]"
                await _m._report(f"✅ Expert [{model_name} / {cat}]:\n{content}\n---")
                # Token metrics
                _uid = state_.get("user_id", "anon")
                PROM_TOKENS.labels(model=model_name, token_type="prompt",      node=endpoint, user_id=_uid).inc(usage.get("prompt_tokens", 0))
                PROM_TOKENS.labels(model=model_name, token_type="completion",  node=endpoint, user_id=_uid).inc(usage.get("completion_tokens", 0))
                # Confidence automatically as performance signal → no waiting for user feedback needed
                conf = _parse_expert_confidence(content)
                await _m._report(
                    f"  → [{model_name}/{cat}] Confidence: {conf or '?'} | "
                    f"{usage.get('prompt_tokens', 0)}→{usage.get('completion_tokens', 0)} tok"
                )
                PROM_CONFIDENCE.labels(level=conf or "unknown", category=cat).inc()
                if conf == "high":
                    asyncio.create_task(_record_expert_outcome(model_name, cat, positive=True))
                elif conf == "low":
                    asyncio.create_task(_record_expert_outcome(model_name, cat, positive=False))
                # "medium" → no signal (neutral, do not increment counter)
                # Unload model — unless the same model is needed as judge LLM
                if api_type == "ollama":
                    _judge_model_name = (state_.get("judge_model_override") or JUDGE_MODEL).strip()
                    if model_name == _judge_model_name:
                        logger.debug(f"⏭️ VRAM unload skipped: {model_name} will be reused as judge")
                    else:
                        asyncio.create_task(_ollama_unload(model_name, expert_base_url))
                res_prefix = f"ENSEMBLE: {model_name.upper()}" if model_cfg.get("forced") else model_name.upper()
                result = {"res": f"[{res_prefix} / {cat}]: {content}", "model_cat": f"{model_name}::{cat}", **usage}
                # User-owned connection tokens are tracked separately for budget exclusion.
                if model_cfg.get("_user_conn_url"):
                    result["user_conn_prompt_tokens"]     = usage.get("prompt_tokens", 0)
                    result["user_conn_completion_tokens"] = usage.get("completion_tokens", 0)
                    result["prompt_tokens"]     = 0
                    result["completion_tokens"] = 0
                return result
            except Exception as e:
                err = str(e)
                # Distinguish real GPU errors (VRAM/CUDA) from network/timeout errors
                is_vram = any(x in err.lower() for x in
                              ("cudamalloc", "out of memory", "oom", "transfer encoding",
                               "not enough data", "cuda error"))
                if is_vram:
                    PROM_EXPERT_FAILURES.labels(model=model_name, reason="vram").inc()
                    logger.error(f"❌ VRAM/HTTP error GPU#{gpu} {model_name}: {e}")
                    await _m._report(f"❌ Expert {model_name}: GPU/HTTP error")
                    return {"res": f"[{model_name} ERROR]: VRAM/HTTP", "model_cat": None}
                PROM_EXPERT_FAILURES.labels(model=model_name, reason="error").inc()
                logger.error(f"❌ Expert {model_name}: {e}")
                await _m._report(f"❌ Expert {model_name}: error")
                return {"res": f"[{model_name} ERROR]: {e}", "model_cat": None}

    async def run_task(i: int, task: dict) -> List[dict]:
        """Two-Tier-Logik + Forced-Parallel-Ensemble.

        Forced models always run — independent of score/tier — simultaneously with T1.
        Intended for ensemble approaches: evaluate different providers/training data in parallel.
        """
        cat              = task.get("category", "general")
        effective_experts = state_.get("user_experts") or EXPERTS
        all_experts = [e for e in effective_experts.get(cat, effective_experts.get("general", EXPERTS.get(cat, EXPERTS.get("general", [])))) if e.get("enabled", True)]

        forced_experts = [e for e in all_experts if e.get("forced", False)]
        normal_experts = [e for e in all_experts if not e.get("forced", False)]

        scored = []
        for e in normal_experts:
            score = await _get_expert_score(e["model"], cat)
            scored.append((score, e))
        scored.sort(key=lambda x: -x[0])

        tier1 = [(s, e) for s, e in scored if e.get("_tier", 1) == 1 and s >= EXPERT_MIN_SCORE]
        tier2 = [(s, e) for s, e in scored if e.get("_tier", 2) == 2 and s >= EXPERT_MIN_SCORE]

        # If no T1 results available, treat all normal results as T1
        if not tier1:
            tier1, tier2 = tier2, []

        # Cost-tier enforcement: trivial tasks use at most one T1 expert — skips T2
        # entirely and keeps only the best-scored T1 to reduce token consumption.
        if state_.get("force_tier1") and tier1:
            tier1 = tier1[:1]  # only the top-scored T1 expert
            tier2 = []         # T2 never runs for trivial cost-tier tasks

        task_results: List[dict] = []

        # Forced + T1 start in parallel in one batch
        parallel_batch = (
            [run_single(e, task, i + 1, j + 1) for j, e in enumerate(forced_experts)] +
            [run_single(e, task, i + 1, len(forced_experts) + j + 1) for j, (_, e) in enumerate(tier1)]
        )

        if forced_experts:
            forced_names = ", ".join(e["model"] for e in forced_experts)
            await _m._report(f"🔀 Forced-Ensemble [{cat}]: {forced_names}")
        if tier1:
            t1_names = ", ".join(e["model"] for _, e in tier1)
            await _m._report(f"⚡ T1 [{cat}]: {t1_names}")

        if parallel_batch:
            combined = await asyncio.gather(*parallel_batch)
            n_forced      = len(forced_experts)
            forced_results = list(combined[:n_forced])
            t1_results     = list(combined[n_forced:])
            task_results.extend(forced_results)
            task_results.extend(t1_results)

            if t1_results:
                t1_has_high = any(
                    _parse_expert_confidence(r.get("res", "")) == "high"
                    for r in t1_results if r.get("res")
                )
                if t1_has_high or not tier2:
                    if t1_has_high:
                        logger.info(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                        await _m._report(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                    return task_results
            elif not tier2:
                return task_results

        if tier2:
            t2_names = ", ".join(e["model"] for _, e in tier2)
            await _m._report(f"🔬 T2 [{cat}]: {t2_names} (T1 insufficient)")
            t2_results = await asyncio.gather(
                *[run_single(e, task, i + 1, len(forced_experts) + len(tier1) + j + 1)
                  for j, (_, e) in enumerate(tier2)]
            )
            task_results.extend(t2_results)

        return task_results

    # Dynamic parallel/sequential execution via dependency levels.
    # Tasks with no 'depends_on' run in parallel (level 0).
    # Tasks whose 'depends_on' points to a level-N task run in level N+1.
    # Within each level, all tasks run in parallel (asyncio.gather).
    levels = _topological_levels(expert_tasks)
    has_deps = any(t.get("depends_on") for _, t in expert_tasks)
    if has_deps:
        logger.info(f"⛓️ Expert execution: {len(levels)} dependency level(s) "
                    f"({[len(lvl) for lvl in levels]} tasks per level)")

    all_results: List[dict] = []
    prior_outputs: dict[str, str] = {}  # task_id → trimmed expert output for placeholder injection

    for lvl_idx, level in enumerate(levels):
        # Inject results from prior levels into {result_of:id} placeholders
        injected_level = [(i, _inject_prior_results(t, prior_outputs)) for i, t in level]

        if has_deps and len(levels) > 1:
            level_ids = [t.get("id", f"task{i}") for i, t in level]
            await _m._report(
                f"⛓️ Dependency level {lvl_idx + 1}/{len(levels)}: "
                f"running {len(level)} task(s) in parallel — [{', '.join(level_ids)}]"
            )

        level_groups = await asyncio.gather(
            *[run_task(i, task) for i, task in injected_level]
        )
        level_results = [r for group in level_groups for r in group]
        all_results.extend(level_results)

        # Collect outputs for downstream placeholder injection
        for (orig_idx, orig_task), group in zip(injected_level, level_groups):
            tid = orig_task.get("id", "")
            if tid:
                combined = " | ".join(r.get("res", "") for r in group if r.get("res"))
                prior_outputs[tid] = combined[:400]

    used = [r["model_cat"] for r in all_results if r.get("model_cat")]
    return {
        "expert_results":              [r["res"] for r in all_results if "res" in r],
        "expert_models_used":          used,
        "prompt_tokens":               sum(r.get("prompt_tokens",               0) for r in all_results),
        "completion_tokens":           sum(r.get("completion_tokens",           0) for r in all_results),
        "user_conn_prompt_tokens":     sum(r.get("user_conn_prompt_tokens",     0) for r in all_results),
        "user_conn_completion_tokens": sum(r.get("user_conn_completion_tokens", 0) for r in all_results),
    }


async def research_node(state_: AgentState):
    if state_.get("cache_hit"):
        return {"web_research": ""}
    # Template toggle: skip web research if disabled
    if not state_.get("enable_web_research", True):
        logger.info("Web research disabled by template toggle")
        return {"web_research": ""}
    # Complexity routing: trivial requests skip research
    if state_.get("skip_research"):
        logger.info("⚡ Research node skipped (trivial/moderate routing)")
        return {"web_research": ""}
    plan = state_.get("plan", [])
    research_tasks = [t for t in plan if isinstance(t, dict) and t.get("category") == "research"]
    if not research_tasks:
        return {"web_research": ""}

    mode = state_.get("mode", "default")
    _is_agentic_replan = state_.get("agentic_iteration", 0) > 0 and state_.get("agentic_max_rounds", 0) > 0
    _prev_queries: list = list(state_.get("attempted_queries") or [])

    # Search cache TTL strategy:
    # - First pass (iteration 0): 24h cache reads + writes — reproducible results
    # - Agentic re-plan (iteration > 0): skip cache reads (force fresh), write with 2h TTL
    #   This ensures gap-filling searches always see current web content, and any bad
    #   agentic-round results expire quickly rather than poisoning subsequent runs.
    _SEARCH_CACHE_TTL      = 86400  # 24 h for first-pass searches
    _SEARCH_CACHE_TTL_AGNT = 7_200  # 2 h for agentic re-plan searches

    def _search_cache_key(query: str, domain_hint: str = "") -> str:
        """Build a domain-aware cache key.

        Including the domain in the key means that a domain-restricted follow-up
        search (e.g., site:tardis.fandom.com) never hits a broad-query cache entry
        for the same topic — each domain+query pair caches independently.
        """
        raw = f"{domain_hint.lower()}::{query}" if domain_hint else query
        return f"moe:search_cache:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"

    async def _fetch_one(task: dict, idx: int, total: int) -> tuple[str, str, str]:
        """Execute a single search with domain-aware Redis caching.

        Returns (result_text, query, quality).

        Cache behaviour:
          - iteration 0: read + write at 24h TTL (reproducibility)
          - iteration > 0 (agentic): skip read (force fresh), write at 2h TTL
        Domain hint is extracted from 'domain' field (web_search_domain calls) or
        from site: syntax in the query to ensure domain-specific cache isolation.
        """
        query = task.get("search_query", state_["input"])
        domain_hint = task.get("domain", "")
        # Also extract site: prefix if planner embedded it in the query string
        if not domain_hint:
            _site_m = re.search(r'\bsite:(\S+)', query)
            if _site_m:
                domain_hint = _site_m.group(1)
        _cache_key = _search_cache_key(query, domain_hint)
        _ttl = _SEARCH_CACHE_TTL_AGNT if _is_agentic_replan else _SEARCH_CACHE_TTL

        # Cache read: skipped during agentic re-plan to guarantee fresh results
        if state.redis_client is not None and not _is_agentic_replan:
            try:
                cached = await state.redis_client.get(_cache_key)
                if cached:
                    logger.debug("🗄️ Search cache hit: %s", query[:60])
                    return cached.decode("utf-8", errors="replace"), query, "ok-cached"
            except Exception:
                pass

        await _m._report(f"🌐 Web search [{idx+1}/{total}]: '{query[:60]}'...")
        raw = await _m._web_search_with_citations(query, ddg_fallback=state_.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG))
        # Quality classification drives both the result flag and the cache TTL.
        # Poor results (empty or < 500 chars) are only cached for 30 min to prevent
        # poisoning subsequent runs with stale, low-signal snippets.
        if raw and len(raw) > 500:
            quality = "ok"
            _effective_ttl = _ttl
        elif raw and len(raw) > 100:
            quality = "partial"
            _effective_ttl = min(_ttl, 1800)  # 30 min for short results
        else:
            quality = "empty"
            _effective_ttl = 0  # do not cache empty results

        if raw:
            await _m._report(f"🌐 Search [{idx+1}] result ({len(raw)} chars, quality={quality})")
            if state.redis_client is not None and _effective_ttl > 0:
                try:
                    await state.redis_client.set(_cache_key, raw.encode("utf-8"), ex=_effective_ttl)
                except Exception:
                    pass
            return raw, query, quality

        await _m._report(f"🌐 Search [{idx+1}]: no result (SearXNG unreachable or empty)")
        return "", query, "empty"

    if mode in ("research", "plan") or _is_agentic_replan:
        # Deep research mode OR agentic re-plan: run ALL tasks in parallel for maximum coverage.
        # In agentic re-plan we need every available search result to resolve the gap.
        logger.info(f"--- [NODE] WEB RESEARCH (MULTI — {len(research_tasks)} queries, agentic={_is_agentic_replan}) ---")
        await _m._report(f"🌐 {'Agentic re-search' if _is_agentic_replan else 'Deep research'}: {len(research_tasks)} parallel search(es)...")

        raw_results = await asyncio.gather(*[
            _fetch_one(t, i, len(research_tasks)) for i, t in enumerate(research_tasks)
        ])
        combined_parts = [f"[Search {i+1}: {q}]:\n{r}" for i, (r, q, _) in enumerate(raw_results) if r]
        combined = "\n\n".join(combined_parts)
        new_query_records = [{"query": q, "quality": qlt} for _, q, qlt in raw_results]

        # Extract authoritative domains from all results for agentic follow-up
        _prev_domains: list = list(state_.get("discovered_domains") or [])
        _known_domains = {d["domain"] for d in _prev_domains}
        _new_domains = [
            d for r, _, _ in raw_results if r
            for d in _extract_authoritative_domains(r)
            if d["domain"] not in _known_domains
        ]
        _all_domains = _prev_domains + _new_domains

        await _m._report(
            f"🌐 Multi-search complete: {len(combined)} chars total ({sum(1 for r, _, _ in raw_results if r)} hits)"
            if combined else "🌐 Multi-search: no results"
        )
        return {
            "web_research": combined,
            "attempted_queries": _prev_queries + new_query_records,
            "discovered_domains": _all_domains,
        }
    else:
        # Standard single-search mode
        query = research_tasks[0].get("search_query", state_["input"])
        logger.info(f"--- [NODE] WEB RESEARCH: '{query[:80]}' ---")
        raw, q, quality = await _fetch_one(research_tasks[0], 0, 1)
        _prev_domains = list(state_.get("discovered_domains") or [])
        _known_domains = {d["domain"] for d in _prev_domains}
        _new_domains = [
            d for d in _extract_authoritative_domains(raw)
            if d["domain"] not in _known_domains
        ] if raw else []
        return {
            "web_research": raw,
            "attempted_queries": _prev_queries + [{"query": q, "quality": quality}],
            "discovered_domains": _prev_domains + _new_domains,
        }


# ─── Domain Discovery ─────────────────────────────────────────────────────────

# Domains that carry no domain-specific authority — filtered out so the
# re-planner is not offered generic search infrastructure as "sources".
_GENERIC_DOMAINS: frozenset[str] = frozenset({
    "google.com", "google.de", "bing.com", "duckduckgo.com", "yahoo.com",
    "baidu.com", "yandex.ru", "ask.com", "search.yahoo.com",
    "t.co", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "reddit.com", "quora.com", "pinterest.com", "linkedin.com",
    "youtube.com",           # transcripts go through youtube_transcript tool instead
    "amazon.com", "ebay.com", "etsy.com",
    "cloudflare.com", "amazonaws.com", "akamai.com",
    "w3.org", "schema.org", "openstreetmap.org",
    "fonts.googleapis.com", "gstatic.com",
})


def _extract_authoritative_domains(web_result_text: str) -> list[dict]:
    """Parse URLs from a web-search result block and return authoritative domains.

    Each returned dict has the shape:
        {"domain": str, "context": str}   # context = a short hint why it looks useful

    Domains that appear in _GENERIC_DOMAINS are silently dropped. The caller
    accumulates these across iterations so the re-planner can offer them as
    targeted follow-up search targets via web_search_domain.
    """
    import urllib.parse as _urlparse
    _URL_RE = re.compile(r'https?://[^\s\)"\'<>]+')
    seen: set[str] = set()
    result: list[dict] = []

    def _classify(domain: str) -> str:
        if any(domain.endswith(s) for s in (".fandom.com", ".wikia.com", ".wiki.gg")):
            return "fan wiki"
        if "wikipedia.org" in domain:
            return "encyclopedia"
        if any(x in domain for x in ("museum", "smithsonian", "getty", "moma")):
            return "museum database"
        if any(x in domain for x in ("ncbi.nlm.nih.gov", "pubmed", "pubchem", "chembl")):
            return "scientific database"
        if any(x in domain for x in ("webbook.nist", "physics.nist", "nist.gov")):
            return "standards database"
        if "arxiv.org" in domain:
            return "preprint server"
        if "github.com" in domain:
            return "code repository"
        if "orcid.org" in domain:
            return "researcher profile"
        if any(x in domain for x in ("sciencedirect", "springer", "nature.com", "wiley", "tandfonline")):
            return "academic publisher"
        if domain.endswith(".gov"):
            return "government source"
        if domain.endswith(".edu"):
            return "academic institution"
        if domain.endswith(".org"):
            return "organization"
        return "web source"

    for match in _URL_RE.finditer(web_result_text):
        url = match.group(0).rstrip(".,;)")
        try:
            parsed = _urlparse.urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
        except Exception:
            continue
        if not domain or domain in _GENERIC_DOMAINS or domain in seen:
            continue
        seen.add(domain)
        result.append({"domain": domain, "context": _classify(domain)})
        if len(result) >= 6:
            break
    return result


# ─── Context Compression Layer ────────────────────────────────────────────────

# _GRAPH_COMPRESS_* constants imported from config.py

# Pattern to split graph context into per-entity blocks.
# Blocks start with "Entity:" or the special section headers the manager emits.
_ENTITY_BLOCK_RE = re.compile(r'(?=Entity:|(?:\n\s*\n))', re.MULTILINE)
_CONFIDENCE_RE   = re.compile(r'confidence[=:]\s*([0-9.]+)', re.IGNORECASE)


def _rerank_graph_context(ctx: str, budget: int) -> str:
    """Reorders graph context blocks by confidence score before truncation.

    Splits the raw graph string into entity blocks, sorts highest-confidence
    first, then reassembles within the character budget — preserving complete
    blocks rather than cutting mid-sentence.
    """
    if not ctx or budget <= 0:
        return ctx

    # Split into blocks; keep non-empty blocks only
    blocks = [b.strip() for b in _ENTITY_BLOCK_RE.split(ctx) if b.strip()]
    if len(blocks) <= 1:
        # No entity structure detected — fall back to simple truncation
        return ctx[:budget]

    def _block_confidence(block: str) -> float:
        m = _CONFIDENCE_RE.search(block[:200])
        return float(m.group(1)) if m else 0.5

    blocks.sort(key=_block_confidence, reverse=True)

    # Reassemble within budget, preserving complete blocks
    result_parts: list[str] = []
    remaining = budget
    for block in blocks:
        if len(block) + 1 <= remaining:
            result_parts.append(block)
            remaining -= len(block) + 1
        else:
            break

    if not result_parts:
        # Budget too tight even for one block — truncate the highest-confidence block
        return blocks[0][:budget]

    truncated = len(blocks) - len(result_parts)
    result = "\n".join(result_parts)
    if truncated > 0:
        result += f"\n[...{truncated} lower-confidence entity block(s) omitted]"
    return result


async def _compress_graph_context_llm(ctx: str, budget: int) -> Optional[str]:
    """Summarises graph context to fit within budget using a small local LLM.

    Returns compressed string on success, None on timeout/error (caller falls
    back to reranking-based truncation).  Timeout is hard-capped to prevent
    merger latency impact.
    """
    from langchain_openai import ChatOpenAI
    model = _GRAPH_COMPRESS_LLM_MODEL
    if not model or _m.judge_llm is None:
        return None
    try:
        compress_prompt = (
            f"Summarise the following Knowledge Graph context in at most {budget} characters. "
            "Preserve entity names, types, relationships, and confidence values. "
            "Prioritise high-confidence facts. Output plain text only — no JSON, no headers.\n\n"
            f"{ctx[:6000]}"
        )
        _compress_llm = ChatOpenAI(
            model=model,
            base_url=_m.judge_llm.openai_api_base,
            api_key=_m.judge_llm.openai_api_key,
            timeout=_GRAPH_COMPRESS_LLM_TIMEOUT,
        )
        result = await asyncio.wait_for(
            _compress_llm.ainvoke(compress_prompt),
            timeout=_GRAPH_COMPRESS_LLM_TIMEOUT + 0.5,
        )
        compressed = result.content.strip()
        if compressed and len(compressed) <= budget * 1.1:
            return compressed
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug(f"Graph LLM compression skipped: {e}")
    return None


async def merger_node(state_: AgentState):
    from datetime import datetime
    from parsing import _extract_usage, _parse_expert_confidence, _expert_category, _dedup_by_category, _collect_conflicts, _improvement_ratio
    from metrics import PROM_TOKENS
    from config import KAFKA_TOPIC_REQUESTS

    # Cache hit: direct answer, no LLM call needed
    if state_.get("cache_hit"):
        logger.info("--- [NODE] MERGER (cache hit, direct return) ---")
        await _m._report("💨 Merger: cached response delivered directly")
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id": state_.get("response_id", ""),
            "input":       state_["input"][:300],
            "cache_hit":   True,
            "ts":          datetime.now().isoformat(),
        }))
        return {"final_response": state_.get("cached_facts", "")}

    logger.info("--- [NODE] MERGER & INGEST ---")
    await _m._report("🔀 Merger analyzing expert confidence...")

    _all_expert_raw = state_.get("expert_results") or []
    _ensemble_raw   = [r for r in _all_expert_raw if re.match(r'\[ENSEMBLE:', r)]
    _normal_raw     = [r for r in _all_expert_raw if not re.match(r'\[ENSEMBLE:', r)]
    expert_results  = _dedup_by_category(_normal_raw)
    ensemble_results = _ensemble_raw   # all ensemble results unfiltered to merger

    # Paraconsistent conflict detection: collect divergent expert outputs before
    # dedup discards them. Both propositions are preserved in conflict_registry
    # rather than silently overwritten — de Vries (2007), arXiv:0707.2161, §2.
    _new_conflicts = _collect_conflicts(_normal_raw)
    if _new_conflicts:
        _cats = sorted({c["category"] for c in _new_conflicts})
        logger.info(f"⚖️  Conflict registry: {len(_new_conflicts)} paraconsistent conflicts in {_cats}")
        await _m._report(f"⚖️ Paraconsistent conflicts detected: {', '.join(_cats)}")

    web            = state_.get("web_research")    or ""
    cached         = state_.get("cached_facts")    or ""
    math_res       = state_.get("math_result")     or ""
    mcp_res        = state_.get("mcp_result")      or ""
    graph_ctx      = state_.get("graph_context")   or ""
    reasoning      = state_.get("reasoning_trace") or ""

    _SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}

    # ── 3B: Confidence analysis (normal + ensemble results) ────────────────
    low_conf_critical = [
        r for r in (expert_results + ensemble_results)
        if _parse_expert_confidence(r) == "low"
        and _expert_category(r) in _SAFETY_CRITICAL_CATS
    ]
    if low_conf_critical:
        cats = sorted({_expert_category(r) for r in low_conf_critical})
        logger.info(f"⚠️ Low confidence in: {cats}")
        await _m._report(f"⚠️ Low confidence: {', '.join(cats)}")

    # ── Judge Refinement Loop: improve low-confidence expert responses ────────
    if JUDGE_REFINE_MAX_ROUNDS > 0 and expert_results:
        for _refine_round in range(JUDGE_REFINE_MAX_ROUNDS):
            low_conf_list = [r for r in expert_results if _parse_expert_confidence(r) == "low"]
            if not low_conf_list:
                break
            await _m._report(f"🔄 Refinement round {_refine_round + 1}/{JUDGE_REFINE_MAX_ROUNDS}: "
                          f"{len(low_conf_list)} low-confidence experts")
            # Judge generates feedback — enriched with web/graph context
            _ctx_snippet = ""
            if web:
                _ctx_snippet += f"\nWEB CONTEXT (excerpt):\n{web[:1500]}"
            if graph_ctx:
                _ctx_snippet += f"\nGRAPH KNOWLEDGE (excerpt):\n{graph_ctx[:800]}"
            gap_prompt = (
                "Analyze these expert responses with CONFIDENCE: low and formulate "
                "concrete, specific improvement hints for each category (max. 3 sentences). "
                "Use available context to directly name missing facts:\n\n"
                + "\n\n".join(low_conf_list)
                + _ctx_snippet
                + "\n\nFormat: [CATEGORY]: <improvement hints with concrete facts>"
            )
            try:
                await _m._report(f"🔄 Judge refinement prompt (round {_refine_round + 1}):\n{gap_prompt}")
                _gap_res = await _invoke_judge_with_retry(state_, gap_prompt)
                gap_feedback_text = _gap_res.content.strip()
                # Persist refinement reason in state for causal-path logging
                state_["judge_reason"] = gap_feedback_text[:500]
                state_["judge_refined"] = True
                await _m._report(f"🔄 Judge refinement response (round {_refine_round + 1}):\n{gap_feedback_text}")
            except Exception as _ge:
                logger.warning(f"⚠️ Refinement judge feedback round {_refine_round + 1}: {_ge}")
                break
            # Per low-confidence category: re-invoke the best expert
            any_improvement = False
            new_expert_results = list(expert_results)
            for old_result in low_conf_list:
                _cat = _expert_category(old_result)
                # Extract category-specific feedback
                cat_feedback = gap_feedback_text
                for _line in gap_feedback_text.splitlines():
                    if _line.strip().startswith(f"[{_cat}]"):
                        cat_feedback = _line.split(":", 1)[-1].strip()
                        break
                refined = await _refine_expert_response(_cat, cat_feedback, state_)
                if not refined:
                    continue
                new_conf  = _parse_expert_confidence(refined)
                old_conf  = _parse_expert_confidence(old_result)
                ratio     = _improvement_ratio(old_result, refined)
                logger.info(f"🔄 Refinement [{_cat}]: {old_conf} → {new_conf}, Δ={ratio:.2f}")
                await _m._report(f"🔄 [{_cat}]: {old_conf} → {new_conf} (Δ{ratio:.0%})")
                if ratio >= JUDGE_REFINE_MIN_IMPROVEMENT:
                    _prefix = old_result.split("]:", 1)[0].lstrip("[")
                    new_expert_results = [
                        f"[{_prefix}]: {refined}" if r is old_result else r
                        for r in new_expert_results
                    ]
                    any_improvement = True
                    PROM_JUDGE_REFINED.labels(outcome="improved").inc()
                    if CORRECTION_MEMORY_ENABLED and state.graph_manager is not None:
                        from graph_rag.corrections import store_correction as _store_correction
                        PROM_CORRECTIONS_STORED.labels(source="judge_refinement").inc()
                        asyncio.create_task(_store_correction(
                            state.graph_manager.driver if hasattr(state.graph_manager, 'driver') else None,
                            prompt=state_.get("input", "")[:500],
                            wrong=old_result[:500],
                            correct=refined[:500],
                            category=_cat,
                            source_model=state_.get("judge_model_override") or "",
                            correction_source="judge_refinement",
                            tenant_id=",".join(state_.get("tenant_ids", [])),
                        ))
            expert_results = new_expert_results
            if not any_improvement:
                await _m._report(f"⏹️ Refinement stopped: no significant improvement "
                              f"(< {JUDGE_REFINE_MIN_IMPROVEMENT:.0%})")
                break

    await _m._report("🔀 Merger synthesizing final response...")

    # ── 3B: Confidence-aware merger instruction ────────────────────────────
    mode     = state_.get("mode", "default")
    mode_cfg = MODES.get(mode, MODES["default"])

    # Only include non-empty sections in the prompt
    sections: List[str] = [f"REQUEST: {state_['input']}"]
    if reasoning:
        sections.append(f"REASONING ANALYSIS:\n{reasoning}")
    if graph_ctx:
        _gctx = graph_ctx
        # Compute per-template GraphRAG char budget: explicit override > auto from
        # judge model context window > global MAX_GRAPH_CONTEXT_CHARS.
        _judge_model = state_.get("judge_model_override") or JUDGE_MODEL
        _tpl_limit = state_.get("graphrag_max_chars", 0)
        # Use async context-window lookup (Redis-cache → Ollama → static table) for judge model
        from context_budget import get_model_ctx_async as _ctx_async_grag, graphrag_budget_chars
        _judge_url = (state_.get("judge_url_override") or "").rstrip("/")
        _judge_tok = (state_.get("judge_token_override") or "ollama")
        _judge_ctx = await _ctx_async_grag(model=_judge_model, base_url=_judge_url,
                                           token=_judge_tok, redis_client=state.redis_client)
        # Override static-table result with live value when available
        _effective_limit = graphrag_budget_chars(
            model=_judge_model,
            query_chars=len(state_.get("input", "")),
            override_chars=_tpl_limit,
        )
        # If async lookup found a larger (or known) context window, recompute
        if _judge_ctx > 0 and _tpl_limit <= 0:
            from context_budget import (MERGER_FIXED_TOKENS, MERGER_HEADROOM_TOKENS,
                                        CHARS_PER_TOKEN, MIN_GRAPHRAG_CHARS)
            _q_tok = (len(state_.get("input", "")) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
            _avail = _judge_ctx - MERGER_FIXED_TOKENS - MERGER_HEADROOM_TOKENS - _q_tok
            _effective_limit = max(MIN_GRAPHRAG_CHARS, _avail * CHARS_PER_TOKEN)
        # Final safety net: never exceed the global hard cap if set.
        if MAX_GRAPH_CONTEXT_CHARS > 0:
            _effective_limit = min(_effective_limit, MAX_GRAPH_CONTEXT_CHARS)
        _graph_raw_chars = len(_gctx)
        _compression_method = "none"
        if _effective_limit > 0 and _graph_raw_chars > _effective_limit:
            threshold = _effective_limit * _GRAPH_COMPRESS_THRESHOLD_FACTOR
            if _graph_raw_chars > threshold and _GRAPH_COMPRESS_LLM_MODEL:
                # Very large context: attempt LLM-based semantic compression first
                _compressed = await _compress_graph_context_llm(_gctx, _effective_limit)
                if _compressed:
                    _gctx = _compressed
                    _compression_method = "llm"
                else:
                    _gctx = _rerank_graph_context(_gctx, _effective_limit)
                    _compression_method = "rerank"
            else:
                # Moderate overrun: reorder by confidence, preserve complete blocks
                _gctx = _rerank_graph_context(_gctx, _effective_limit)
                _compression_method = "rerank"
        logger.info(
            f"📊 GraphRAG compression: {_graph_raw_chars} → {len(_gctx)} chars "
            f"(method={_compression_method}, budget={_effective_limit})"
        )
        # Store compression telemetry in state for causal-path logging
        state_["graphrag_entities"] = state_.get("graphrag_entities") or []
        sections.append(f"STRUCTURED KNOWLEDGE (Ontology/Knowledge Graph):\n{_gctx}")
    if expert_results:
        # Dynamic per-expert truncation: budget scales with expert count so
        # multi-expert synthesis retains enough of each response to synthesise.
        # With 1 expert: 3500 chars. With 2: 2800 each. With 4+: 2000 each.
        # Floor is 2000 to keep merger token budget bounded.
        _n_experts    = len(expert_results)
        MAX_EXPERT_CHARS = max(2000, min(3500, 3500 - (_n_experts - 1) * 500))
        trimmed = []
        for er in expert_results:
            if len(er) > MAX_EXPERT_CHARS:
                trimmed.append(er[:MAX_EXPERT_CHARS] + "\n[...truncated for merger efficiency]")
            else:
                trimmed.append(er)
        # For multi-expert synthesis: prepend a domain index so the judge
        # immediately sees which domains are represented and can plan coverage.
        if _n_experts > 1:
            domain_index = ", ".join(
                f"[{_expert_category(er) or 'general'}]" for er in expert_results
            )
            expert_header = (
                f"EXPERT RESPONSES ({_n_experts} domains: {domain_index}) — "
                "integrate ALL domains into the synthesis:\n"
            )
        else:
            expert_header = "EXPERT RESPONSES:\n"
        sections.append(expert_header + "\n\n".join(trimmed))
    if ensemble_results:
        sections.append(
            "ENSEMBLE ANALYSIS (multiple models from different providers, run in parallel with identical prompt — "
            "treat all perspectives equally, highlight commonalities, "
            "explicitly name and classify contradictions):\n" + "\n\n".join(ensemble_results)
        )
    if mcp_res:
        sections.append(f"PRECISION CALCULATIONS (MCP — exact, authoritative):\n{mcp_res}")
    if math_res:
        sections.append(f"MATH (SymPy):\n{math_res}")
    if web:
        # Adaptive web-research compression: block/char limits scale with the
        # judge model's context window so small fallback models (gemma4:31b 8K)
        # get tighter limits than large models (gpt-120B 128K).
        from context_budget import web_research_budget
        _judge_model_for_web = state_.get("judge_model_override") or JUDGE_MODEL
        MAX_WEB_BLOCKS, MAX_BLOCK_CHARS = web_research_budget(
            model=_judge_model_for_web,
            query_chars=len(state_.get("input", "")),
            graphrag_chars_used=len(_gctx) if "_gctx" in dir() else 0,
        )
        web_blocks = [b.strip() for b in re.split(r'\n\[(?:Research|Recherche|\d+)', web) if b.strip()]
        compressed_web = "\n\n".join(
            block[:MAX_BLOCK_CHARS] + ("…" if len(block) > MAX_BLOCK_CHARS else "")
            for block in web_blocks[:MAX_WEB_BLOCKS]
        )
        if not compressed_web:
            compressed_web = web[:MAX_BLOCK_CHARS * 2]  # fallback if split produced nothing
        sections.append(f"WEB RESEARCH (current, with sources):\n{compressed_web}")
    # Code-duplication guard: LLMs (especially gpt-4.1) interpret "integrate the strongest
    # insights" literally when they receive two similar code implementations — they interleave
    # both character-by-character, producing doubled output. Guard fires when any primary source
    # (expert_results OR ensemble_results) contains code AND a secondary source does too.
    _CODE_MARKERS = ("```", "<!DOCTYPE", "<html", "def ", "function ", "class ", "import ", "setInterval")
    _primary_sources = list(expert_results) + list(ensemble_results)
    _primary_has_code = any(any(m in s for m in _CODE_MARKERS) for s in _primary_sources)

    if cached:
        _cached_has_code = any(m in cached for m in _CODE_MARKERS)
        if _primary_has_code and _cached_has_code:
            logger.info("🛡️ PRIOR KNOWLEDGE suppressed: primary source + cache both contain code (prevents judge interleaving)")
            await _m._report("🛡️ Prior knowledge suppressed (code duplication guard)")
        else:
            sections.append(f"PRIOR KNOWLEDGE (Cache):\n{cached[:1000]}")
    soft_examples = state_.get("soft_cache_examples") or ""
    if soft_examples:
        _soft_has_code = any(m in soft_examples for m in _CODE_MARKERS)
        if _primary_has_code and _soft_has_code:
            logger.info("🛡️ Soft-cache suppressed: primary source + cached snippet both contain code (prevents judge interleaving)")
            await _m._report("🛡️ Soft-cache examples suppressed (code duplication guard)")
        else:
            sections.append(f"SIMILAR PREVIOUS ANSWERS (few-shot orientation, do not use as fact):\n{soft_examples}")

    conf_note = ""
    if low_conf_critical and mode != "code":  # Code mode does not need caveats
        cats_str = ", ".join(sorted({_expert_category(r) for r in low_conf_critical}))
        conf_note = (
            f"\nWARNING: Expert categories [{cats_str}] reported CONFIDENCE: low. "
            "Explicitly point out this uncertainty in the response. "
            "Recommend professional advice (doctor/lawyer). "
            "Prioritize web research data over low-confidence expert statements."
        )

    _custom_judge = (state_.get("judge_prompt") or "").strip()
    merger_prefix = _custom_judge if _custom_judge else mode_cfg["merger_prefix"]
    _has_graph_ctx = bool(graph_ctx and graph_ctx.strip())
    prompt = (
        merger_prefix
        + conf_note + "\n\n"
        + "\n\n---\n\n".join(sections)
        + _m.SYNTHESIS_PERSISTENCE_INSTRUCTION
        + (_m.PROVENANCE_INSTRUCTION if _has_graph_ctx else "")
    )

    # Inject output skill formatting instructions if planner suggested one.
    # Guard: suppress skill body when BOTH primary expert output AND the skill
    # template contain code markers — the judge LLM otherwise interleaves the
    # expert's code with identical patterns from the skill template, producing
    # visible duplication (e.g. repeated bash find commands). The skill body is
    # formatting guidance only; if the expert already produced code the format
    # hint is redundant and harmful.
    _skill_body = state_.get("output_skill_body", "")
    if _skill_body:
        _skill_has_code = any(m in _skill_body for m in _CODE_MARKERS)
        if _primary_has_code and _skill_has_code:
            logger.info("🛡️ Skill body suppressed: expert output + skill template both contain code (prevents judge interleaving)")
        else:
            prompt += (
                "\n\n--- OUTPUT FORMATTING SKILL ---\n"
                "The planner selected a specific output format for this response. "
                "Follow these formatting instructions:\n\n"
                + _skill_body[:3000]
            )

    # Determine expert domain early — used for both ChromaDB metadata and Kafka ingest payload
    _plan_cats_early = [t.get("category", "") for t in state_.get("plan", []) if isinstance(t, dict)]
    _expert_domain = next(
        (c for c in ("medical_consult", "legal_advisor", "technical_support") if c in _plan_cats_early),
        _plan_cats_early[0] if _plan_cats_early else "general",
    )

    # ── Fast path: single high-confidence expert, no additional context ─────────
    _single_expert_modes = ("default", "concise")
    if (len(expert_results) == 1
            and not ensemble_results
            and not web and not mcp_res and not math_res and not graph_ctx
            and _parse_expert_confidence(expert_results[0]) == "high"
            and mode in _single_expert_modes):
        _raw_fp = re.sub(r'^\[[^\]]+\]:\s*', '', expert_results[0])
        _details_m = re.search(r'DETAILS:\n?(.*)', _raw_fp, re.DOTALL)
        fast_resp = _details_m.group(1).strip() if _details_m else _raw_fp.strip()
        logger.info(f"⚡ Fast-Path: single high-confidence expert → direct response ({len(fast_resp)} chars)")
        await _m._report(f"⚡ Fast-Path: single high-confidence expert ({len(fast_resp)} chars)")
        if len(fast_resp) > CACHE_MIN_RESPONSE_LEN and not state_.get("no_cache", False):
            # Deterministic ID (SHA-256 of content) prevents duplicate entries under
            # concurrent writes — upsert is idempotent if same response races twice.
            # Skipped when no_cache=True to avoid polluting the vector store.
            _fp_cid = hashlib.sha256(fast_resp.encode()).hexdigest()[:32]
            await asyncio.to_thread(
                state.cache_collection.upsert,
                ids=[_fp_cid],
                documents=[fast_resp],
                metadatas=[{"ts": datetime.now().isoformat(), "input": state_["input"][:200], "flagged": False, "expert_domain": _expert_domain}],
            )
            # L0: Write to query-hash cache for instant hits on identical queries
            if not state_.get("no_cache") and state.redis_client:
                try:
                    import hashlib as _hl
                    _q_norm = re.sub(r'\s+', ' ', state_["input"].lower().strip().rstrip('?!.,;'))
                    _q_hash = _hl.sha256(_q_norm.encode()).hexdigest()[:24]
                    asyncio.create_task(state.redis_client.setex(f"moe:qcache:{_q_hash}", 1800, fast_resp))
                except Exception:
                    pass
            asyncio.create_task(_m._store_response_metadata(
                state_.get("response_id", ""), state_["input"],
                state_.get("expert_models_used", []), _fp_cid,
                plan=state_.get("plan", []), cost_tier=state_.get("cost_tier", "")))
            asyncio.create_task(_m._self_evaluate(
                state_.get("response_id", ""), state_["input"], fast_resp, _fp_cid,
                template_name=state_.get("template_name", ""),
                complexity=state_.get("complexity_level", ""),
            ))
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id": state_.get("response_id", ""),
            "input":       state_["input"][:300],
            "fast_path":   True,
            "ts":          datetime.now().isoformat(),
        }))
        return {"final_response": fast_resp, "prompt_tokens": 0, "completion_tokens": 0}

    await _m._report(f"🔀 Merger prompt ({len(prompt)} chars):\n{prompt}")
    try:
        res = await _invoke_judge_with_retry(state_, prompt, temperature=state_.get("query_temperature"))
    except Exception as e:
        logger.error(f"❌ Merger Judge LLM error: {e}")
        await _m._report(f"❌ Merger: Judge LLM unreachable ({e})")
        fallback = "\n\n".join(s for s in sections[1:] if s)  # raw sections as emergency response
        return {"final_response": fallback or "Error: Merger could not generate a response."}
    await _m._report(f"🔀 Merger response ({len(res.content)} chars):\n{res.content}")
    merger_usage = _extract_usage(res)
    _uid = state_.get("user_id", "anon")
    PROM_TOKENS.labels(model=JUDGE_MODEL, token_type="prompt",      node="merger", user_id=_uid).inc(merger_usage.get("prompt_tokens", 0))
    PROM_TOKENS.labels(model=JUDGE_MODEL, token_type="completion",  node="merger", user_id=_uid).inc(merger_usage.get("completion_tokens", 0))
    _judge_failed = (not res.content.strip() or
                     res.content.startswith("[Judge unavailable"))
    if _judge_failed:
        logger.error("❌ Merger: Judge LLM returned empty/error response (VRAM/OOM?)")
        await _m._report("❌ Merger: empty or failed answer from judge — possible VRAM exhaustion")
        # Best expert response as fallback
        best = next((r for r in expert_results if _parse_expert_confidence(r) == "high"), None) \
               or (expert_results[0] if expert_results else None)
        fallback = best or "No answer available — please try again."
        return {"final_response": fallback, **merger_usage}
    await _m._report(f"✅ Response complete ({len(res.content)} chars)")

    # Parse and strip any SYNTHESIS_INSIGHT block from the LLM output.
    # The clean content is shown to the user; the insight is persisted to Neo4j separately.
    _SYNTH_RE = re.compile(r"<SYNTHESIS_INSIGHT>(.*?)</SYNTHESIS_INSIGHT>", re.DOTALL)
    _synth_match = _SYNTH_RE.search(res.content)
    _synthesis_payload = None
    if _synth_match:
        try:
            _synthesis_payload = json.loads(_synth_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass
        res_content_clean = _SYNTH_RE.sub("", res.content).rstrip()
    else:
        res_content_clean = res.content

    # ── Provenance tag extraction ──────────────────────────────────────────
    _REF_RE = re.compile(r'\[REF:([^\]]+)\]')
    _ref_matches = _REF_RE.findall(res_content_clean)
    _provenance_sources = []
    if _ref_matches:
        for ref_name in dict.fromkeys(_ref_matches):  # deduplicate, preserve order
            _provenance_sources.append({"type": "neo4j", "label": ref_name.strip()})
        # Strip REF tags from content for clean output
        res_content_clean = _REF_RE.sub('', res_content_clean).strip()

    # Strip internal merger format headers that should not reach the user
    _INTERNAL_HEADERS_RE = re.compile(
        r'^(Key findings from each expert role:|Expert consensus:|## Expert Analysis|'
        r'\[EXPERT_[A-Z_]+\]|=== EXPERT ===).*?(?=\n\n|\Z)',
        re.MULTILINE | re.DOTALL
    )
    res_content_clean = _INTERNAL_HEADERS_RE.sub('', res_content_clean).strip()

    # Strip confidence annotations that leak from expert/judge nodes into the response.
    # Covers: **LOW CONFIDENCE (30%)**, CONFIDENCE: low, Set CONFIDENCE: high, etc.
    _CONFIDENCE_TAG_RE = re.compile(
        r'(?:'
        r'\*{1,2}(?:low|medium|high)\s+confidence\s*(?:\(\s*\d+\s*%\s*\))?\*{1,2}'
        r'|(?:set\s+)?confidence\s*:\s*(?:low|medium|high|very\s+high|very\s+low)'
        r')',
        re.IGNORECASE,
    )
    res_content_clean = _CONFIDENCE_TAG_RE.sub('', res_content_clean).strip()

    # Strip all citation/reference brackets leaked from tool results:
    # covers various bracket styles used by different LLM tools.
    res_content_clean = re.sub(r'【[^】]*】', '', res_content_clean).strip()

    # Strip leading markdown bold label if the answer starts with "**Label:** value".
    # Models like qwen3 emit structured output ("**Identified Compound:** Benzene") which
    # should be reduced to just the value ("Benzene").
    _md_label = re.match(r'^\*{1,2}[^*\n]{1,40}\*{1,2}\s*[:\-]\s*(.+)', res_content_clean, re.DOTALL)
    if _md_label:
        res_content_clean = _md_label.group(1).strip()

    # Post-strip fallback: if cleaning stripped everything, use best expert result.
    # Explicitly skip expert results that are capability disclaimers (expert-leak patterns)
    # — using a leak answer as fallback is worse than returning empty.
    _LEAK_FALLBACK_RE = re.compile(
        r'\b(i (cannot|can\'t|won\'t) (access|browse|fetch)|'
        r'we need(s)? to (browse|search|fetch)|'
        r'no (direct )?access to (the )?(internet|web)|'
        r'attempt\s+(web\s+)?search|'
        r'unable to (browse|access|fetch))\b',
        re.I,
    )
    if not res_content_clean:
        _expert_results = state_.get("expert_results") or []
        _non_leak = [r for r in _expert_results if r and not _LEAK_FALLBACK_RE.search(r)]
        _best_expert = (
            next((r for r in _non_leak if _parse_expert_confidence(r) == "high"), None)
            or (_non_leak[0] if _non_leak else None)
        )
        if _best_expert:
            res_content_clean = _best_expert.strip()
            logger.warning("⚠️ Merger output empty after strip — using best non-leak expert result as fallback")

    _no_cache_write = state_.get("no_cache", False)
    if len(res_content_clean) > CACHE_MIN_RESPONSE_LEN and not _no_cache_write:
        # Deterministic ID (SHA-256 of content) prevents duplicate entries under
        # concurrent writes — upsert is idempotent if same response races twice.
        # Skipped when no_cache=True: unique benchmark/research queries would only
        # pollute the vector store with entries that are never read again.
        chroma_doc_id = hashlib.sha256(res_content_clean.encode()).hexdigest()[:32]
        await asyncio.to_thread(
            state.cache_collection.upsert,
            ids=[chroma_doc_id],
            documents=[res_content_clean],
            metadatas=[{"ts": datetime.now().isoformat(), "input": state_["input"][:200], "flagged": False, "expert_domain": _expert_domain}],
        )
        # L0: Write to query-hash cache (30 min TTL)
        if not state_.get("no_cache") and state.redis_client:
            try:
                import hashlib as _hl
                _q_norm = re.sub(r'\s+', ' ', state_["input"].lower().strip().rstrip('?!.,;'))
                _q_hash = _hl.sha256(_q_norm.encode()).hexdigest()[:24]
                asyncio.create_task(state.redis_client.setex(f"moe:qcache:{_q_hash}", 1800, res_content_clean))
            except Exception:
                pass
        # Save response metadata for feedback tracking in Valkey (non-blocking)
        asyncio.create_task(
            _m._store_response_metadata(
                state_.get("response_id", ""),
                state_["input"],
                state_.get("expert_models_used", []),
                chroma_doc_id,
                plan=state_.get("plan", []),
                cost_tier=state_.get("cost_tier", ""),
            )
        )
        # Self-evaluation via judge LLM (async, fire-and-forget — no latency overhead)
        asyncio.create_task(_m._self_evaluate(
            state_.get("response_id", ""), state_["input"], res_content_clean, chroma_doc_id,
            template_name=state_.get("template_name", ""),
            complexity=state_.get("complexity_level", ""),
        ))
        # Routing telemetry → PostgreSQL (async, fire-and-forget)
        import telemetry as _telemetry
        asyncio.create_task(_telemetry.record_routing_decision(
            state._userdb_pool, state_.get("response_id", ""), state_,
            wall_clock_ms=int((time.time() - state_.get("_start_time", time.time())) * 1000),
        ))
        # Request-Audit-Log → Kafka moe.requests
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id":        state_.get("response_id", ""),
            "input":              state_["input"][:300],
            "answer":             res_content_clean[:500],
            "expert_models_used": state_.get("expert_models_used", []),
            "cache_hit":          False,
            "ts":                 datetime.now().isoformat(),
        }))
        # GraphRAG Ingest → Kafka moe.ingest (consumer processes asynchronously)
        # Reuse the domain already computed above for ChromaDB metadata
        ingest_domain = _expert_domain
        # Dominant model from expert_models_used as provenance source
        _used_models = state_.get("expert_models_used", [])
        _ingest_model = _used_models[0] if _used_models else JUDGE_MODEL
        # Derive confidence from expert results (high=0.9, medium=0.6, low=0.3)
        _conf_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
        _expert_confs = [
            _conf_map.get(_parse_expert_confidence(r), 0.5)
            for r in state_.get("expert_results", [])
        ]
        _ingest_confidence = (sum(_expert_confs) / len(_expert_confs)) if _expert_confs else 0.5
        # Classify knowledge type: procedural if answer implies action→location requirements.
        _proc_markers = {
            "requires", "must", "necessary", "prerequisite", "needed",
            "location", "on-site", "on premises", "physically", "necessitates",
        }
        _knowledge_type = (
            "procedural"
            if any(kw in res_content_clean for kw in _proc_markers)
            else "factual"
        )
        _tenant_ids = state_.get("tenant_ids", [])
        # Use personal namespace as ingest target so knowledge starts private.
        # The first element is always user:{id} when a real user is logged in.
        _ingest_tenant_id = _tenant_ids[0] if _tenant_ids else None
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_INGEST, {
            "response_id":      state_.get("response_id", ""),
            "input":            state_["input"],
            "answer":           res_content_clean,
            "domain":           ingest_domain,
            "source_expert":    ingest_domain,   # expert category for domain-isolated memory
            "source_model":     _ingest_model,
            "template_name":    state_.get("template_name", ""),
            "confidence":       round(_ingest_confidence, 2),
            "knowledge_type":   _knowledge_type,
            "synthesis_insight": _synthesis_payload,  # None if no synthesis was generated
            "tenant_id":        _ingest_tenant_id,
        }))

        # Self-Correction Loop (OBJ 3): Numerical discrepancies → few-shot examples
        from self_correction import process_merger_output as _sc_process
        asyncio.create_task(_sc_process(
            query=state_["input"],
            expert_results=state_.get("expert_results") or [],
            final_response=res_content_clean,
            plan=state_.get("plan") or [],
            redis_client=state.redis_client,
        ))

    # ── Agentic gap detection: assess if another iteration is needed ─────────
    _agentic_max  = state_.get("agentic_max_rounds") or 0
    _agentic_iter = state_.get("agentic_iteration") or 0
    _agentic_gap  = ""
    _agentic_history = list(state_.get("agentic_history") or [])
    _agentic_extra: dict = {}
    _strategy_hint = ""

    if _agentic_max > 0 and _agentic_iter < _agentic_max:
        # Early exit: if a file was generated (SKILL_TRIGGER / download link), the answer is complete.
        # Re-planning would cause skill_detector to run again and overwrite the generated file.
        _is_skill_response = (
            "SKILL_TRIGGER" in res_content_clean
            or "/downloads/" in res_content_clean
            or "DOWNLOAD_URL" in res_content_clean
        )
        if _is_skill_response:
            # File already generated — no re-plan needed; skill_detector must not run again.
            logger.info("⚡ Agentic gap skipped: skill response detected (file already generated)")
            _agentic_gap = "COMPLETE"
        else:
            # Token-budget guard: skip gap detection if already close to limit
            _used_tokens = state_.get("prompt_tokens", 0) + merger_usage.get("prompt_tokens", 0)

            # Expert-leak detection FIRST: capability disclaimers must override confidence gate.
            # "We need to browse." is <=15 words and would pass the confidence gate falsely.
            _EXPERT_LEAK_RE = re.compile(
                r"\b(i (cannot|can't|won'?t) (access|browse|fetch|retrieve|visit|search)|"
                r"i don'?t have (web|internet|direct|real.?time)|"
                r"(we|let'?s|i'?ll|we'?ll) (will |)(browse|search|look up|fetch|navigate|check)|"
                r"(we|it) need(s)? to (browse|search|fetch|access|look up|retrieve)|"
                r"attempt\s+(web\s+)?search|"
                r"attempt\s+tool\s+(call|use)|"
                r"attempt\s+to\s+(search|browse|fetch|find|look|call)|"
                r"will\s+attempt\s+to\s+(search|browse|fetch|find)|"
                r"no (direct )?access to (the )?(internet|web|url|website|page)|"
                r"unable to (browse|access|fetch|visit|open)|"
                r"as an ai.{0,30}(cannot|can'?t)|"
                r"i('m| am) not able to (access|browse|fetch))\b",
                re.I,
            )
            _expert_results_combined = " ".join(state_.get("expert_results") or [])
            _expert_is_leak = bool(_EXPERT_LEAK_RE.search(_expert_results_combined))
            if _expert_is_leak:
                logger.info("🔍 Expert-leak detected — forcing NEEDS_MORE_INFO (skipping confidence gate)")
                _agentic_gap = (
                    "One or more experts responded with a capability disclaimer instead of "
                    "attempting to research. Use web_researcher or fetch_pdf_text to get the data directly."
                )
                _strategy_hint = "use web_researcher with a targeted search query for the missing data"

            # Confidence gate: if the answer is short, precise and all experts reported high
            # confidence, skip re-planning — the answer is almost certainly correct and further
            # searching may overwrite it with a wrong result (e.g. "backtick" → "dot").
            # Only applies when no expert-leak was detected.
            _all_high = all(
                _parse_expert_confidence(r) == "high"
                for r in (state_.get("expert_results") or []) if r
            )
            _answer_is_short = len(res_content_clean.split()) <= 5  # <=5 words: single-token answers like "backtick", "Fred", "42"
            # In research mode a short answer is NOT a reliability signal — complex research
            # questions that need web lookups should still be re-checked even when compact.
            _is_research_mode = (state_.get("mode") or "") == "research"
            _confidence_gate_passed = (
                not _expert_is_leak
                and _all_high
                and _answer_is_short
                and not _is_research_mode
            )
            if _confidence_gate_passed:
                logger.info("⚡ Agentic gap skipped: short high-confidence answer — no re-plan")
                _agentic_gap = "COMPLETE"

            # Skip gap detection when already resolved by confidence gate or expert-leak handler.
            # Running a judge LLM-call after the gate already decided COMPLETE wastes tokens
            # and risks overwriting the correct COMPLETE verdict with NEEDS_MORE_INFO.
            if not _confidence_gate_passed and _agentic_gap != "COMPLETE" and _used_tokens < 80_000:
                _gap_prompt = (
                    "You are a completion assessor. Based on the original question and the current answer, "
                    "determine if the answer is complete and what specific data is still missing.\n\n"
                    f"ORIGINAL QUESTION:\n{state_['input'][:600]}\n\n"
                    f"CURRENT ANSWER:\n{res_content_clean[:800]}\n\n"
                    "IMPORTANT: If the answer contains phrases like 'I cannot access', 'no web browsing', "
                    "'I don't have internet access' — this is INCOMPLETE regardless of other content.\n\n"
                    "Reply ONLY in this exact format (no extra text):\n"
                    "COMPLETION_STATUS: COMPLETE | NEEDS_MORE_INFO\n"
                    "GAP: <specific fact/calculation/document still missing, or 'none'>\n"
                    "SEARCH_STRATEGY: <concrete next search — prefer domain-specific: "
                    "'web_search_domain site:semanticscholar.org <paper title>', "
                    "'web_search_domain site:webbook.nist.gov <compound name>', "
                    "'web_search_domain site:<authoritative_domain> <query>', "
                    "'use youtube_transcript with discovered video URL', "
                    "'use semantic_scholar_search <author year topic>'>"
                )
                try:
                    _gap_res = await _invoke_judge_with_retry(state_, _gap_prompt)
                    _gap_text = (_gap_res.content or "").strip()
                    _gap_match = re.search(r'GAP:\s*(.+?)(?:\n|$)', _gap_text, re.IGNORECASE)
                    _status_match = re.search(r'COMPLETION_STATUS:\s*(\w+)', _gap_text, re.IGNORECASE)
                    _strategy_match = re.search(r'SEARCH_STRATEGY:\s*(.+?)(?:\n|$)', _gap_text, re.IGNORECASE)
                    _status = (_status_match.group(1) if _status_match else "COMPLETE").upper()
                    _agentic_gap = (_gap_match.group(1).strip() if _gap_match else "").strip()
                    _strategy_hint = (_strategy_match.group(1).strip() if _strategy_match else "").strip()
                    if _status == "COMPLETE" or not _agentic_gap or _agentic_gap.lower() in ("none", ""):
                        _agentic_gap = "COMPLETE"
                        _strategy_hint = ""
                    logger.info(f"🔍 Agentic gap check: status={_status}, gap={_agentic_gap[:80]}, strategy={_strategy_hint[:60]}")
                except Exception as _ge:
                    logger.warning(f"⚠️ Agentic gap detection failed: {_ge}")
                    _agentic_gap = "COMPLETE"
                    _strategy_hint = ""
            else:
                logger.info(f"⚠️ Agentic gap skipped: token budget {_used_tokens} > 80k")
                _agentic_gap = "COMPLETE"

        # Record only gap + strategy for the re-planner — not full findings text.
        # Full findings bloat the re-planner prompt (~1200 chars × rounds) without
        # adding information the planner can act on. Gap and strategy are sufficient.
        _agentic_history.append({
            "iteration": _agentic_iter,
            "gap":      _agentic_gap[:300],
            "strategy": _strategy_hint[:200],
        })

        # Working Memory: LLM-based fact extraction only when:
        # (a) gap is still open — facts will feed the next re-planning round
        # (b) there are more rounds available — extraction is useless on the last iteration
        _max_rounds = state_.get("max_agentic_rounds", 2)
        _wm_merged: dict = dict(state_.get("working_memory") or {})
        if (_agentic_gap != "COMPLETE"
                and _agentic_iter < _max_rounds - 1
                and state_.get("prompt_tokens", 0) < 90_000):
            from parsing import _extract_json
            _extract_prompt = (
                "Extract the key facts from the text below as a flat JSON object "
                "{\"key\": \"value\"}. Keys must be short snake_case. "
                "Values must be concrete facts only (no opinions, no explanations). "
                "Return ONLY valid JSON, no markdown, no extra text.\n\n"
                f"TEXT:\n{res_content_clean[:500]}"
            )
            try:
                _fact_res = await _invoke_judge_with_retry(state_, _extract_prompt, max_retries=1)
                _facts = _extract_json(_fact_res.content or "")
                if isinstance(_facts, dict):
                    _fact_ts = datetime.utcnow().isoformat() + "Z"
                    for k, v in _facts.items():
                        _wm_merged[f"merger:{_agentic_iter}:{k}"] = {
                            "value": str(v)[:300],
                            "source": "merger_node",
                            "confidence": 0.7,
                            "ts": _fact_ts,
                        }
                    logger.info(f"📝 Working Memory: {len(_facts)} facts extracted by merger (iter {_agentic_iter})")
            except Exception as _fe:
                logger.debug(f"Merger fact extraction failed: {_fe}")

        # Increment agentic_iteration here via state return — not via direct mutation
        # in the router function (_should_replan), which is an anti-pattern in LangGraph.
        _agentic_extra = {
            "agentic_gap":          _agentic_gap,
            "agentic_history":      _agentic_history,
            "working_memory":       _wm_merged,
            "search_strategy_hint": _strategy_hint,
            "agentic_iteration":    _agentic_iter + 1 if _agentic_gap != "COMPLETE" else _agentic_iter,
        }

    return {
        "final_response":    res_content_clean,
        "provenance_sources": _provenance_sources,
        "conflict_registry": _new_conflicts,
        **merger_usage,
        **_agentic_extra,
    }


async def research_fallback_node(state_: AgentState):
    """
    Runs after all parallel nodes — only when a gap-detector strategy_hint provides
    a *different* search query than already attempted. Avoids re-running identical
    SearXNG queries that the research_node already executed (which produced the
    low-confidence result in the first place).
    """
    if state_.get("cache_hit"):
        return {"web_research": state_.get("web_research", "")}

    from parsing import _parse_expert_confidence, _expert_category

    expert_results   = state_.get("expert_results") or []
    existing_web     = state_.get("web_research") or ""
    strategy_hint    = (state_.get("search_strategy_hint") or "").strip()
    attempted        = {q.get("query", "") for q in (state_.get("attempted_queries") or [])}

    # Only fire when we have a concrete new search strategy from the gap detector
    # that hasn't been tried yet. Using the same plan-task query again wastes a
    # SearXNG call — the first result already showed it couldn't answer the question.
    if not strategy_hint or strategy_hint in attempted:
        if any(_parse_expert_confidence(r) == "low" for r in expert_results):
            logger.info("⚡ Research fallback skipped — no new strategy hint from gap detector")
        return {"web_research": existing_web}

    # Build search list from gap-detector strategy hint (new, not-yet-tried query)
    low_conf_cats = {
        _expert_category(r)
        for r in expert_results
        if _parse_expert_confidence(r) == "low"
    }
    cat = next(iter(low_conf_cats), "general")
    searches = [{"query": strategy_hint, "category": cat}]

    logger.info(f"--- [NODE] RESEARCH FALLBACK (strategy: '{strategy_hint[:60]}') ---")
    await _m._report(f"🔍 Research fallback — new strategy: '{strategy_hint[:60]}'")

    async def _search_one(item: dict) -> str:
        query = item["query"][:180]
        cat_s = item["category"]
        await _m._report(f"🌐 Fallback search [{cat_s}]: '{query[:60]}'...")
        raw = await _m._web_search_with_citations(query, ddg_fallback=state_.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG))
        if raw:
            await _m._report(f"🌐 [{cat_s}]: {len(raw)} chars fallback result")
            logger.info(f"🌐 Research Fallback [{cat_s}]: {len(raw)} chars")
            return f"[Research Fallback / {cat_s}]:\n{raw[:2000]}"
        await _m._report(f"⚠️ Fallback search [{cat_s}] returned empty")
        return ""

    results = await asyncio.gather(*[_search_one(s) for s in searches])
    new_web  = "\n\n".join(r for r in results if r)
    combined = "\n\n".join(filter(None, [existing_web, new_web]))
    return {"web_research": combined}


async def thinking_node(state_: AgentState):
    """
    Simulates structured reasoning before synthesis.
    Activated for complex plans (>1 task) or when experts report low confidence.
    Magistral:24b generates explicit chain-of-thought that serves as context for the merger.
    """
    if state_.get("cache_hit"):
        return {"reasoning_trace": ""}
    # Agent mode: skip thinking node — coding agents need low latency, not CoT
    if state_.get("mode") == "agent":
        return {"reasoning_trace": ""}
    # Complexity routing: trivial/moderate requests skip thinking
    if state_.get("skip_thinking"):
        logger.info("⚡ Thinking node skipped (complexity routing)")
        return {"reasoning_trace": ""}

    from parsing import _extract_usage, _parse_expert_confidence, _expert_category

    mode     = state_.get("mode", "default")
    mode_cfg = MODES.get(mode, MODES["default"])
    force    = mode_cfg.get("force_think", False)

    plan           = state_.get("plan", [])
    expert_results = state_.get("expert_results") or []

    has_low_conf = any(_parse_expert_confidence(r) == "low" for r in expert_results)
    # Genuine complexity: sequential task chains (depends_on) or multi-domain expert divergence.
    # len(plan) > 1 is too broad — most research requests have >1 task but don't need CoT.
    has_sequential_chain = any(t.get("depends_on") for t in plan if isinstance(t, dict))
    has_multi_category   = len({t.get("category") for t in plan if isinstance(t, dict)}) > 2
    # Also activate for complex/research queries with multiple tasks — L3 GAIA questions
    # have only 1 category but multi-step reasoning benefits from CoT.
    has_multi_task = len([t for t in plan if isinstance(t, dict)]) > 2
    is_complex = has_sequential_chain or has_multi_category or has_multi_task

    if not (force or is_complex or has_low_conf):
        return {"reasoning_trace": ""}

    logger.info("--- [NODE] THINKING (Chain-of-Thought) ---")
    await _m._report("🧠 Reasoning: strukturierte Analyse des Problems...")

    sections = [f"QUESTION: {state_['input']}"]
    if expert_results:
        conf_summary = ", ".join(
            f"{_expert_category(r) or '?'}={_parse_expert_confidence(r)}"
            for r in expert_results
        )
        sections.append(f"EXPERT CONFIDENCE: {conf_summary}")
    if state_.get("web_research"):
        sections.append(f"WEB CONTEXT (excerpt):\n{state_['web_research'][:1000]}")
    if state_.get("graph_context"):
        sections.append(f"GRAPH CONTEXT (excerpt):\n{state_['graph_context'][:500]}")

    reasoning_prompt = (
        "You are an analytical reasoning assistant. Analyze the task in 4 steps:\n\n"
        "1. PROBLEM DECOMPOSITION: What are the core questions and sub-problems?\n"
        "2. SOURCE EVALUATION: Which information is reliable? Where are there contradictions?\n"
        "3. KNOWLEDGE GAPS: What remains uncertain or unclear?\n"
        "4. CONCLUSION: What is the most likely correct answer and why?\n\n"
        "Be precise and critical. Maximum 300 words.\n\n"
        + "\n\n".join(sections)
    )

    await _m._report(f"🧠 Reasoning-Prompt:\n{reasoning_prompt}")
    try:
        res   = await _invoke_judge_with_retry(state_, reasoning_prompt)
        usage = _extract_usage(res)
        trace = res.content.strip()
        await _m._report(f"🧠 Reasoning result ({len(trace)} chars):\n{trace}")
        logger.info(f"🧠 Reasoning Trace: {trace[:200]}")
        return {"reasoning_trace": trace, **usage}
    except Exception as e:
        logger.warning(f"Thinking node error: {e}")
        return {"reasoning_trace": ""}


def _should_replan(state_: AgentState) -> str:
    """Router: decides whether merger should loop back to planner or proceed to critic."""
    _max   = state_.get("agentic_max_rounds") or 0
    _iter  = state_.get("agentic_iteration") or 0
    if _max <= 0:
        return "critic"
    if _iter >= _max:
        return "critic"
    _gap = (state_.get("agentic_gap") or "").strip()
    if not _gap or _gap.upper() == "COMPLETE" or _gap.lower() in ("none", ""):
        return "critic"
    # agentic_iteration is incremented in merger_node via state return — no direct mutation here.
    logger.info(f"🔄 Agentic router: iteration {_iter}/{_max}, gap='{_gap[:60]}'")
    return "planner"


async def resolve_conflicts_node(state_: AgentState):
    """Evaluate the paraconsistent conflict registry and mark entries as resolved.

    Paraconsistent logic (de Vries 2007, arXiv:0707.2161, §2) tolerates
    contradictions — this node does not eliminate them but makes them explicit
    so downstream nodes (critic, agentic re-planner) can act on them.

    Resolution strategy is implemented by the user-facing TODO below.
    Until resolved, all entries remain 'pending' and are visible in the
    audit trail (conflict_registry in AgentState).
    """
    _SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}
    conflicts = state_.get("conflict_registry") or []
    pending   = [c for c in conflicts if c.get("resolution") == "pending"]
    if not pending:
        return {}

    logger.info(f"⚖️  resolve_conflicts_node: {len(pending)} pending conflicts")
    await _m._report(f"⚖️ Resolving {len(pending)} paraconsistent conflict(s)...")

    # Strategy A: auto-dismiss low-divergence conflicts (formulaic variation, not real contradiction).
    # Strategy B: escalate safety-critical conflicts to a judge LLM call.
    # Mathematical basis: de Vries (2007), arXiv:0707.2161, §2 — paraconsistent resolution.
    _DIVERGENCE_AUTO_DISMISS = 0.5
    resolved: list = [c for c in conflicts if c.get("resolution") != "pending"]

    for c in pending:
        score    = c.get("divergence_score", 0.0)
        category = c.get("category", "")

        # Strategy A — low divergence: formulaic variation, not a real contradiction
        if score < _DIVERGENCE_AUTO_DISMISS:
            resolved.append({**c, "resolution": "dismissed", "resolved_by": "auto_low_divergence"})
            logger.info(f"⚖️  [{category}] conflict dismissed (score={score:.2f} < {_DIVERGENCE_AUTO_DISMISS})")
            continue

        # Strategy B — safety-critical with significant divergence: ask judge to arbitrate
        if category in _SAFETY_CRITICAL_CATS:
            arbitration_prompt = (
                f"Two experts in '{category}' produced conflicting answers. "
                f"Evaluate both and determine which is more accurate, or synthesise if both are partially correct.\n\n"
                f"EXPERT A:\n{c['proposition_a']}\n\n"
                f"EXPERT B:\n{c['proposition_b']}\n\n"
                f"Respond with: VERDICT: <A|B|SYNTHESIS> — <one-sentence rationale>"
            )
            try:
                arb_res = await _invoke_judge_with_retry(state_, arbitration_prompt)
                verdict = arb_res.content.strip()[:300]
                resolved.append({**c, "resolution": "resolved", "resolved_by": f"judge_arbitration: {verdict}"})
                logger.info(f"⚖️  [{category}] conflict resolved by judge: {verdict[:80]}")
                await _m._report(f"⚖️ [{category}] Judge verdict: {verdict[:120]}")
            except Exception as _arb_err:
                logger.warning(f"⚖️  [{category}] judge arbitration failed: {_arb_err}")
                resolved.append({**c, "resolution": "dismissed", "resolved_by": "judge_unavailable"})
            continue

        # Non-safety-critical, high divergence: log and dismiss — no LLM cost warranted
        resolved.append({**c, "resolution": "dismissed", "resolved_by": "unresolved_non_critical"})
        logger.info(f"⚖️  [{category}] conflict dismissed (non-critical, score={score:.2f})")

    return {"conflict_registry": resolved}


async def critic_node(state_: AgentState):
    """
    Fact-check for safety-critical domains (medical_consult, legal_advisor).
    Checks the merger answer for factual errors and returns a corrected version if needed.
    """
    if state_.get("cache_hit"):
        return {"final_response": state_.get("final_response", "")}

    _SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}
    from parsing import _extract_usage

    plan      = state_.get("plan", [])
    plan_cats = {t.get("category", "") for t in plan if isinstance(t, dict)}
    active    = plan_cats & _SAFETY_CRITICAL_CATS
    if not active:
        return {"final_response": state_.get("final_response", "")}

    final_response = state_.get("final_response", "")
    if not final_response or len(final_response) < 100:
        return {"final_response": final_response}

    logger.info(f"--- [NODE] CRITIC (fact-check: {active}) ---")
    await _m._report(f"🔎 Critic: fact-check for {', '.join(sorted(active))}...")

    critic_prompt = (
        f"You are a critical reviewer for {', '.join(sorted(active))} answers.\n"
        "Check the following answer for factual errors, dangerous statements or misleading information.\n\n"
        f"REQUEST: {state_['input']}\n\n"
        f"ANSWER TO CHECK:\n{final_response}\n\n"
        "RESPOND IN ONE OF EXACTLY TWO WAYS — no other format is acceptable:\n\n"
        "1. If the answer is factually correct and safe:\n"
        "   Respond with exactly the single word: CONFIRMED\n\n"
        "2. If the answer contains factual errors or dangerous content:\n"
        "   Write the fully corrected answer DIRECTLY — as if you were answering the user's request yourself.\n"
        "   Do NOT begin with any preamble, error analysis, or meta-commentary such as "
        "'Factual errors were found' or 'The answer contains mistakes'.\n"
        "   Start immediately with the corrected content.\n"
        "   You may append a brief [Correction-Note: ...] at the very end only.\n"
    )

    await _m._report(f"🔎 Critic-Prompt:\n{critic_prompt}")
    try:
        res          = await _invoke_judge_with_retry(state_, critic_prompt)
        usage        = _extract_usage(res)
        critic_out   = res.content.strip()
        await _m._report(f"🔎 Critic response:\n{critic_out}")

        # Guard: if the judge refused (content filter / VRAM), keep the merger answer unchanged.
        if critic_out.startswith("[Judge unavailable") or not critic_out:
            logger.warning("⚠️ Critic: judge refused — preserving merger answer unchanged")
            await _m._report("⚠️ Critic: judge refused (content filter?) — merger answer preserved")
            return {"final_response": final_response}

        if critic_out.upper().startswith("CONFIRMED"):
            await _m._report("✅ Critic: answer confirmed correct")
            logger.info("✅ Critic: no errors found")
            return {"final_response": final_response, **usage}

        await _m._report(f"⚠️ Critic: answer corrected ({len(critic_out)} chars)")
        logger.info(f"⚠️ Critic hat Korrekturen vorgenommen: {critic_out[:100]}")
        return {"final_response": critic_out, **usage}
    except Exception as e:
        logger.warning(f"Critic node error: {e}")
        return {"final_response": final_response}


# --- GRAPH ROUTER ---
def _route_cache(state_: AgentState) -> str:
    """On cache hit go directly to merger — entire pipeline is skipped."""
    return "merger" if state_.get("cache_hit") else "semantic_router"

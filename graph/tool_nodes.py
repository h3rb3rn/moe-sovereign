"""graph/tool_nodes.py — deterministic data nodes (MCP, GraphRAG, math)."""

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
    await _report(f"⚙️ MCP Precision Tools: {', '.join(tool_names)}")
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
        _schema = state.MCP_TOOL_SCHEMAS.get(tool, {})
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
        await _report(f"⚙️ MCP-Call: {tool}\nArgs: {json.dumps(args, ensure_ascii=False, indent=2)}")
        _mcp_t0 = time.monotonic()
        try:
            resp = await client.post(f"{MCP_URL}/invoke", json={"tool": tool, "args": args})
            resp.raise_for_status()
            data = resp.json()
            _mcp_dt = round(time.monotonic() - _mcp_t0, 3)
            if "error" in data:
                err_str = data['error']
                await _report(f"⚙️ MCP error [{tool}]: {err_str}")
                _log_tool_eval({
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
                        await _report(f"⚙️ MCP retry OK [{tool}]:\n{result_str2}")
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
            await _report(f"⚙️ MCP result [{tool}]:\n{result_str}")
            logger.info(f"🔧 MCP: [{desc}] {result_str[:120]}")
            _log_tool_eval({
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
            await _report(f"⚙️ MCP exception [{tool}]: {e}")
            _log_tool_eval({
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
    await _report(f"⚙️ MCP: {len(results)} result(s) received")
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
    _is_public_fact_query = bool(_RESEARCH_DETECT.search(state_.get("input", "")))
    if _is_public_fact_query and not _has_knowledge_healing:
        logger.info("⚡ GraphRAG skipped (public-fact query — internal graph not relevant)")
        await _report("⚡ GraphRAG: skipped (external research query)")
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
                await _report(f"🔗 GraphRAG: context from Valkey cache ({len(_cached_ctx_str)} chars)")
                return {"graph_context": _cached_ctx_str}
        except Exception as _ge:
            logger.debug(f"GraphRAG cache read error: {_ge}")

    await _report("🔗 GraphRAG — knowledge graph query (Neo4j)...")
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
            await _report(f"🔗 GraphRAG: {len(ctx)} chars structured context")
            if state.redis_client is not None:
                asyncio.create_task(state.redis_client.setex(_graph_cache_key, 3600, ctx))
        else:
            await _report("🔗 GraphRAG: no matching context found")

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
    await _report("🧮 Math module (SymPy)...")
    from math_node import math_node
    result = await math_node(state_)
    await _report("🧮 Math computation complete")
    return {"math_result": result["math_result"]}

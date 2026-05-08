"""graph/synthesis.py — merger, thinking, conflict resolution, critic, replan router."""

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

# Cross-module: graph context compression helpers live in graph.research
from graph.research import _rerank_graph_context, _compress_graph_context_llm


async def merger_node(state_: AgentState):
    from datetime import datetime
    from parsing import _extract_usage, _parse_expert_confidence, _expert_category, _dedup_by_category, _collect_conflicts, _improvement_ratio
    from metrics import PROM_TOKENS
    from config import KAFKA_TOPIC_REQUESTS
    from services.lineage import (
        start_run as _ol_start, complete_run as _ol_complete, fail_run as _ol_fail,
        dataset_response,
    )
    _ol_merger_run = await _ol_start(
        "merger_node",
        extra_facets={"templateName": {"_producer": "https://github.com/h3rb3rn/moe-sovereign",
                                       "_schemaURL": "moe-sovereign://templateName",
                                       "name": state_.get("template_name", "default")}},
    )

    # Cache hit: direct answer, no LLM call needed
    if state_.get("cache_hit"):
        logger.info("--- [NODE] MERGER (cache hit, direct return) ---")
        await _report("💨 Merger: cached response delivered directly")
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id": state_.get("response_id", ""),
            "input":       state_["input"][:300],
            "cache_hit":   True,
            "ts":          datetime.now().isoformat(),
        }))
        await _ol_complete(_ol_merger_run, job_name="merger_node",
                           outputs=[dataset_response(state_.get("response_id", "cache"))])
        return {"final_response": state_.get("cached_facts", "")}

    logger.info("--- [NODE] MERGER & INGEST ---")
    await _report("🔀 Merger analyzing expert confidence...")

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
        await _report(f"⚖️ Paraconsistent conflicts detected: {', '.join(_cats)}")

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
        await _report(f"⚠️ Low confidence: {', '.join(cats)}")

    # ── Judge Refinement Loop: improve low-confidence expert responses ────────
    if JUDGE_REFINE_MAX_ROUNDS > 0 and expert_results:
        for _refine_round in range(JUDGE_REFINE_MAX_ROUNDS):
            low_conf_list = [r for r in expert_results if _parse_expert_confidence(r) == "low"]
            if not low_conf_list:
                break
            await _report(f"🔄 Refinement round {_refine_round + 1}/{JUDGE_REFINE_MAX_ROUNDS}: "
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
                await _report(f"🔄 Judge refinement prompt (round {_refine_round + 1}):\n{gap_prompt}")
                _gap_res = await _invoke_judge_with_retry(state_, gap_prompt)
                gap_feedback_text = _gap_res.content.strip()
                # Persist refinement reason in state for causal-path logging
                state_["judge_reason"] = gap_feedback_text[:500]
                state_["judge_refined"] = True
                await _report(f"🔄 Judge refinement response (round {_refine_round + 1}):\n{gap_feedback_text}")
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
                await _report(f"🔄 [{_cat}]: {old_conf} → {new_conf} (Δ{ratio:.0%})")
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
                await _report(f"⏹️ Refinement stopped: no significant improvement "
                              f"(< {JUDGE_REFINE_MIN_IMPROVEMENT:.0%})")
                break

    await _report("🔀 Merger synthesizing final response...")

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
            await _report("🛡️ Prior knowledge suppressed (code duplication guard)")
        else:
            sections.append(f"PRIOR KNOWLEDGE (Cache):\n{cached[:1000]}")
    soft_examples = state_.get("soft_cache_examples") or ""
    if soft_examples:
        _soft_has_code = any(m in soft_examples for m in _CODE_MARKERS)
        if _primary_has_code and _soft_has_code:
            logger.info("🛡️ Soft-cache suppressed: primary source + cached snippet both contain code (prevents judge interleaving)")
            await _report("🛡️ Soft-cache examples suppressed (code duplication guard)")
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
        + SYNTHESIS_PERSISTENCE_INSTRUCTION
        + (PROVENANCE_INSTRUCTION if _has_graph_ctx else "")
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
        await _report(f"⚡ Fast-Path: single high-confidence expert ({len(fast_resp)} chars)")
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
            asyncio.create_task(_store_response_metadata(
                state_.get("response_id", ""), state_["input"],
                state_.get("expert_models_used", []), _fp_cid,
                plan=state_.get("plan", []), cost_tier=state_.get("cost_tier", "")))
            asyncio.create_task(_self_evaluate(
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
        await _ol_complete(_ol_merger_run, job_name="merger_node",
                           outputs=[dataset_response(state_.get("response_id", "fast"))])
        return {"final_response": fast_resp, "prompt_tokens": 0, "completion_tokens": 0}

    await _report(f"🔀 Merger prompt ({len(prompt)} chars):\n{prompt}")
    try:
        res = await _invoke_judge_with_retry(state_, prompt, temperature=state_.get("query_temperature"))
    except Exception as e:
        logger.error(f"❌ Merger Judge LLM error: {e}")
        await _report(f"❌ Merger: Judge LLM unreachable ({e})")
        fallback = "\n\n".join(s for s in sections[1:] if s)  # raw sections as emergency response
        await _ol_fail(_ol_merger_run, job_name="merger_node", error=str(e))
        return {"final_response": fallback or "Error: Merger could not generate a response."}
    await _report(f"🔀 Merger response ({len(res.content)} chars):\n{res.content}")
    merger_usage = _extract_usage(res)
    _uid = state_.get("user_id", "anon")
    PROM_TOKENS.labels(model=JUDGE_MODEL, token_type="prompt",      node="merger", user_id=_uid).inc(merger_usage.get("prompt_tokens", 0))
    PROM_TOKENS.labels(model=JUDGE_MODEL, token_type="completion",  node="merger", user_id=_uid).inc(merger_usage.get("completion_tokens", 0))
    _judge_failed = (not res.content.strip() or
                     res.content.startswith("[Judge unavailable"))
    if _judge_failed:
        logger.error("❌ Merger: Judge LLM returned empty/error response (VRAM/OOM?)")
        await _report("❌ Merger: empty or failed answer from judge — possible VRAM exhaustion")
        # Best expert response as fallback
        best = next((r for r in expert_results if _parse_expert_confidence(r) == "high"), None) \
               or (expert_results[0] if expert_results else None)
        fallback = best or "No answer available — please try again."
        await _ol_fail(_ol_merger_run, job_name="merger_node", error="judge_empty_or_error")
        return {"final_response": fallback, **merger_usage}
    await _report(f"✅ Response complete ({len(res.content)} chars)")

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
            _store_response_metadata(
                state_.get("response_id", ""),
                state_["input"],
                state_.get("expert_models_used", []),
                chroma_doc_id,
                plan=state_.get("plan", []),
                cost_tier=state_.get("cost_tier", ""),
            )
        )
        # Self-evaluation via judge LLM (async, fire-and-forget — no latency overhead)
        asyncio.create_task(_self_evaluate(
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

    await _ol_complete(_ol_merger_run, job_name="merger_node",
                       outputs=[dataset_response(state_.get("response_id", "synthesis"))])
    return {
        "final_response":    res_content_clean,
        "provenance_sources": _provenance_sources,
        "conflict_registry": _new_conflicts,
        **merger_usage,
        **_agentic_extra,
    }


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
    await _report("🧠 Reasoning: strukturierte Analyse des Problems...")

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

    await _report(f"🧠 Reasoning-Prompt:\n{reasoning_prompt}")
    try:
        res   = await _invoke_judge_with_retry(state_, reasoning_prompt)
        usage = _extract_usage(res)
        trace = res.content.strip()
        await _report(f"🧠 Reasoning result ({len(trace)} chars):\n{trace}")
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
    await _report(f"⚖️ Resolving {len(pending)} paraconsistent conflict(s)...")

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
                await _report(f"⚖️ [{category}] Judge verdict: {verdict[:120]}")
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
    await _report(f"🔎 Critic: fact-check for {', '.join(sorted(active))}...")

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

    await _report(f"🔎 Critic-Prompt:\n{critic_prompt}")
    try:
        res          = await _invoke_judge_with_retry(state_, critic_prompt)
        usage        = _extract_usage(res)
        critic_out   = res.content.strip()
        await _report(f"🔎 Critic response:\n{critic_out}")

        # Guard: if the judge refused (content filter / VRAM), keep the merger answer unchanged.
        if critic_out.startswith("[Judge unavailable") or not critic_out:
            logger.warning("⚠️ Critic: judge refused — preserving merger answer unchanged")
            await _report("⚠️ Critic: judge refused (content filter?) — merger answer preserved")
            return {"final_response": final_response}

        if critic_out.upper().startswith("CONFIRMED"):
            await _report("✅ Critic: answer confirmed correct")
            logger.info("✅ Critic: no errors found")
            return {"final_response": final_response, **usage}

        await _report(f"⚠️ Critic: answer corrected ({len(critic_out)} chars)")
        logger.info(f"⚠️ Critic hat Korrekturen vorgenommen: {critic_out[:100]}")
        return {"final_response": critic_out, **usage}
    except Exception as e:
        logger.warning(f"Critic node error: {e}")
        return {"final_response": final_response}

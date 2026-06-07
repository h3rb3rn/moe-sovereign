"""graph/expert.py — expert worker (two-tier MoE execution with dependency levels)."""

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
    PLANNER_TIMEOUT, MAX_EXPERT_OUTPUT_CHARS, MAX_EXPERT_TOKENS,
    MAX_EXPERT_TOKENS_CODE, MAX_EXPERT_OUTPUT_CHARS_CODE, JUDGE_MODEL,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    EXPERT_OUTPUT_DIVISOR, EXPERT_INPUT_MIN_CHARS, EXPERT_CHARS_PER_TOKEN,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    ROUTE_THRESHOLD, ROUTE_GAP, CACHE_MIN_RESPONSE_LEN,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    TRIVIAL_LOW_CONF_RESCUE_ENABLED,
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
    PROM_TIER_ESCALATION,
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

# Cross-module: dependency-level helpers live in graph.planner
from graph.planner import _topological_levels, _inject_prior_results


def _tier2_escalation_decision(cost_tier_t1: bool, t1_confs: list, has_tier2: bool) -> str:
    """Decide the two-tier outcome from the T1 confidences. Pure function (unit-tested).

    t1_confs: per-T1-expert confidences ("high"/"medium"/"low"/None for unparseable).
    Returns one of:
      "t1_high_skip" — a T1 expert was high-confidence; T2 skipped (T2 was available)
      "t1_cost_kept" — cost-tier trivial task kept a good-enough (medium) T1 answer
                       even though T2 was available — the deliberate cost saving
      "t1_only"      — kept T1 because no T2 tier was available at all
      "t2_escalated" — escalate to T2

    Rules:
      • normal task      → escalate on anything below 'high'
      • cost-tier trivial → escalate only on a low/empty answer ('medium' keeps the saving)
    """
    t1_has_high = "high" in t1_confs
    t1_is_weak = (not t1_confs) or any(c == "low" for c in t1_confs)
    should_escalate = t1_is_weak if cost_tier_t1 else (not t1_has_high)
    if t1_has_high:
        return "t1_high_skip" if has_tier2 else "t1_only"
    if not has_tier2:
        return "t1_only"
    if not should_escalate:
        # T2 was available but not needed — only reachable on a cost-tier task
        # with a good-enough (medium) answer.
        return "t1_cost_kept"
    return "t2_escalated"


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
            url        = selected.get("url") or URL_MAP.get(endpoint) or ""
            if not url:
                raise ValueError(
                    f"Expert '{model_name}' on endpoint '{endpoint}' has no URL configured. "
                    "Check the inference server settings in the Admin UI."
                )
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
                _get_expert_prompt(cat, state_.get("user_experts"))
                + mode_cfg["expert_suffix"]
                + _conf_format_for_mode(mode)
            )
            # CC profile behavioral directives are prepended as highest-priority instructions
            _behavioral = (state_.get("behavioral_directives") or "").strip()
            if _behavioral:
                sys_prompt = f"MANDATORY RESPONSE DIRECTIVES (override all other instructions):\n{_behavioral}\n\n" + sys_prompt
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
            # Categories that generate large code artifacts need higher token/output limits.
            # Defined here (before first use) to avoid Python's "referenced before assignment"
            # error that occurs when the name appears anywhere in the enclosing scope.
            _CODE_GEN_CATS = {"code_reviewer", "devops_sre", "frontend", "backend", "fullstack"}
            # Derive per-expert output and input limits from context window.
            # Code-generation categories use a much higher output cap so that
            # large HTML/JS/Python files are not truncated mid-function.
            _max_output_cap = (
                MAX_EXPERT_OUTPUT_CHARS_CODE if cat in _CODE_GEN_CATS else MAX_EXPERT_OUTPUT_CHARS
            )
            _expert_max_output = _max_output_cap
            _max_input_chars = 0
            if _expert_ctx_window > 0:
                # Reserve 1/EXPERT_OUTPUT_DIVISOR of window for output, capped at category cap
                _expert_max_output = min(_max_output_cap, max(EXPERT_INPUT_MIN_CHARS, _expert_ctx_window // EXPERT_OUTPUT_DIVISOR))
                # EXPERT_CHARS_PER_TOKEN chars/token conservative estimate for mixed content
                _max_input_chars = max(EXPERT_INPUT_MIN_CHARS, _expert_ctx_window * EXPERT_CHARS_PER_TOKEN)
                _available_task_chars = _max_input_chars - len(sys_prompt)
                if len(task_text) > _available_task_chars > 0:
                    task_text = task_text[:_available_task_chars] + "\n[…truncated for context window]"

            # Context-aware history trimming: keep as much history as fits within the
            # model's actual context window after system prompt and task are accounted for.
            # This prevents context flooding without hardcoding a static character limit.
            _local_history: List[Dict] = list(chat_history)
            if _max_input_chars > 0 and _local_history:
                _hist_budget = max(0, _max_input_chars - len(sys_prompt) - len(task_text))
                _hist_total = sum(len(str(m.get("content", ""))) for m in _local_history)
                if _hist_total > _hist_budget > 0:
                    _local_history = _truncate_history(
                        _local_history,
                        max_turns=-1,
                        max_chars=_hist_budget,
                    )
                    logger.info(
                        f"🗜️ Expert [{cat}] history trimmed: {_hist_total} → {_hist_budget} chars "
                        f"(ctx={_expert_ctx_window//1024}K)"
                    )

            logger.info(f"🚀 Expert {t_idx}.{e_idx} GPU#{gpu} [{model_name} / {cat}]")

            # Build messages list: system + history + user turn (with optional image blocks)
            messages: List[Dict] = [{"role": "system", "content": sys_prompt}]
            if _local_history:
                messages.extend(_local_history)

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

            await _report(
                f"🚀 Expert [{model_name} / {cat}] GPU#{gpu}\n"
                f"  Task: {task_text}"
                + (f" | ctx={_expert_ctx_window//1024}K" if _expert_ctx_window else "")
            )
            await _report(
                f"📤 Expert [{model_name} / {cat}] System-Prompt:\n{sys_prompt}"
            )
            expert_base_url = url.rstrip("/").removesuffix("/v1")
            _expert_node_timeout = float(
                selected.get("timeout", EXPERT_TIMEOUT)
                if not LITELLM_URL and not model_cfg.get("_user_conn_url")
                else EXPERT_TIMEOUT
            )
            _expert_temp = state_.get("query_temperature")  # None = API default
            # max_tokens must be passed via model_kwargs so LangChain forwards it
            # verbatim to Ollama as "max_tokens". Using the top-level max_tokens
            # parameter causes LangChain to rename it to "max_completion_tokens",
            # which Ollama ignores — resulting in unlimited generation.
            # Code-generation categories need much higher token limits than
            # factual-lookup categories. A full browser game or backend service
            # can easily exceed 16k tokens; 4096 would truncate mid-function.
            # _CODE_GEN_CATS is already defined above (before first use).
            _expert_max_tokens = (
                MAX_EXPERT_TOKENS_CODE if cat in _CODE_GEN_CATS else MAX_EXPERT_TOKENS
            )
            _model_kw: dict = {"max_tokens": _expert_max_tokens}
            if _expert_ctx_window > 0:
                # Pass num_ctx to Ollama so the model is loaded with the correct
                # context window. Without this Ollama defaults to 8192 regardless
                # of what is defined in the template or the model's native capacity.
                _model_kw["num_ctx"] = _expert_ctx_window
            _llm_kwargs: dict = {"model": model_name, "base_url": url, "api_key": token,
                                 "timeout": _expert_node_timeout,
                                 "model_kwargs": _model_kw}
            if _expert_temp is not None:
                _llm_kwargs["temperature"] = _expert_temp
            # thinking_mode=False: inject /no_think directive into the last human message.
            # Ollama 0.24 does not support think=false via the OpenAI-compatible API;
            # the /no_think prefix in the user message is the reliable cross-version method.
            # qwen3 respects this directive and skips the <think>…</think> block entirely,
            # saving ~30k tokens and 10+ minutes for factual-lookup categories.
            _thinking_enabled = bool(model_cfg.get("thinking_mode", True))
            if not _thinking_enabled and messages:
                from langchain_core.messages import HumanMessage
                _patched = list(messages)
                for _i in reversed(range(len(_patched))):
                    if hasattr(_patched[_i], "type") and _patched[_i].type == "human":
                        _orig = _patched[_i].content
                        _patched[_i] = HumanMessage(content=f"/no_think\n{_orig}")
                        break
                messages = _patched
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
                    await _report(f"⚠️ Expert [{cat}]: used local fallback (primary endpoint degraded)")
                usage = _extract_usage(res)
                # Strip thinking traces before truncation so the actual answer
                # is captured instead of the thinking preamble. Thinking-mode
                # models (qwen3.6:35b) output <think>...</think> first which
                # would otherwise fill the entire _expert_max_output window.
                import re as _re
                _raw_content = _re.sub(
                    r'<think>.*?</think>', '', res.content, flags=_re.DOTALL
                ).strip()
                content = _raw_content[:_expert_max_output]
                if len(_raw_content) > _expert_max_output:
                    content += "\n[…truncated]"
                await _report(f"✅ Expert [{model_name} / {cat}]:\n{content}\n---")
                # Token metrics
                _uid = state_.get("user_id", "anon")
                PROM_TOKENS.labels(model=model_name, token_type="prompt",      node=endpoint, user_id=_uid).inc(usage.get("prompt_tokens", 0))
                PROM_TOKENS.labels(model=model_name, token_type="completion",  node=endpoint, user_id=_uid).inc(usage.get("completion_tokens", 0))
                # Confidence automatically as performance signal → no waiting for user feedback needed
                conf = _parse_expert_confidence(content)
                await _report(
                    f"  → [{model_name}/{cat}] Confidence: {conf or '?'} | "
                    f"{usage.get('prompt_tokens', 0)}→{usage.get('completion_tokens', 0)} tok"
                )
                # Structured per-call line (greppable for load-test analysis): which
                # tier/node handled this category, with token cost and confidence.
                logger.info(
                    "📊 expert_call model=%s node=%s tier=%s cat=%s tokens=%s->%s conf=%s%s",
                    model_name, endpoint, model_cfg.get("_tier", "?"), cat,
                    usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                    conf or "unknown",
                    " fallback" if _used_fallback else "",
                )
                PROM_CONFIDENCE.labels(level=conf or "unknown", category=cat).inc()
                # Thompson-sampling reward is no longer recorded here from the
                # expert's *self-reported* confidence — a confidently-wrong answer
                # would be rewarded. The outcome is recorded later in merger_node
                # (graph/synthesis.py), where the judge's verdict is available:
                # a category the judge had to refine counts as negative. Self-
                # confidence is used only as a fallback when refinement is disabled.
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
                    await _report(f"❌ Expert {model_name}: GPU/HTTP error")
                    return {"res": f"[{model_name} ERROR]: VRAM/HTTP", "model_cat": None}
                PROM_EXPERT_FAILURES.labels(model=model_name, reason="error").inc()
                logger.error(f"❌ Expert {model_name}: {e}")
                await _report(f"❌ Expert {model_name}: error")
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

        # Cost-tier enforcement: trivial tasks use at most one T1 expert to reduce
        # token consumption. T2 is normally skipped — but when the low-confidence
        # rescue is enabled it stays available as a fallback that only fires if the
        # single T1 answer comes back low-confidence/empty (see escalation below).
        cost_tier_t1 = bool(state_.get("force_tier1")) and bool(tier1)
        if cost_tier_t1:
            tier1 = tier1[:1]  # only the top-scored T1 expert
            if TRIVIAL_LOW_CONF_RESCUE_ENABLED:
                tier2 = tier2[:1]  # rescue uses at most the single best T2 expert
            else:
                tier2 = []         # strict mode: T2 never runs for trivial cost-tier tasks

        task_results: List[dict] = []

        # Forced + T1 start in parallel in one batch
        parallel_batch = (
            [run_single(e, task, i + 1, j + 1) for j, e in enumerate(forced_experts)] +
            [run_single(e, task, i + 1, len(forced_experts) + j + 1) for j, (_, e) in enumerate(tier1)]
        )

        if forced_experts:
            forced_names = ", ".join(e["model"] for e in forced_experts)
            await _report(f"🔀 Forced-Ensemble [{cat}]: {forced_names}")
        if tier1:
            t1_names = ", ".join(e["model"] for _, e in tier1)
            await _report(f"⚡ T1 [{cat}]: {t1_names}")

        if parallel_batch:
            combined = await asyncio.gather(*parallel_batch)
            n_forced      = len(forced_experts)
            forced_results = list(combined[:n_forced])
            t1_results     = list(combined[n_forced:])
            task_results.extend(forced_results)
            task_results.extend(t1_results)

            if t1_results:
                t1_confs = [_parse_expert_confidence(r.get("res", "")) for r in t1_results if r.get("res")]
                decision = _tier2_escalation_decision(cost_tier_t1, t1_confs, bool(tier2))
                if decision != "t2_escalated":
                    PROM_TIER_ESCALATION.labels(category=cat, decision=decision).inc()
                    if decision == "t1_high_skip":
                        logger.info(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                        await _report(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                    elif decision == "t1_cost_kept":
                        logger.info(f"💰 T1 [{cat}]: cost-tier kept (medium conf) — T2 rescue not needed")
                    return task_results
            elif not tier2:
                return task_results

        if tier2:
            PROM_TIER_ESCALATION.labels(category=cat, decision="t2_escalated").inc()
            t2_names = ", ".join(e["model"] for _, e in tier2)
            _why = "low-confidence T1 rescue" if cost_tier_t1 else "no T1 high confidence"
            logger.info(f"🔬 T2 [{cat}]: {t2_names} — escalated ({_why})")
            await _report(f"🔬 T2 [{cat}]: {t2_names} (T1 insufficient)")
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
            await _report(
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

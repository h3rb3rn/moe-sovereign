"""graph/planner.py — planner node, plan sanitization, dependency-level helpers."""

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
    # Lazy import to avoid circular: main imports from graph/nodes; this call happens at runtime.
    from main import _build_filtered_tool_desc
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
        await _report(_pr)

    # ── Agentic loop: read config from template state ───────────────────────
    _agentic_iteration  = state_.get("agentic_iteration") or 0
    _agentic_max_rounds = state_.get("max_agentic_rounds") or 0
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
        f"{len(state.MCP_TOOLS_DESCRIPTION)}:{(state_.get('planner_prompt') or '')[:80]}"
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
                await _report("📋 Planner: plan loaded from Valkey cache")
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
        await _report("📋 Agent mode: code experts activated...")
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
        await _report("🧠 Memory Expert: Analysiere Konversationshistorie...")
        return {
            **_complexity_state_update,
            "plan": [{"task": state_["input"], "category": "memory_recall"}],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    logger.debug("--- [NODE] PLANNER ---")
    await _report("📋 Planner analyzing request...")
    # When a template defines its own expert set, restrict routing to those categories so
    # the planner cannot accidentally route to a global expert not wired in the template.
    # Fall back to the global EXPERTS list when no template experts are active.
    _NON_EXPERT = {"precision_tools", "research"}
    _user_experts_for_cats = state_.get("user_experts") or {}
    if _user_experts_for_cats:
        expert_categories = [c for c in _user_experts_for_cats.keys()
                             if c not in _NON_EXPERT]
    else:
        expert_categories = list(EXPERTS.keys())
    if "agentic_coder" not in expert_categories and (
        state_.get("mode") in ("agent_orchestrated", "code")
        or "agentic_coder" in _user_experts_for_cats
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
        f"{state.AGENTIC_CODE_TOOLS_DESCRIPTION}\n"
        f"Format: {{\"task\": \"...\", \"category\": \"precision_tools\", "
        f"\"mcp_tool\": \"repo_map|read_file_chunked|lsp_query\", \"mcp_args\": {{...}}}}\n"
        f"THEN use agentic_coder expert for analysis/implementation.\n"
    ) if _inject_agentic and state.AGENTIC_CODE_TOOLS_DESCRIPTION else ""

    _planner_role = (state_.get("planner_prompt") or "").strip() or DEFAULT_PLANNER_ROLE

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
        await _report(
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
{_build_filtered_tool_desc(state_["input"], enable_graphrag=state_.get("enable_graphrag", False)) if state_.get("complexity_level") != "trivial" else "  - calculate: arithmetic and math  - date_diff: date calculations  - unit_convert: unit conversions"}
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
{_build_skill_catalog()}
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
    await _report(f"📋 Planner prompt ({len(prompt)} chars):\n{prompt}")
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
            await _report("⚠️ Planner: used local fallback (primary endpoint degraded)")
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
            await _report(f"📋 Plan: {len(plan)} Task(s) → {', '.join(categories)}")
            for _pt in plan:
                _desc = (_pt.get("task") or "")[:80]
                _ptcat = _pt.get("category", "?")
                _extra = "…" if len(_pt.get("task", "")) > 80 else ""
                await _report(f"  • [{_ptcat}] {_desc}{_extra}")
            await _report(
                f"📋 Planner done — {total_usage['prompt_tokens']} prompt tok / "
                f"{total_usage['completion_tokens']} completion tok"
            )
            break
        except Exception:
            if attempt == 0:
                logger.warning(f"Planner parse error (attempt 1) — retry. Output: {res.content[:200]!r}")
                await _report("⚠️ Planner: JSON error — retrying...")
                continue
            logger.warning(f"Planner could not parse JSON — fallback. Output: {res.content[:200]!r}")
            await _report("⚠️ Planner-Fallback: general")
            plan = [{"task": state_["input"], "category": "general"}]
            _extracted_filters = {}
    # Unload planner model — unless the same model is immediately needed as expert.
    # Use the template-specific planner model/URL when the template overrides them,
    # so we unload from the correct node instead of always hitting the global default.
    _actual_planner_model = (state_.get("planner_model_override") or PLANNER_MODEL).strip()
    _actual_planner_url   = (state_.get("planner_url_override")   or PLANNER_URL or "").strip()
    _actual_planner_token = (state_.get("planner_token_override") or PLANNER_TOKEN or "ollama").strip()
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
    elif _actual_planner_base and _actual_planner_token == "ollama":
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

"""graph/research.py — web research, domain discovery, graph context compression, fallback search."""

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

        await _report(f"🌐 Web search [{idx+1}/{total}]: '{query[:60]}'...")
        raw = await _web_search_with_citations(query, ddg_fallback=state_.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG))
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
            await _report(f"🌐 Search [{idx+1}] result ({len(raw)} chars, quality={quality})")
            if state.redis_client is not None and _effective_ttl > 0:
                try:
                    await state.redis_client.set(_cache_key, raw.encode("utf-8"), ex=_effective_ttl)
                except Exception:
                    pass
            return raw, query, quality

        await _report(f"🌐 Search [{idx+1}]: no result (SearXNG unreachable or empty)")
        return "", query, "empty"

    if mode in ("research", "plan") or _is_agentic_replan:
        # Deep research mode OR agentic re-plan: run ALL tasks in parallel for maximum coverage.
        # In agentic re-plan we need every available search result to resolve the gap.
        logger.info(f"--- [NODE] WEB RESEARCH (MULTI — {len(research_tasks)} queries, agentic={_is_agentic_replan}) ---")
        await _report(f"🌐 {'Agentic re-search' if _is_agentic_replan else 'Deep research'}: {len(research_tasks)} parallel search(es)...")

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

        await _report(
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
    if not model or judge_llm is None:
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
            base_url=judge_llm.openai_api_base,
            api_key=judge_llm.openai_api_key,
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
    await _report(f"🔍 Research fallback — new strategy: '{strategy_hint[:60]}'")

    async def _search_one(item: dict) -> str:
        query = item["query"][:180]
        cat_s = item["category"]
        await _report(f"🌐 Fallback search [{cat_s}]: '{query[:60]}'...")
        raw = await _web_search_with_citations(query, ddg_fallback=state_.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG))
        if raw:
            await _report(f"🌐 [{cat_s}]: {len(raw)} chars fallback result")
            logger.info(f"🌐 Research Fallback [{cat_s}]: {len(raw)} chars")
            return f"[Research Fallback / {cat_s}]:\n{raw[:2000]}"
        await _report(f"⚠️ Fallback search [{cat_s}] returned empty")
        return ""

    results = await asyncio.gather(*[_search_one(s) for s in searches])
    new_web  = "\n\n".join(r for r in results if r)
    combined = "\n\n".join(filter(None, [existing_web, new_web]))
    return {"web_research": combined}

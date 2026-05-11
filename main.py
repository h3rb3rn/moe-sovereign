import os, time, uuid, operator, uvicorn, logging, json, re, asyncio, contextvars, hashlib, threading
from pathlib import Path

# Load .env at runtime so profile changes take effect after container restart
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv("/app/.env", override=True)
except ImportError:
    pass
from prometheus_client import (
    Counter, Histogram, Gauge,
    generate_latest, CONTENT_TYPE_LATEST,
)
from datetime import datetime, timedelta, timezone
from typing import List, Annotated, Dict, Any, TypedDict, Optional, Union, AsyncGenerator
from pipeline.state import AgentState
from pipeline.logic_types import goedel_tnorm, lukasiewicz_tnorm
from web_search import (
    _domain_score,
    _reliability_label,
    _web_search_with_citations as _web_search_with_citations_impl,
)
from parsing import (
    _extract_usage,
    _extract_json,
    _parse_expert_confidence,
    _parse_expert_gaps,
    _expert_category,
    _dedup_by_category,
    _collect_conflicts,
    _compute_routing_confidence,
    _truncate_history as _truncate_history_pure,
    _improvement_ratio,
    _oai_content_to_str,
    _anthropic_content_to_text,
    _extract_images,
    _extract_oai_images,
    _anthropic_to_openai_messages,
    _anthropic_tools_to_openai,
)

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import telemetry as _telemetry
from graph_rag.corrections import (
    store_correction as _store_correction,
    query_corrections as _query_corrections,
    format_correction_context as _format_correction_context,
    ensure_schema as _ensure_correction_schema,
)

from config import (
    # Runtime flags
    CORRECTION_MEMORY_ENABLED, _EDGE_MODE, LOG_LEVEL,
    # Kafka
    KAFKA_BOOTSTRAP, KAFKA_TOPIC_INGEST, KAFKA_TOPIC_REQUESTS,
    KAFKA_TOPIC_FEEDBACK, KAFKA_TOPIC_LINTING, KAFKA_TOPIC_AUDIT,
    # Database
    REDIS_URL, POSTGRES_CHECKPOINT_URL, MOE_USERDB_URL,
    # Enterprise stack
    _ENTERPRISE_ENABLED, NIFI_URL, MARQUEZ_URL, LAKEFS_ENDPOINT,
    # OIDC
    AUTHENTIK_URL, OIDC_JWKS_URL, OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_ENABLED,
    # Neo4j
    NEO4J_URI, NEO4J_USER, NEO4J_PASS,
    # Inference
    INFERENCE_SERVERS_LIST, URL_MAP, TOKEN_MAP, API_TYPE_MAP,
    JUDGE_ENDPOINT_NAME, JUDGE_URL, JUDGE_TOKEN, JUDGE_MODEL,
    GRAPH_INGEST_MODEL, GRAPH_INGEST_URL, GRAPH_INGEST_TOKEN,
    PLANNER_MODEL, PLANNER_ENDPOINT, PLANNER_URL, PLANNER_TOKEN,
    _JUDGE_BASE, _PLANNER_BASE,
    EXPERTS, MCP_URL, GRAPH_VIA_MCP, MAX_GRAPH_CONTEXT_CHARS, LITELLM_URL,
    # Shadow mode
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    # Claude Code
    CLAUDE_CODE_MODELS, CLAUDE_CODE_TOOL_MODEL, CLAUDE_CODE_TOOL_ENDPOINT,
    CLAUDE_CODE_MODE, CLAUDE_CODE_REASONING_MODEL, CLAUDE_CODE_REASONING_ENDPOINT,
    CLAUDE_CODE_TOOL_CHOICE,
    _CLAUDE_CODE_TOOL_URL, _CLAUDE_CODE_TOOL_TOKEN, _CLAUDE_CODE_REASONING_URL,
    _DEFAULT_CLAUDE_CODE_MODELS,
    # Thresholds & limits
    MAX_EXPERT_OUTPUT_CHARS, CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD,
    SOFT_CACHE_MAX_EXAMPLES, ROUTE_THRESHOLD, ROUTE_GAP, CACHE_MIN_RESPONSE_LEN,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    # History
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS, HISTORY_MAX_ENTRIES,
    # Timeouts
    JUDGE_TIMEOUT, EXPERT_TIMEOUT, PLANNER_TIMEOUT,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    TOOL_MAX_TOKENS, REASONING_MAX_TOKENS,
    PLANNER_RETRIES, PLANNER_MAX_TASKS, SSE_CHUNK_SIZE,
    # Feedback
    EVAL_CACHE_FLAG_THRESHOLD, FEEDBACK_POSITIVE_THRESHOLD, FEEDBACK_NEGATIVE_THRESHOLD,
    # Web search
    _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    # Primary endpoint fallback
    _FALLBACK_NODE, _FALLBACK_MODEL, _FALLBACK_MODEL_SECOND,
    _FALLBACK_ENABLED,
    _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY, _ENDPOINT_DEGRADED_TTL,
    _EXTERNAL_ENDPOINT_PATTERNS,
    # Thompson / fuzzy / graph compress
    THOMPSON_SAMPLING_ENABLED,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
    # HTTP limits & CORS
    MAX_REQUEST_BODY_MB, CORS_ALL_ORIGINS, CORS_ORIGINS_RAW, MAX_REQUESTS_PER_MINUTE,
    # Custom prompts
    _CUSTOM_EXPERT_PROMPTS,
)
import starfleet_config as _starfleet
import watchdog as _watchdog
import mission_context as _mission_context
import httpx
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response, JSONResponse
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
from langchain_community.utilities import SearxSearchWrapper
from langgraph.graph import StateGraph, END
from contextlib import asynccontextmanager

if not _EDGE_MODE:  # _EDGE_MODE imported from config
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import chromadb
    from chromadb.utils import embedding_functions

# Import for the math node
from math_node import math_node
# Import for GraphRAG
from graph_rag import GraphRAGManager
# Import context window budget helper
from context_budget import graphrag_budget_chars, web_research_budget
# Import Tier-2 semantic memory (warm context retrieval from ChromaDB)
from memory_retrieval import get_memory_store, compute_evicted_turns

# --- LOGGING (LOG_LEVEL imported from config) ---
logging.basicConfig(level=getattr(logging, LOG_LEVEL), format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MOE-SOVEREIGN")

import state
from prompts import (
    SYNTHESIS_PERSISTENCE_INSTRUCTION,
    PROVENANCE_INSTRUCTION,
    DEFAULT_PLANNER_ROLE,
)

# JWKS cache: (keys_dict, fetched_at)
_jwks_cache: tuple = (None, 0.0)

# ─── Global-state locks ────────────────────────────────────────────────────────
# asyncio does not share a thread with other coroutines while awaiting, but
# synchronous dict mutation (e.g. _endpoint_gpu_indices[k] = v) is NOT atomic
# under concurrent asyncio tasks on CPython once the GIL is released between
# byte-code instructions. threading.Lock is the correct primitive here because:
#   (a) all writers run in the same event-loop thread — so Lock.acquire() never
#       blocks the event loop longer than the locked section itself (a few ns);
#   (b) asyncio.Lock would require async with, which is heavier and unnecessary
#       for pure-synchronous dict updates.
# _shadow_lock + _shadow_request_counter live in services/helpers.py
# _gpu_lock and _cache_lock live in services/inference.py (guards inference caches)


# Template loaders moved to services/templates.py
from services.templates import (
    _read_expert_templates,
    _load_templates_from_db_sync,
    _load_templates_from_env_file,
)


from services.templates import _read_cc_profiles

# Neo4j, inference servers, benchmark constants imported from config.py
if not INFERENCE_SERVERS_LIST and os.getenv("INFERENCE_SERVERS", "").strip():
    logger.warning("INFERENCE_SERVERS JSON is invalid — no inference servers loaded")
# DEFAULT_PLANNER_ROLE moved to prompts.py


# _resolve_user_experts, _resolve_template_prompts moved to services/routing.py
from services.routing import (
    _resolve_user_experts, _resolve_template_prompts,
    _server_info, _is_endpoint_error,
)
from services.tracking import (
    _log_usage_to_db, _register_active_request,
    _deregister_active_request, _increment_user_budget,
    _check_ip_rate_limit,
)

# Helper functions moved to services/helpers.py
from services.helpers import (
    _log_tool_eval, _tool_eval_logger,
    _update_rate_limit_headers, _check_rate_limit_exhausted,
    _conf_format_for_mode, _get_expert_prompt,
    _truncate_history, _apply_semantic_memory,
    _web_search_with_citations,
    _store_response_metadata, _self_evaluate, _neo4j_terms_exist,
    _report, _progress_queue,
    _shadow_request, _shadow_lock,
)
from services.auth import _extract_session_id


# Skills moved to services/skills.py
from services.skills import (
    _SKILLS_DIR, _COMMUNITY_SKILLS_DIR,
    _FILE_SKILL_MAP, _MIME_SKILL_MAP,
    _load_skill_body, _build_skill_catalog, _resolve_skill_invocation,
    _ensure_skill_registry_schema, _bootstrap_skill_registry,
    _check_skill_approved, _log_skill_execution, _resolve_skill_secure,
    _skill_for_file, _detect_file_skill,
)


# Claude Code constants imported from config.py
# ─── Claude Code Integration Profiles ────────────────────────────────────────
# Profiles loaded dynamically via _read_cc_profiles() (60s cache from .env file)
_CC_SYSTEM_PREFIX = ""
_CC_STREAM_THINK  = False

# _provider_rate_limits lives in state.py — imported here for backward compat
import state as _state_prl
_provider_rate_limits = _state_prl._provider_rate_limits  # same dict object


# MCP runtime state lives in state.py — populated by _load_mcp_tool_descriptions().
# Code-navigation tool names — used by _load_mcp_tool_descriptions to split
# AGENTIC_CODE_TOOLS_DESCRIPTION from the general MCP_TOOLS_DESCRIPTION block.
_AGENTIC_TOOL_NAMES = {"repo_map", "read_file_chunked", "lsp_query"}

# ── Tool groups for domain-filtered planner injection ────────────────────────
# Core: always shown — fundamental precision + web access
_TOOL_GROUP_CORE = frozenset({
    "web_researcher", "web_search_domain", "fetch_pdf_text",
    "wikipedia_get_section",
    "wikidata_search", "wikidata_sparql",
    "web_browser", "duckduckgo_search",
    "calculate", "python_sandbox",
    "date_diff", "date_add", "unit_convert",
})
# Research: shown when query contains paper/author/database/species/media markers
_TOOL_GROUP_RESEARCH = frozenset({
    "semantic_scholar_search", "pubmed_search",
    "crossref_lookup", "openalex_search",
    "orcid_works_count",
    "wayback_fetch",
    "pubchem_compound_search", "pubchem_advanced_search",
    "github_search_issues", "github_issue_events",
    "youtube_transcript",
    "chess_analyze_position", "chess_legal_moves",
})
# Data/Math: shown for numeric, statistical, or text-processing queries
_TOOL_GROUP_DATA = frozenset({
    "statistics_calc", "regex_extract", "json_query", "text_analyze",
    "hash_text", "base64_codec", "solve_equation", "gcd_lcm",
    "prime_factorize", "roman_numeral", "subnet_calc", "day_of_week",
})
# Legal: shown only for law/regulatory queries
_TOOL_GROUP_LEGAL = frozenset({
    "legal_search_laws", "legal_get_law_overview",
    "legal_get_paragraph", "legal_fulltext_search",
})
# Files/Graph: shown for attachment or knowledge-graph queries
_TOOL_GROUP_FILES = frozenset({
    "parse_attachment", "file_upload", "file_download_url",
    "graph_query", "graph_ingest", "graph_provenance", "graph_analyze",
})

# Routing detection regexes moved to prompts.py
from prompts import _RESEARCH_DETECT, _LEGAL_DETECT, _DATA_DETECT, _FILE_DETECT


def _build_filtered_tool_desc(query: str, enable_graphrag: bool = False) -> str:
    """Return MCP tool description block filtered to the query's domain.

    Always includes CORE tools.  Adds RESEARCH, DATA, LEGAL, FILE groups
    when the query contains matching markers.  Falls back to the global
    MCP_TOOLS_DESCRIPTION if the per-tool dict is not yet populated.
    """
    if not state._MCP_TOOLS_DICT:
        return state.MCP_TOOLS_DESCRIPTION

    active = set(_TOOL_GROUP_CORE)
    if _RESEARCH_DETECT.search(query):
        active |= _TOOL_GROUP_RESEARCH
    if _LEGAL_DETECT.search(query):
        active |= _TOOL_GROUP_LEGAL
    if _DATA_DETECT.search(query):
        active |= _TOOL_GROUP_DATA
    if _FILE_DETECT.search(query) or enable_graphrag:
        active |= _TOOL_GROUP_FILES

    lines = [
        f"  - {name}: {desc}"
        for name, desc in state._MCP_TOOLS_DICT.items()
        if name in active
    ]
    return "\n".join(lines) if lines else state.MCP_TOOLS_DESCRIPTION

# Categories handled by specialized nodes (not by expert LLMs)
NON_EXPERT_CATEGORIES = {"precision_tools", "research"}

# Routing thresholds, timeouts, feedback thresholds imported from config.py

# _CONF_FORMAT_* constants moved to prompts.py

from metrics import (
    PROM_TOKENS, PROM_REQUESTS, PROM_BUDGET_EXCEEDED, PROM_EXPERT_CALLS,
    PROM_CONFIDENCE, PROM_CACHE_HITS, PROM_CACHE_MISSES,
    PROM_RESPONSE_TIME, PROM_SELF_EVAL, PROM_FEEDBACK, PROM_COMPLEXITY,
    PROM_CHROMA_DOCS, PROM_GRAPH_ENTITIES, PROM_GRAPH_RELATIONS,
    PROM_GRAPH_DENSITY, PROM_SYNTHESIS_NODES, PROM_FLAGGED_RELS,
    PROM_SYNTHESIS_CREATED, PROM_LINTING_RUNS, PROM_LINTING_ORPHANS,
    PROM_LINTING_CONFLICTS, PROM_LINTING_DECAY, PROM_QUARANTINE_ADDED,
    PROM_ACTIVE_REQUESTS, PROM_SERVER_UP, PROM_SERVER_MODELS,
    PROM_SERVER_LOADED_MODELS, PROM_SERVER_VRAM_BYTES, PROM_SERVER_MODEL_VRAM_BYTES,
    PROM_PLANNER_PATS, PROM_ONTOLOGY_GAPS, PROM_ONTOLOGY_ENTS,
    PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS, PROM_TOOL_FORMAT_ERRORS,
    PROM_TOOL_CALL_SUCCESS,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
    PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_STORED, PROM_CORRECTIONS_INJECTED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES,
)

# --- USER AUTH & USAGE TRACKING ---

async def _validate_oidc_token(token: str) -> Optional[dict]:
    """Validate an OIDC JWT from Authentik and return user context dict."""
    if not OIDC_ENABLED:
        return None
    try:
        import jwt as _jwt
        jwks = await _fetch_jwks()
        if not jwks:
            return None
        # Decode without verification first to get the kid
        header = _jwt.get_unverified_header(token)
        kid = header.get("kid")
        # Find the matching key
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid or kid is None:
                key = _jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                break
        if key is None:
            return None
        payload = _jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=OIDC_CLIENT_ID,
            issuer=OIDC_ISSUER,
        )
        username   = payload.get("preferred_username") or payload.get("sub", "")
        email      = payload.get("email", "")
        groups     = payload.get("groups", [])
        is_admin   = "moe-admins" in groups
        # Try to look up local user for budget/permission context
        try:
            if state._userdb_pool is not None:
                async with state._userdb_pool.connection() as conn:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute(
                            "SELECT * FROM users WHERE username=%s OR email=%s LIMIT 1",
                            (username, email),
                        )
                        local = await cur.fetchone()
                if local:
                    return {
                        "user_id":      local["id"],
                        "username":     local["username"],
                        "is_admin":     bool(local.get("is_admin", is_admin)),
                        "budget_daily": None,
                        "budget_monthly": None,
                        "is_active":    "1",
                        "auth_method":  "oidc",
                    }
        except Exception:
            pass
        # Fallback: construct minimal context from JWT claims
        return {
            "user_id":      hashlib.sha256(f"oidc:{payload.get('sub',username)}".encode()).hexdigest()[:32],
            "username":     username,
            "is_admin":     is_admin,
            "budget_daily": None,
            "budget_monthly": None,
            "is_active":    "1",
            "auth_method":  "oidc",
        }
    except Exception as e:
        logger.debug("OIDC token validation failed: %s", e)
        return None


def _extract_api_key(request: Request) -> Optional[str]:
    """Extract API key from Authorization header or x-api-key."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip() or None


# Inference cluster moved to services/inference.py
from services.inference import (
    _model_avail_cache, _MODEL_AVAIL_TTL, _get_available_models,
    _endpoint_semaphores, _endpoint_gpu_indices, _init_semaphores, assign_gpu,
)
# --- DB SETUP ---
if _EDGE_MODE:
    from edge_vector_store import EdgeClient
    chroma_client    = EdgeClient()
    default_ef       = None
    cache_collection = chroma_client.get_or_create_collection("moe_fact_cache")
    route_collection = chroma_client.get_or_create_collection("task_type_prototypes")
else:
    chroma_client    = chromadb.HttpClient(host=os.getenv("CHROMA_HOST", "chromadb-vector"), port=8000)
    default_ef       = embedding_functions.DefaultEmbeddingFunction()
    cache_collection = chroma_client.get_or_create_collection(name="moe_fact_cache", embedding_function=default_ef)
    # Second collection for semantic pre-routing: prototypical task queries per category
    route_collection = chroma_client.get_or_create_collection(name="task_type_prototypes", embedding_function=default_ef)

import state as _state_ref
_state_ref.cache_collection = cache_collection
_state_ref.route_collection = route_collection

# MODES, _MODEL_ID_TO_MODE imported from config.py
from config import MODES, _MODEL_ID_TO_MODE

# --- API SCHEMAS ---
class Message(BaseModel):
    role: str
    content: Optional[Union[str, List[Any]]] = None  # str or multimodal list (image_url etc.)

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: bool = False
    tools: Optional[List[Dict]] = None          # Coding agents (OpenCode, Continue.dev) send tools
    tool_choice: Optional[Any] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream_options: Optional[Dict] = None
    files: Optional[List[Any]] = None           # OpenWebUI file attachments [{type, id, name, ...}]
    no_cache: bool = False                      # If True, skip L0 (Redis) and L1 (ChromaDB) cache reads and writes
    max_agentic_rounds: Optional[int] = None   # Override template's max_agentic_rounds for this request

class FeedbackRequest(BaseModel):
    response_id: str           # chat_id from the response ("chatcmpl-...")
    rating: int                # 1-5  (1-2=negativ, 3=neutral, 4-5=positiv)
    correction: Optional[str] = None  # optional corrected response

# AgentState is defined in pipeline/state.py and imported at the top of this file.
# See that module for full field documentation grouped by purpose.

# LLM instances and SearxNG search singleton moved to services/llm_instances.py
from services.llm_instances import judge_llm, planner_llm, ingest_llm, search
# Mutable globals — set by lifespan (state.graph_manager, state.redis_client, state.kafka_producer in state.py in next step)
state.graph_manager:  Optional[GraphRAGManager]  = None
state.redis_client:   Optional[aioredis.Redis]   = None
state.kafka_producer: Optional[AIOKafkaProducer] = None

# Inference helpers moved to services/inference.py
from services.inference import (
    _perf_key, _ollama_unload,
    _degraded_endpoints, _mark_endpoint_degraded, _endpoint_is_degraded,
    _get_fallback_llm, _is_external_endpoint_url, _invoke_llm_with_fallback,
    _invoke_judge_with_retry, _get_judge_llm, _get_planner_llm,
    _refine_expert_response,
    _ps_cache, _PS_CACHE_TTL, _get_model_node_load, _estimate_model_vram_gb,
    _select_node, _get_expert_score, _record_expert_outcome, _infer_tier,
)
# _dedup_by_category — see parsing.py

from services.kafka import _kafka_publish


async def _kafka_consumer_loop() -> None:
    """
    Persistent consumer for moe.ingest, moe.requests, moe.feedback, and moe.linting.
    Runs as a background task for the entire application lifetime.
    """
    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC_INGEST,
        KAFKA_TOPIC_REQUESTS,
        KAFKA_TOPIC_FEEDBACK,
        KAFKA_TOPIC_LINTING,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id="moe-worker",
        auto_offset_reset="earliest",
        value_deserializer=lambda b: json.loads(b.decode()),
        # Backpressure: cap poll batch size and fetch volume to prevent unbounded
        # memory growth when downstream (Neo4j, LLMs) slows processing.
        max_poll_records=50,
        fetch_max_bytes=5_242_880,  # 5 MB per fetch
    )
    for attempt in range(12):
        try:
            await consumer.start()
            logger.info("✅ Kafka Consumer started")
            break
        except Exception as e:
            wait = 5 * (attempt + 1)
            logger.warning(f"⚠️ Kafka Consumer unreachable (attempt {attempt+1}/12): {e} — retry in {wait}s")
            await asyncio.sleep(wait)
    else:
        logger.error("❌ Kafka Consumer unreachable after 12 attempts — background ingest disabled")
        return

    try:
        async for msg in consumer:
            try:
                if msg.topic == KAFKA_TOPIC_INGEST:
                    payload = msg.value
                    # Determine curator status early — used to gate both ingest and gap detection.
                    # Curator responses are direct Neo4j classifications, not raw knowledge
                    # fragments. Running LLM-based extract_and_ingest on them is redundant
                    # and would route expensive model calls to the global judge endpoint
                    # instead of the curator's designated node.
                    _src_model = str(payload.get("source_model", ""))
                    _tmpl_nm   = str(payload.get("template_name", ""))
                    _is_curator = (
                        "ontology-curator" in _tmpl_nm
                        or "ontology-curator" in _src_model
                        or "ontology_gap_healer" in _src_model
                    )
                    if state.graph_manager is not None:
                        _llm_for_ingest = ingest_llm if ingest_llm is not None else judge_llm
                        _source_expert  = payload.get("source_expert", "")
                        # Persist synthesis insight as a :Synthesis node if present
                        _synthesis = payload.get("synthesis_insight")
                        if _synthesis and isinstance(_synthesis, dict):
                            _syn_domain = payload.get("domain") or "unknown"
                            _syn_src    = payload.get("source_model", "unknown")
                            _syn_conf   = float(payload.get("confidence", 0.5))
                            async def _ingest_synthesis_tracked(
                                _s=_synthesis, _d=_syn_domain, _m=_syn_src, _c=_syn_conf,
                                _ed=_source_expert
                            ):
                                _itype = await state.graph_manager.ingest_synthesis(
                                    _s, domain=_d, source_model=_m, confidence=_c,
                                    expert_domain=_ed
                                )
                                if _itype:
                                    PROM_SYNTHESIS_CREATED.labels(
                                        domain=_d,
                                        insight_type=_itype,
                                    ).inc()
                            asyncio.create_task(_ingest_synthesis_tracked())
                        # Skip LLM-based entity extraction for curator responses:
                        # they write entities directly to Neo4j via ingest_synthesis
                        # and do not require a second extraction pass on the global judge.
                        if not _is_curator:
                            await state.graph_manager.extract_and_ingest(
                                payload.get("input", ""),
                                payload.get("answer", ""),
                                _llm_for_ingest,
                                domain=payload.get("domain"),
                                source_model=payload.get("source_model", "unknown"),
                                confidence=float(payload.get("confidence", 0.5)),
                                knowledge_type=payload.get("knowledge_type", "factual"),
                                expert_domain=_source_expert,
                                tenant_id=payload.get("tenant_id"),
                                redis_client=state.redis_client,
                            )
                    # Detect ontology gaps: terms not present in Neo4j.
                    # Skip when the request came from an ontology curator template —
                    # those responses are classifications of existing gaps, not
                    # sources of new ones. Counting them would produce a self-
                    # replenishing loop (resolve one, add five).
                    if state.redis_client is not None and state.graph_manager is not None and not _is_curator:
                        try:
                            terms = state.graph_manager._extract_terms(payload.get("answer", ""))
                            if terms:
                                existing = await _neo4j_terms_exist(terms)
                                gaps = [t for t in terms if t not in existing]
                                if gaps:
                                    pipe = state.redis_client.pipeline()
                                    for gap in gaps[:5]:
                                        pipe.zincrby("moe:ontology_gaps", 1, gap)
                                    pipe.expire("moe:ontology_gaps", 60 * 60 * 24 * 90)  # 90 Tage
                                    await pipe.execute()
                        except Exception as e:
                            logger.debug(f"Ontology gap detection failed: {e}")
                elif msg.topic == KAFKA_TOPIC_REQUESTS:
                    payload = msg.value
                    logger.debug(
                        f"📬 Request-Log: id={payload.get('response_id','')} "
                        f"cache_hit={payload.get('cache_hit')} "
                        f"experts={payload.get('expert_models_used')}"
                    )
                elif msg.topic == KAFKA_TOPIC_FEEDBACK:
                    payload = msg.value
                    # Positive feedback → save planner pattern
                    if payload.get("positive") and state.redis_client is not None:
                        try:
                            rid = payload.get("response_id", "")
                            meta = await state.redis_client.hgetall(f"moe:response:{rid}")
                            plan_cats = json.loads(meta.get("plan_cats", "[]"))
                            if plan_cats:
                                sig = "+".join(sorted(set(plan_cats)))
                                await state.redis_client.zincrby("moe:planner_success", 1, sig)
                                await state.redis_client.expire("moe:planner_success", 60 * 60 * 24 * 180)  # 180 days
                                logger.debug(f"📈 Planner pattern saved: {sig}")
                        except Exception as e:
                            logger.debug(f"Planner pattern save failed: {e}")
                elif msg.topic == KAFKA_TOPIC_LINTING:
                    if state.graph_manager is not None:
                        _llm_for_lint = ingest_llm if ingest_llm is not None else judge_llm
                        async def _run_linting_tracked(_llm=_llm_for_lint):
                            PROM_LINTING_RUNS.inc()
                            try:
                                _result = await asyncio.wait_for(
                                    state.graph_manager.run_graph_linting(
                                        _llm, kafka_publish_fn=_kafka_publish,
                                    ),
                                    timeout=600,  # 10-minute hard cap — prevents Kafka consumer stall
                                )
                            except asyncio.TimeoutError:
                                logger.warning("⚠️ Graph-Linting timed out after 600s — task cancelled")
                                return
                            PROM_LINTING_ORPHANS.inc(_result.get("orphans_deleted", 0))
                            PROM_LINTING_CONFLICTS.inc(_result.get("conflicts_resolved", 0))
                            PROM_LINTING_DECAY.inc(_result.get("decay_deleted", 0))
                        asyncio.create_task(_run_linting_tracked())
                        logger.info("🧹 Graph-Linting started (background task)")
            except Exception as e:
                logger.warning(f"Kafka Consumer processing error: {e}")
    finally:
        await consumer.stop()
        logger.info("🔌 Kafka Consumer stopped")


# --- SEMANTIC PRE-ROUTING SEEDING ─────────────────────────────────────────────

# Prototype queries per category — stored once at startup in ChromaDB.
# New categories from EXPERT_MODELS are automatically included.
# _ROUTE_PROTOTYPES moved to prompts.py
from prompts import _ROUTE_PROTOTYPES


# LangGraph nodes moved to thematic submodules under graph/
from graph import (
    _seed_task_type_prototypes,
    cache_lookup_node, semantic_router_node,
    _validate_tool_result, mcp_node,
    graph_rag_node, math_node_wrapper,
    _sanitize_plan, _detect_query_temperature,
    planner_node,
    _topological_levels, _inject_prior_results,
    fuzzy_router_node, expert_worker,
    research_node,
    _extract_authoritative_domains, _rerank_graph_context, _compress_graph_context_llm,
    merger_node, research_fallback_node, thinking_node,
    _should_replan, resolve_conflicts_node, critic_node, _route_cache,
)

async def _init_graph_rag() -> None:
    """Initialize the Neo4j GraphRAG Manager with retry logic.

    Skipped immediately when NEO4J_URI or NEO4J_PASS is empty — this is the
    intended state for lightweight deployments that don't include Neo4j.
    """
    # state.graph_manager lives in state.py
    if not NEO4J_URI or not NEO4J_PASS:
        logger.info("ℹ️ Neo4j not configured (NEO4J_URI/NEO4J_PASS empty) — GraphRAG disabled")
        return
    for attempt in range(6):
        try:
            mgr = GraphRAGManager(NEO4J_URI, NEO4J_USER, NEO4J_PASS)
            await mgr.setup()
            graph_manager = mgr
            import state as _state; _state.graph_manager = mgr
            if CORRECTION_MEMORY_ENABLED:
                await _ensure_correction_schema(mgr.driver)
            # Initiale Ontologie-Entity-Anzahl setzen
            try:
                from graph_rag.ontology import _ENTITIES
                PROM_ONTOLOGY_ENTS.set(len(_ENTITIES))
            except Exception:
                pass
            return
        except Exception as e:
            wait = 10 * (attempt + 1)
            logger.warning(f"⚠️ Neo4j unreachable (attempt {attempt+1}/6): {e} — retry in {wait}s")
            await asyncio.sleep(wait)
    logger.error("❌ Neo4j unreachable after 6 attempts — GraphRAG disabled")


async def _load_mcp_tool_descriptions():
    """Loads tool descriptions from the MCP server for the planner prompt.
    Code navigation tools (repo_map, read_file_chunked, lsp_query) are
    stored separately in AGENTIC_CODE_TOOLS_DESCRIPTION and NOT included
    in the global MCP_TOOLS_DESCRIPTION block.

    Also populates MCP_TOOL_SCHEMAS for pre-call argument validation.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{MCP_URL}/tools")
            resp.raise_for_status()
            tools = resp.json().get("tools", [])
            general_lines = []
            agentic_lines = []
            for t in tools:
                line = f"  - {t['name']}: {t['description']}"
                if t['name'] in _AGENTIC_TOOL_NAMES:
                    agentic_lines.append(line)
                else:
                    general_lines.append(line)
                    state._MCP_TOOLS_DICT[t['name']] = t['description']
                # Store schema for pre-call validation
                state.MCP_TOOL_SCHEMAS[t["name"]] = {
                    "required": t.get("required_args", t.get("required", [])),
                    "args": t.get("args", t.get("parameters", {})),
                }
            state.MCP_TOOLS_DESCRIPTION = "\n".join(general_lines)
            state.AGENTIC_CODE_TOOLS_DESCRIPTION = "\n".join(agentic_lines)
            logger.info(
                f"✅ MCP server: {len(tools)} tools loaded ({len(agentic_lines)} code-nav exclusive)"
            )
    except Exception as e:
        logger.warning(f"⚠️ MCP server unreachable ({e}) — planner without tool descriptions")
        state.MCP_TOOLS_DESCRIPTION = (
            "  - calculate: Exact arithmetic and formulas\n"
            "  - solve_equation: Solve algebraic equations\n"
            "  - date_diff: Difference between two dates\n"
            "  - date_add: Date arithmetic\n"
            "  - day_of_week: Day of week for a date\n"
            "  - unit_convert: Unit conversion\n"
            "  - statistics_calc: Statistical metrics\n"
            "  - hash_text: MD5/SHA256/SHA512\n"
            "  - base64_codec: Base64 encode/decode\n"
            "  - regex_extract: Regex pattern matching\n"
            "  - subnet_calc: IP/network calculations\n"
            "  - text_analyze: Text metrics\n"
            "  - prime_factorize: Prime factorization\n"
            "  - gcd_lcm: GCD and LCM\n"
            "  - json_query: JSON path queries\n"
            "  - roman_numeral: Arabic ↔ Roman numerals\n"
            "  - legal_search_laws: Search German federal laws by keyword\n"
            "  - legal_get_law_overview: Table of contents of a German federal law\n"
            "  - legal_get_paragraph: Exact legal text of a section/article (BGB/StGB/GG etc.)\n"
            "  - legal_fulltext_search: Full-text search within a German federal law"
        )
        state.AGENTIC_CODE_TOOLS_DESCRIPTION = (
            "  - repo_map: AST/regex skeleton of a repo (file paths + classes/functions)\n"
            "  - read_file_chunked: Paginated file reading (start_line/end_line, max 200 lines)\n"
            "  - lsp_query: Python LSP features: signature, find_references, completions (.py only)"
        )


async def _init_kafka() -> None:
    """Start the Kafka producer with retry logic."""
    # state.kafka_producer lives in state.py
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: v,  # we already pass bytes
    )
    for attempt in range(12):
        try:
            await producer.start()
            kafka_producer = producer
            import state as _state; _state.kafka_producer = producer
            logger.info(f"✅ Kafka Producer connected ({KAFKA_BOOTSTRAP})")
            return
        except Exception as e:
            wait = 5 * (attempt + 1)
            logger.warning(f"⚠️ Kafka unreachable (attempt {attempt+1}/12): {e} — retry in {wait}s")
            await asyncio.sleep(wait)
    logger.error("❌ Kafka unreachable after 12 attempts — Kafka disabled")


async def _init_enterprise_stack() -> None:
    """Check reachability of optional enterprise data services (NiFi, Marquez, lakeFS).

    Sets _enterprise_reachable=True if INSTALL_ENTERPRISE_DATA_STACK=true AND at least
    one service responds. No-ops silently when the stack is not configured.
    """
    # _enterprise_reachable lives in state.py
    if not _ENTERPRISE_ENABLED:
        return
    checks = [
        ("NiFi",    f"{NIFI_URL}/nifi/"              if NIFI_URL        else None),
        ("Marquez", f"{MARQUEZ_URL}/api/v1/namespaces" if MARQUEZ_URL   else None),
        ("lakeFS",  f"{LAKEFS_ENDPOINT}/api/v1/config" if LAKEFS_ENDPOINT else None),
    ]
    reachable_count = 0
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url in checks:
            if not url:
                continue
            try:
                resp = await client.get(url)
                if resp.status_code < 500:
                    logger.info("✅ Enterprise stack — %s reachable (%s)", name, url)
                    reachable_count += 1
                else:
                    logger.warning("⚠️ Enterprise stack — %s returned %s", name, resp.status_code)
            except Exception as exc:
                logger.warning("⚠️ Enterprise stack — %s unreachable: %s", name, exc)
    state._enterprise_reachable = reachable_count > 0
    if state._enterprise_reachable:
        logger.info("✅ Enterprise Data Stack active (%d/3 services reachable)", reachable_count)
    else:
        logger.warning(
            "⚠️ Enterprise Data Stack configured (INSTALL_ENTERPRISE_DATA_STACK=true) "
            "but no services reachable — routing bypasses enterprise layer"
        )


# Tracks the set of models last seen loaded per Ollama server, so PROM_SERVER_MODEL_VRAM_BYTES
# label series can be removed when a model unloads (otherwise /metrics grows unbounded).
_last_loaded_models: Dict[str, set] = {}


async def _gauge_updater_loop():
    """Periodically update gauge metrics from ChromaDB, Neo4j, Valkey, and inference servers (every 60s)."""
    while True:
        try:
            await asyncio.sleep(60)
            # ChromaDB document count
            try:
                PROM_CHROMA_DOCS.set(cache_collection.count())
            except Exception:
                pass
            # Neo4j: entities, relations, synthesis nodes, flagged relations
            if state.graph_manager is not None:
                try:
                    stats = await state.graph_manager.get_stats()
                    _ents = stats.get("entities", 0)
                    _rels = stats.get("relations", 0)
                    PROM_GRAPH_ENTITIES.set(_ents)
                    PROM_GRAPH_RELATIONS.set(_rels)
                    PROM_SYNTHESIS_NODES.set(stats.get("synthesis_nodes", 0))
                    PROM_FLAGGED_RELS.set(stats.get("flagged_relations", 0))
                    if _ents > 0:
                        PROM_GRAPH_DENSITY.set(round(_rels / _ents, 4))
                except Exception:
                    pass
            # Valkey: planner patterns, ontology gaps, active request count
            if state.redis_client is not None:
                try:
                    PROM_PLANNER_PATS.set(await state.redis_client.zcard("moe:planner_success"))
                    PROM_ONTOLOGY_GAPS.set(await state.redis_client.zcard("moe:ontology_gaps"))
                    active_keys = await state.redis_client.keys("moe:active:*")
                    PROM_ACTIVE_REQUESTS.set(len(active_keys))
                except Exception:
                    pass
            # Inference server health + available model count
            try:
                async with httpx.AsyncClient(timeout=3.0) as _hc:
                    for _srv in INFERENCE_SERVERS_LIST:
                        _sname = _srv["name"]
                        _surl  = _srv.get("url", "").rstrip("/")
                        _atype = _srv.get("api_type", "ollama")
                        try:
                            if _atype == "ollama":
                                # Strip /v1 suffix to reach Ollama native API
                                _base = _surl[:-3] if _surl.endswith("/v1") else _surl
                                _r = await _hc.get(f"{_base}/api/tags")
                                if _r.status_code == 200:
                                    _models = _r.json().get("models", [])
                                    PROM_SERVER_UP.labels(server=_sname).set(1)
                                    PROM_SERVER_MODELS.labels(server=_sname).set(len(_models))
                                else:
                                    PROM_SERVER_UP.labels(server=_sname).set(0)
                                    PROM_SERVER_MODELS.labels(server=_sname).set(0)
                                # Ollama has no native /metrics — scrape /api/ps for loaded-model and VRAM info.
                                # Keep this isolated from the /api/tags result so a /api/ps hiccup
                                # never flips PROM_SERVER_UP.
                                try:
                                    _rps = await _hc.get(f"{_base}/api/ps")
                                    if _rps.status_code == 200:
                                        _loaded = _rps.json().get("models", []) or []
                                        _current_names = set()
                                        _total_vram = 0
                                        for _m in _loaded:
                                            _mname = _m.get("name") or _m.get("model") or ""
                                            if not _mname:
                                                continue
                                            _vram = int(_m.get("size_vram") or 0)
                                            _total_vram += _vram
                                            _current_names.add(_mname)
                                            PROM_SERVER_MODEL_VRAM_BYTES.labels(server=_sname, model=_mname).set(_vram)
                                        PROM_SERVER_LOADED_MODELS.labels(server=_sname).set(len(_current_names))
                                        PROM_SERVER_VRAM_BYTES.labels(server=_sname).set(_total_vram)
                                        # Drop label series for models that unloaded since the previous cycle.
                                        _prev = _last_loaded_models.get(_sname, set())
                                        for _gone in _prev - _current_names:
                                            try:
                                                PROM_SERVER_MODEL_VRAM_BYTES.remove(_sname, _gone)
                                            except KeyError:
                                                pass
                                        _last_loaded_models[_sname] = _current_names
                                        # Model Registry: register warm models in Valkey
                                        # for floating node discovery.
                                        # Skip registration for blocked or floating-disabled servers.
                                        if state.redis_client and _current_names:
                                            try:
                                                _skip = False
                                                _blk = await state.redis_client.sismember("moe:blocked_servers", _sname)
                                                _fld = await state.redis_client.sismember("moe:floating_disabled_servers", _sname)
                                                _skip = bool(_blk or _fld)
                                                if not _skip:
                                                    _now = time.time()
                                                    for _mn in _current_names:
                                                        _reg_key = f"moe:model_registry:{_mn.split(':')[0]}"
                                                        await state.redis_client.zadd(_reg_key, {_sname: _now})
                                                        await state.redis_client.expire(_reg_key, 120)  # 2 min TTL
                                            except Exception:
                                                pass
                                    else:
                                        PROM_SERVER_LOADED_MODELS.labels(server=_sname).set(0)
                                        PROM_SERVER_VRAM_BYTES.labels(server=_sname).set(0)
                                except Exception as _ps_err:
                                    logger.debug(f"/api/ps scrape for {_sname} failed: {_ps_err}")
                                    PROM_SERVER_LOADED_MODELS.labels(server=_sname).set(0)
                                    PROM_SERVER_VRAM_BYTES.labels(server=_sname).set(0)
                            else:
                                _tok = _srv.get("token", "")
                                _hdr = {"Authorization": f"Bearer {_tok}"} if _tok and _tok != "ollama" else {}
                                _r = await _hc.get(f"{_surl}/models", headers=_hdr)
                                if _r.status_code == 200:
                                    _models = _r.json().get("data", [])
                                    PROM_SERVER_UP.labels(server=_sname).set(1)
                                    PROM_SERVER_MODELS.labels(server=_sname).set(len(_models))
                                else:
                                    PROM_SERVER_UP.labels(server=_sname).set(0)
                                    PROM_SERVER_MODELS.labels(server=_sname).set(0)
                        except Exception:
                            PROM_SERVER_UP.labels(server=_sname).set(0)
                            PROM_SERVER_MODELS.labels(server=_sname).set(0)
                            if _atype == "ollama":
                                PROM_SERVER_LOADED_MODELS.labels(server=_sname).set(0)
                                PROM_SERVER_VRAM_BYTES.labels(server=_sname).set(0)
                                for _gone in _last_loaded_models.get(_sname, set()):
                                    try:
                                        PROM_SERVER_MODEL_VRAM_BYTES.remove(_sname, _gone)
                                    except KeyError:
                                        pass
                                _last_loaded_models[_sname] = set()
            except Exception as e:
                logger.debug(f"Gauge updater server health error: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Gauge updater error: {e}")


# ─── LangGraph pipeline definition ─────────────────────────────────────────────
# Topology mirrors commit 4d5b9674 (fuzzy t-norm routing) plus 6da692c7
# (paraconsistent conflict registry). Nodes are imported from the graph/ package
# above; this block wires them into a StateGraph that lifespan() compiles with
# the appropriate checkpointer.
builder = StateGraph(AgentState)
builder.add_node("cache",              cache_lookup_node)
builder.add_node("semantic_router",    semantic_router_node)
builder.add_node("planner",            planner_node)
builder.add_node("fuzzy_router",       fuzzy_router_node)
builder.add_node("workers",            expert_worker)
builder.add_node("research",           research_node)
builder.add_node("math",               math_node_wrapper)
builder.add_node("mcp",                mcp_node)
builder.add_node("graph_rag",          graph_rag_node)
builder.add_node("research_fallback",  research_fallback_node)
builder.add_node("thinking",           thinking_node)
builder.add_node("merger",             merger_node)
builder.add_node("resolve_conflicts",  resolve_conflicts_node)
builder.add_node("critic",             critic_node)

builder.set_entry_point("cache")
builder.add_conditional_edges(
    "cache", _route_cache,
    {"semantic_router": "semantic_router", "merger": "merger"},
)
builder.add_edge("semantic_router", "planner")
builder.add_edge("planner", "fuzzy_router")
builder.add_edge("fuzzy_router", "workers")
builder.add_edge("fuzzy_router", "research")
builder.add_edge("fuzzy_router", "math")
builder.add_edge("fuzzy_router", "mcp")
builder.add_edge("fuzzy_router", "graph_rag")
builder.add_edge(
    ["workers", "research", "math", "mcp", "graph_rag"],
    "research_fallback",
)
builder.add_edge("research_fallback", "thinking")
builder.add_edge("thinking", "merger")
builder.add_conditional_edges(
    "merger", _should_replan,
    {"planner": "planner", "critic": "resolve_conflicts"},
)
builder.add_edge("resolve_conflicts", "critic")
builder.add_edge("critic", END)

app_graph = None


@asynccontextmanager
async def lifespan(app_: FastAPI):
    import state as _state
    global app_graph
    state.redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)  # expose to route modules via state.*
    logger.info("✅ Valkey client initialized")
    # moe_userdb pool: opened lazily, fails open so SQL-less startup is possible for tests
    try:
        state._userdb_pool = AsyncConnectionPool(
            MOE_USERDB_URL,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"autocommit": False},
        )
        await state._userdb_pool.open()
        await state._userdb_pool.wait()
        logger.info("✅ moe_userdb pool verbunden (%s)", MOE_USERDB_URL.split("@")[-1])
    except Exception as e:
        logger.warning("moe_userdb pool nicht verbunden: %s", e)
        state._userdb_pool = None
    # admin_ui.database has its own pool that backs sync_user_to_redis (used by
    # _db_fallback_key_lookup when a user's API key hash is not yet in Valkey).
    # Without this, the fallback silently errors and new or cache-evicted keys
    # return 401 — e.g. Open-WebUI loses access to /v1/models and expert templates.
    try:
        from admin_ui.database import init_db as _admin_init_db
        await _admin_init_db()
        logger.info("✅ admin_ui.database pool initialized (for API key fallback sync)")
    except Exception as e:
        logger.warning("admin_ui.database pool not initialized: %s", e)
    # Clean up orphaned active-request keys from the previous process
    _stale_keys = await state.redis_client.keys("moe:active:*")
    if _stale_keys:
        await state.redis_client.delete(*_stale_keys)
        logger.info(f"🧹 {len(_stale_keys)} orphaned moe:active:* keys deleted on startup")
    # Initialize semaphores in event loop context
    await _init_semaphores()
    # Ensure skill registry schema and populate from filesystem
    await _ensure_skill_registry_schema()
    await _bootstrap_skill_registry()
    # Ensure causal-path columns exist in routing_telemetry
    await _telemetry.ensure_causal_columns(state._userdb_pool)
    # Start init tasks in parallel
    await asyncio.gather(
        _load_mcp_tool_descriptions(),
        _init_graph_rag(),
        _init_kafka(),
        _seed_task_type_prototypes(),
        _init_enterprise_stack(),
    )
    # Kafka Consumer as persistent background task
    consumer_task  = asyncio.create_task(_kafka_consumer_loop())
    gauge_task     = asyncio.create_task(_gauge_updater_loop())
    asyncio.create_task(_auto_resume_dedicated_healer())
    asyncio.create_task(_watchdog_dedicated_healer())
    # Starfleet: proactive watchdog alert loop
    if _starfleet.is_feature_enabled_sync("watchdog"):
        asyncio.create_task(_watchdog.watchdog_loop(
            redis_client=state.redis_client,
            inference_servers=INFERENCE_SERVERS_LIST,
            kafka_producer=state.kafka_producer,
            prom_server_up=PROM_SERVER_UP,
            prom_loaded_models=PROM_SERVER_LOADED_MODELS,
            prom_vram_bytes=PROM_SERVER_VRAM_BYTES,
        ))
        logger.info("🛸 Starfleet Watchdog enabled")
    # Semantic memory TTL cleanup — runs every 6 hours, removes expired ChromaDB turns
    async def _semantic_memory_cleanup_loop() -> None:
        from memory_retrieval import cleanup_expired_turns
        while True:
            await asyncio.sleep(6 * 3600)
            deleted = await cleanup_expired_turns(max_delete=1000)
            if deleted:
                logger.info(f"🧹 Semantic memory: {deleted} expired turns removed from ChromaDB")
    asyncio.create_task(_semantic_memory_cleanup_loop())
    if _EDGE_MODE:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
        app_graph = builder.compile(checkpointer=checkpointer)
        logger.info("✅ MemorySaver initialized (edge_mobile — in-RAM checkpoints)")
        yield
    else:
        async with AsyncPostgresSaver.from_conn_string(POSTGRES_CHECKPOINT_URL) as checkpointer:
            await checkpointer.setup()
            app_graph = builder.compile(checkpointer=checkpointer)
            logger.info("✅ PostgresSaver initialized — checkpoints persisted on terra_checkpoints")
            yield
    # Cleanup
    gauge_task.cancel()
    consumer_task.cancel()
    if state.kafka_producer is not None:
        await state.kafka_producer.stop()
        logger.info("🔌 Kafka Producer stopped")
    if state.graph_manager is not None:
        await state.graph_manager.close()
        logger.info("🔌 Neo4j connection closed")
    if state.redis_client is not None:
        await state.redis_client.aclose()
        logger.info("🔌 Valkey client closed")
    if state._userdb_pool is not None:
        await state._userdb_pool.close()
        logger.info("🔌 moe_userdb pool closed")

app = FastAPI(lifespan=lifespan)

# ── APIRouter modules (extracted from main.py) ────────────────────────────────
from routes.health           import router as _health_router
from routes.watchdog         import router as _watchdog_router
from routes.mission_context  import router as _mc_router
from routes.graph            import router as _graph_router
from routes.admin_benchmark  import router as _admin_bench_router
from routes.admin_ontology   import router as _admin_onto_router
from routes.admin_stats      import router as _admin_stats_router
from routes.feedback         import router as _feedback_router
from routes.ollama_compat    import router as _ollama_router
from routes.models           import router as _models_router
from routes.anthropic_compat import router as _anthropic_router
app.include_router(_health_router)
app.include_router(_watchdog_router)
app.include_router(_mc_router)
app.include_router(_graph_router)
app.include_router(_admin_bench_router)
app.include_router(_admin_onto_router)
app.include_router(_admin_stats_router)
app.include_router(_feedback_router)
app.include_router(_ollama_router)
app.include_router(_models_router)
app.include_router(_anthropic_router)

# ── Security Headers Middleware ────────────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware

class _SecurityHeadersMiddleware(_BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]    = "nosniff"
        response.headers["X-Frame-Options"]           = "DENY"
        response.headers["X-XSS-Protection"]          = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]        = "geolocation=(), camera=(), microphone=()"
        # HSTS only when behind TLS (Nginx sets this on the public endpoint)
        return response

app.add_middleware(_SecurityHeadersMiddleware)

# ── Request Body Size Limit — protect against payload-based DoS ───────────────
from starlette.middleware.base import BaseHTTPMiddleware as _BM2
_MAX_BODY_BYTES = MAX_REQUEST_BODY_MB * 1024 * 1024  # MAX_REQUEST_BODY_MB from config.py

class _BodySizeLimitMiddleware(_BM2):
    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": f"Request body exceeds {_MAX_BODY_BYTES // (1024*1024)} MB limit"},
            )
        return await call_next(request)

app.add_middleware(_BodySizeLimitMiddleware)

# CORS for Open WebUI direct connections (browser-side)
from fastapi.middleware.cors import CORSMiddleware
_cors_all     = CORS_ALL_ORIGINS   # from config.py
_cors_origins = ["*"] if _cors_all else [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=not _cors_all,  # credentials + wildcard is forbidden per CORS spec
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "x-api-key", "Content-Type"],
    )

from config import _CLAUDE_PRETTY_NAMES, _model_display_name


async def _stream_native_llm(
    request: "ChatCompletionRequest",
    chat_id: str,
    endpoint: dict,   # {url, token, model}
    user_id: str,
    model_name: str,
    session_id: str = None,
    is_user_conn: bool = False,
):
    """Direct proxy: forward request directly to inference endpoint, no MoE pipeline."""
    url     = endpoint["url"].rstrip("/") + "/chat/completions"
    token   = endpoint["token"]
    created = int(time.time())
    payload: dict = {
        "model":          endpoint["model"],
        "messages":       [{"role": m.role, "content": m.content if m.content is not None else ""} for m in request.messages],
        "stream":         True,
        "stream_options": {"include_usage": True},
    }
    if request.max_tokens:                    payload["max_tokens"]  = request.max_tokens
    if request.temperature is not None:       payload["temperature"] = request.temperature

    _t_start = time.monotonic()
    _t_first: Optional[float] = None
    p_tok = c_tok = 0
    _did_deregister = False

    try:
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST", url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                            if chunk.get("usage"):
                                p_tok = chunk["usage"].get("prompt_tokens", p_tok)
                                c_tok = chunk["usage"].get("completion_tokens", c_tok)
                            chunk["id"] = chat_id
                            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                            if delta.get("content") and _t_first is None:
                                _t_first = time.monotonic()
                            yield f"data: {json.dumps(chunk)}\n\n"
                        except Exception:
                            continue
        except Exception as _e:
            logger.warning(f"Native LLM proxy error: {_e}")
            yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': endpoint['model'], 'choices': [{'index': 0, 'delta': {'content': f'[Error: {_e}]'}, 'finish_reason': 'stop'}]})}\n\n"

        # Usage + token speed (extended Ollama/Open-WebUI format)
        _t_end        = time.monotonic()
        total_dur_ns  = int((_t_end - _t_start) * 1e9)
        load_dur_ns   = int(((_t_first or _t_end) - _t_start) * 1e9)
        eval_dur_ns   = int((_t_end - (_t_first or _t_end)) * 1e9) if _t_first else 0
        p_eval_dur_ns = load_dur_ns  # approximation: prompt processing ends at first token
        elapsed_gen   = eval_dur_ns / 1e9 if eval_dur_ns > 0 else ((_t_end - _t_start))
        elapsed_prompt = p_eval_dur_ns / 1e9 if p_eval_dur_ns > 0 else 1e-9
        tps           = round(c_tok / elapsed_gen, 2) if elapsed_gen > 0 and c_tok > 0 else 0
        prompt_tps    = round(p_tok / elapsed_prompt, 2) if p_tok > 0 else 0
        total_s       = _t_end - _t_start
        approx_total  = f"0h{int(total_s // 3600)}m{int((total_s % 3600) // 60)}m{int(total_s % 60)}s"
        if user_id != "anon":
            asyncio.create_task(_log_usage_to_db(
                user_id=user_id, api_key_id="", request_id=chat_id,
                model=model_name, moe_mode="native",
                prompt_tokens=p_tok, completion_tokens=c_tok, session_id=session_id,
            ))
            # User-owned connections are billed by the user's own provider — exclude from MoE budget.
            if not is_user_conn:
                asyncio.create_task(_increment_user_budget(user_id, p_tok + c_tok, prompt_tokens=p_tok, completion_tokens=c_tok))
        asyncio.create_task(_deregister_active_request(chat_id))
        _did_deregister = True
        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': endpoint['model'], 'choices': [], 'usage': {'prompt_tokens': p_tok, 'completion_tokens': c_tok, 'total_tokens': p_tok + c_tok, 'tokens_per_second': tps, 'response_token_per_s': tps, 'prompt_token_per_s': prompt_tps, 'total_duration': total_dur_ns, 'load_duration': load_dur_ns, 'prompt_eval_count': p_tok, 'prompt_eval_duration': p_eval_dur_ns, 'eval_count': c_tok, 'eval_duration': eval_dur_ns, 'approximate_total': approx_total}})}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        if not _did_deregister:
            asyncio.create_task(_deregister_active_request(chat_id))


async def stream_response(user_input: str, chat_id: str, mode: str = "default",
                          chat_history: Optional[List[Dict]] = None,
                          system_prompt: str = "", user_id: str = "anon",
                          api_key_id: str = "",
                          user_permissions: Optional[dict] = None,
                          user_experts: Optional[dict] = None,
                          planner_prompt: str = "",
                          judge_prompt: str = "",
                          judge_model_override: str = "",
                          judge_url_override: str = "",
                          judge_token_override: str = "",
                          planner_model_override: str = "",
                          planner_url_override: str = "",
                          planner_token_override: str = "",
                          model_name: str = "",
                          pending_reports: Optional[List[str]] = None,
                          images: Optional[List[Dict]] = None,
                          session_id: str = None,
                          max_agentic_rounds: int = 0,
                          no_cache: bool = False):
    _deregistered = False
    config   = {"configurable": {"thread_id": str(uuid.uuid4())}}
    created  = int(time.time())
    _t_start = time.monotonic()

    model_id = MODES.get(mode, MODES["default"])["model_id"]
    def _chunk(delta: dict, finish_reason=None, usage: Optional[dict] = None) -> str:
        payload = {
            "id": chat_id, "object": "chat.completion.chunk", "created": created,
            "model": model_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage:
            payload["usage"] = usage
        return f"data: {json.dumps(payload)}\n\n"

    # Send role chunk immediately — Open WebUI shows spinner and keeps connection open
    yield _chunk({"role": "assistant", "content": ""})

    # Set up progress queue for <think> block (inherited into all nodes via ContextVar)
    progress_q: asyncio.Queue = asyncio.Queue()
    ctx_token = _progress_queue.set(progress_q)

    result_box: dict = {}

    skip_think = MODES.get(mode, MODES["default"]).get("skip_think", False)

    async def _run_pipeline() -> None:
        try:
            result_box["data"] = await app_graph.ainvoke(
                {"input": user_input, "response_id": chat_id, "mode": mode,
                 "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
                 "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
                 "chat_history": chat_history or [], "reasoning_trace": "",
                 "system_prompt": system_prompt, "images": images or [],
                 "user_id": user_id, "api_key_id": api_key_id,
                 "user_permissions": user_permissions or {},
                 "user_experts": user_experts or {},
                 "planner_prompt": planner_prompt or "",
                 "judge_prompt":   judge_prompt or "",
                 "judge_model_override":   judge_model_override or "",
                 "judge_url_override":     judge_url_override or "",
                 "judge_token_override":   judge_token_override or "",
                 "planner_model_override": planner_model_override or "",
                 "planner_url_override":   planner_url_override or "",
                 "planner_token_override": planner_token_override or "",
                 "template_name":          model_name or "",
                 "pending_reports": pending_reports or [],
                 "max_agentic_rounds": max_agentic_rounds,
                 "agentic_iteration": 0,
                 "agentic_history": [],
                 "agentic_gap": "",
                 "attempted_queries": [],
                 "search_strategy_hint": "",
                 "conflict_registry": [],
                 "vector_confidence": 0.5,
                 "graph_confidence": 0.5,
                 "fuzzy_routing_scores": {},
                 "no_cache": no_cache},
                config,
            )
        except Exception as e:
            result_box["error"] = e
        finally:
            await progress_q.put(None)  # Sentinel: Pipeline fertig

    asyncio.create_task(_run_pipeline())

    if not skip_think:
        # Open think block — Open WebUI shows thinking panel
        yield _chunk({"content": "<think>\n"})
        # Stream progress messages until sentinel (None) received
        while True:
            try:
                msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                if msg is None:
                    break
                yield _chunk({"content": msg + "\n"})
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"   # SSE comment — keeps proxies open
        yield _chunk({"content": "</think>\n\n"})
    else:
        # Agent mode: drain progress queue without sending — coding agents do not process <think>
        while True:
            try:
                msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                if msg is None:
                    break
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    _progress_queue.reset(ctx_token)

    # Pipeline complete — deregister now, before streaming content.
    # This prevents the request from remaining "active" if the client closes the connection
    # after the last content chunk (normal OpenWebUI behavior).
    asyncio.create_task(_deregister_active_request(chat_id))
    _deregistered = True

    # Plan mode: output execution plan as visible markdown block before the answer
    if mode == "plan" and "data" in result_box:
        plan_tasks = result_box["data"].get("plan", [])
        if plan_tasks:
            lines = ["## Execution Plan\n"]
            for i, task in enumerate(plan_tasks, 1):
                cat  = task.get("category", "?")
                desc = task.get("task", "")[:80]
                sq   = task.get("search_query", "")
                line = f"{i}. **[{cat}]** {desc}"
                if sq:
                    line += f" *(Search: `{sq[:50]}`)*"
                lines.append(line)
            plan_text = "\n".join(lines) + "\n\n---\n\n"
            for i in range(0, len(plan_text), SSE_CHUNK_SIZE):
                yield _chunk({"content": plan_text[i:i + SSE_CHUNK_SIZE]})
                await asyncio.sleep(0.01)

    _t_first_token: Optional[float] = None
    if "error" in result_box:
        yield _chunk({"content": f"Error: {result_box['error']}"})
    else:
        content = result_box.get("data", {}).get("final_response") or ""
        for i in range(0, len(content), SSE_CHUNK_SIZE):
            if _t_first_token is None:
                _t_first_token = time.monotonic()
            yield _chunk({"content": content[i:i + SSE_CHUNK_SIZE]})
            await asyncio.sleep(0.01)

    # Finalize: stop chunk + separate usage chunk (OpenAI spec: choices=[] for usage)
    data  = result_box.get("data", {})
    p_tok = data.get("prompt_tokens",     0)
    c_tok = data.get("completion_tokens", 0)
    _uid  = data.get("user_id") or "anon"   # Guard: None → "anon"
    try:
        _t_end_moe    = time.monotonic()
        total_dur_ns  = int((_t_end_moe - _t_start) * 1e9)
        load_dur_ns   = int(((_t_first_token or _t_end_moe) - _t_start) * 1e9)
        eval_dur_ns   = int((_t_end_moe - (_t_first_token or _t_end_moe)) * 1e9) if _t_first_token else 0
        p_eval_dur_ns = load_dur_ns
        elapsed_gen   = eval_dur_ns / 1e9 if eval_dur_ns > 0 else (_t_end_moe - _t_start)
        elapsed_prompt = p_eval_dur_ns / 1e9 if p_eval_dur_ns > 0 else 1e-9
        tps           = round(c_tok / elapsed_gen, 2) if elapsed_gen > 0 and c_tok > 0 else 0
        prompt_tps    = round(p_tok / elapsed_prompt, 2) if p_tok > 0 else 0
        total_s       = _t_end_moe - _t_start
        approx_total  = f"0h{int(total_s // 3600)}m{int((total_s % 3600) // 60)}m{int(total_s % 60)}s"
        # Prometheus: record request + duration
        cache_hit_flag = data.get("cache_hit", False)
        PROM_REQUESTS.labels(mode=mode, cache_hit=str(cache_hit_flag).lower(), user_id=_uid).inc()
        PROM_RESPONSE_TIME.labels(mode=mode).observe(time.monotonic() - _t_start)
        # Usage-Tracking in SQLite + Valkey (fire-and-forget)
        # Extract pipeline routing context for transparency log
        _plan = data.get("plan") or []
        _expert_domains = ",".join(sorted({
            t.get("category", "") for t in _plan if isinstance(t, dict) and t.get("category")
        }))
        _agentic_rounds = int(data.get("agentic_round", 0))
        if _uid != "anon":
            asyncio.create_task(_log_usage_to_db(
                user_id=_uid,
                api_key_id=data.get("api_key_id", ""),
                request_id=chat_id,
                model=model_name or model_id,
                moe_mode=mode,
                prompt_tokens=p_tok,
                completion_tokens=c_tok,
                session_id=session_id,
                latency_ms=int(total_s * 1000),
                complexity_level=data.get("complexity_level", ""),
                expert_domains=_expert_domains,
                cache_hit=bool(cache_hit_flag),
                agentic_rounds=_agentic_rounds,
            ))
            # Deduct user-conn tokens: those are billed by the user's own provider.
            _uc_p = data.get("user_conn_prompt_tokens", 0)
            _uc_c = data.get("user_conn_completion_tokens", 0)
            _bill_p = max(0, p_tok - _uc_p)
            _bill_c = max(0, c_tok - _uc_c)
            asyncio.create_task(_increment_user_budget(_uid, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))
        if not _deregistered:
            asyncio.create_task(_deregister_active_request(chat_id))
        # Stop chunk WITHOUT usage (OpenAI spec: usage comes in its own chunk after)
        yield _chunk({}, finish_reason="stop")
        # Separate usage chunk with choices=[] — recognized by Open-WebUI as statistics
        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [], 'usage': {'prompt_tokens': p_tok, 'completion_tokens': c_tok, 'total_tokens': p_tok + c_tok, 'tokens_per_second': tps, 'response_token_per_s': tps, 'prompt_token_per_s': prompt_tps, 'total_duration': total_dur_ns, 'load_duration': load_dur_ns, 'prompt_eval_count': p_tok, 'prompt_eval_duration': p_eval_dur_ns, 'eval_count': c_tok, 'eval_duration': eval_dur_ns, 'approximate_total': approx_total}})}\n\n"
    except Exception as _e:
        logger.warning(f"Stream finalization failed: {_e}")
    finally:
        if not _deregistered:
            asyncio.create_task(_deregister_active_request(chat_id))
        yield "data: [DONE]\n\n"

def _is_openwebui_internal(messages: List[Message]) -> bool:
    """Erkennt interne Open WebUI Prompts (Follow-up, Autocomplete, Title Generation)."""
    if not messages:
        return False
    last = _oai_content_to_str(messages[-1].content)
    internal_markers = (
        "### Task:\nSuggest 3-5 relevant follow-up questions",
        "### Task:\nGenerate a concise, 3-5 word title",
        "### Task:\nGenerate 1-3 broad tags",
        "### Task:\nBased on the chat history below",
        "Generate a concise, 3-5 word title",
    )
    return any(marker in last for marker in internal_markers)


async def _handle_internal_direct(messages: List[Message], chat_id: str, stream: bool):
    """Route internal Open WebUI requests directly to Judge LLM — no MoE pipeline."""
    logger.info(f"⚡ Open WebUI internal request detected — direct to Judge LLM (no pipeline)")
    lc_messages = [{"role": m.role, "content": m.content} for m in messages]
    res = await judge_llm.ainvoke(lc_messages)
    content = res.content
    created = int(time.time())
    u = _extract_usage(res)
    usage = {"prompt_tokens": u["prompt_tokens"], "completion_tokens": u["completion_tokens"],
             "total_tokens": u["prompt_tokens"] + u["completion_tokens"]}

    if not stream:
        return {
            "id": chat_id, "object": "chat.completion", "created": created,
            "model": "moe-orchestrator",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": usage,
        }

    async def _stream():
        def _chunk(delta, finish_reason=None, u=None):
            p = {"id": chat_id, "object": "chat.completion.chunk", "created": created,
                 "model": "moe-orchestrator",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
            if u:
                p["usage"] = u
            return f"data: {json.dumps(p)}\n\n"
        yield _chunk({"role": "assistant", "content": ""})
        for i in range(0, len(content), SSE_CHUNK_SIZE):
            yield _chunk({"content": content[i:i + SSE_CHUNK_SIZE]})
            await asyncio.sleep(0.005)
        yield _chunk({}, finish_reason="stop", u=usage)
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")



# Pipeline handlers moved to services/pipeline.py
# chat_completions, _anthropic_tool_handler, _anthropic_moe_handler,
# _anthropic_reasoning_handler, _ollama_internal_stream,
# _ResponsesRequest, responses_api, _invoke_pipeline_for_responses,
# _stream_responses_api
from services.pipeline import (
    chat_completions, anthropic_messages,
    _anthropic_tool_handler, _anthropic_moe_handler,
    _anthropic_reasoning_handler, _ollama_internal_stream,
    ChatCompletionRequest, _ResponsesRequest, responses_api,
    _invoke_pipeline_for_responses, _stream_responses_api,
)

# Lifespan-task healers (background loops kicked off in lifespan())
from services.healer import (
    _auto_resume_dedicated_healer, _watchdog_dedicated_healer,
)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=600,      # 10 min — prevents proxy/load-balancer from dropping long-running non-streaming requests
        timeout_graceful_shutdown=60,
    )

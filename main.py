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

# --- TOOL-EVAL LOGGING (rotating JSONL, one record per tool handler call) ---
import logging.handlers as _log_handlers
os.makedirs("/app/logs", exist_ok=True)
_tool_eval_logger = logging.getLogger("tool-eval")
_tool_eval_logger.setLevel(logging.INFO)
_tool_eval_logger.propagate = False
_teh = _log_handlers.RotatingFileHandler(
    "/app/logs/tool_eval.jsonl", maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_teh.setFormatter(logging.Formatter("%(message)s"))
_tool_eval_logger.addHandler(_teh)

def _log_tool_eval(record: dict) -> None:
    """Append a structured JSON record to tool_eval.jsonl. Never raises."""
    try:
        _tool_eval_logger.info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception:
        pass

# Kafka constants imported from config.py

# Instruction appended to the merger_node system prompt to trigger synthesis persistence.
# The LLM is asked to append a tagged JSON block only for genuinely novel insights.
SYNTHESIS_PERSISTENCE_INSTRUCTION = (
    "\n\nSYNTHESIS PERSISTENCE: If your response contains a novel multi-source comparison, "
    "logical inference, or non-trivial synthesis (not a simple factual lookup), append "
    "exactly ONE block at the very end of your response:\n"
    "<SYNTHESIS_INSIGHT>\n"
    '{"summary": "<one concise sentence>", '
    '"entities": ["entity1", "entity2"], '
    '"insight_type": "comparison|synthesis|inference"}\n'
    "</SYNTHESIS_INSIGHT>\n"
    "Omit this block entirely for direct factual answers or simple retrievals."
)

# Instruction appended to merger prompt to trigger inline source attribution.
# Tags factual claims derived from the knowledge graph for provenance tracking.
PROVENANCE_INSTRUCTION = (
    "\n\nSOURCE ATTRIBUTION: When your answer includes a factual claim that comes "
    "directly from the Knowledge Graph section above, mark it with [REF:entity_name] "
    "immediately after the claim. Use the exact entity name from the graph context. "
    "Only tag claims derived from the graph — do not tag general knowledge or web results. "
    "Keep tags minimal (max 5 per response)."
)

# DB, enterprise, OIDC constants imported from config.py
# Mutable globals (redis_client, _userdb_pool, etc.) live in state.py
_enterprise_reachable: bool = False  # set by lifespan via _init_enterprise_stack()
_userdb_pool: Optional[AsyncConnectionPool] = None  # set by lifespan

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
_shadow_lock = threading.Lock()  # guards _shadow_request_counter increment
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
_shadow_request_counter: int   = 0

# ─── Default role instruction for the Planner LLM ─────────────────────────────
# Can be overridden per Expert Template via planner_prompt field.
DEFAULT_PLANNER_ROLE = (
    "You are the orchestrator of a Mixture-of-Experts system.\n"
    "Decompose the following request into 1–4 subtasks.\n\n"
    "Mandatorily extract all numerical constraints and technical parameters from the request "
    "(e.g. model sizes, MTU values, protocol overheads, chemical doses, bitrates). "
    "Integrate these as IMMUTABLE_CONSTANTS directly into each subtask description for the experts, "
    "so experts cannot hallucinate default values."
)


# _resolve_user_experts, _resolve_template_prompts moved to services/routing.py
from services.routing import _resolve_user_experts, _resolve_template_prompts
from services.tracking import (
    _log_usage_to_db, _register_active_request,
    _deregister_active_request, _increment_user_budget,
)


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


def _update_rate_limit_headers(endpoint: str, headers, status_code: int = 200) -> None:
    """Parse OpenAI-style x-ratelimit-* headers and cache rate limit state per endpoint."""
    import time as _time, re as _re
    now = _time.time()
    entry = _provider_rate_limits.get(endpoint, {})
    remaining_raw = headers.get("x-ratelimit-remaining-tokens")
    limit_raw     = headers.get("x-ratelimit-limit-tokens")
    reset_raw     = headers.get("x-ratelimit-reset-tokens")
    if remaining_raw is not None:
        try: entry["remaining_tokens"] = int(float(remaining_raw))
        except (ValueError, TypeError): pass
    if limit_raw is not None:
        try: entry["limit_tokens"] = int(float(limit_raw))
        except (ValueError, TypeError): pass
    if reset_raw is not None:
        try:
            m = _re.match(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$', reset_raw)
            if m:
                d, h, mi, s = (float(x or 0) for x in m.groups())
                entry["reset_time"] = now + d*86400 + h*3600 + mi*60 + s
            else:
                ts = float(reset_raw)
                entry["reset_time"] = ts if ts > 1e9 else now + ts
        except (ValueError, TypeError): pass
    if status_code == 429:
        entry["exhausted"] = True
        if "reset_time" not in entry:
            entry["reset_time"] = now + 60  # default: retry after 60s
    else:
        if entry.get("remaining_tokens", 1) > 0:
            entry["exhausted"] = False
    entry["updated_at"] = now
    _provider_rate_limits[endpoint] = entry


def _check_rate_limit_exhausted(endpoint: str) -> bool:
    """Return True if endpoint is known rate-limited and reset time has not yet passed."""
    import time as _time
    entry = _provider_rate_limits.get(endpoint)
    if not entry or not entry.get("exhausted"):
        return False
    return _time.time() < entry.get("reset_time", 0)


# MCP tool descriptions for the planner (loaded at startup)
MCP_TOOLS_DESCRIPTION = ""
# Per-tool description dict for domain-filtered planner injection: {name: description}
_MCP_TOOLS_DICT: dict[str, str] = {}
# MCP tool schemas for pre-call arg validation: {tool_name: {required: [...], args: {...}}}
MCP_TOOL_SCHEMAS: dict = {}

# Code-navigation tools — not included in MCP_TOOLS_DESCRIPTION,
# only injected into the planner prompt when agentic_coder is active.
_AGENTIC_TOOL_NAMES = {"repo_map", "read_file_chunked", "lsp_query"}
AGENTIC_CODE_TOOLS_DESCRIPTION = ""

# ── Tool groups for domain-filtered planner injection ────────────────────────
# Core: always shown — fundamental precision + web access
_TOOL_GROUP_CORE = frozenset({
    # Web research — always available; these are the backbone of every research task
    "web_researcher", "web_search_domain", "fetch_pdf_text",
    "wikipedia_get_section",
    # Deterministic entity/fact lookup — prefer over web search for structured data
    "wikidata_search", "wikidata_sparql",
    # JS-capable browser + alternative search — generic enough for any research query
    "web_browser", "duckduckgo_search",
    # Math / utility — domain-agnostic
    "calculate", "python_sandbox",
    "date_diff", "date_add", "unit_convert",
})
# Research: shown when query contains paper/author/database/species/media markers
_TOOL_GROUP_RESEARCH = frozenset({
    "semantic_scholar_search", "pubmed_search",
    "crossref_lookup", "openalex_search",   # academic publication databases
    "orcid_works_count",
    "wayback_fetch",                         # historical snapshots (ORCID, archived pages)
    "pubchem_compound_search", "pubchem_advanced_search",
    "github_search_issues", "github_issue_events",
    "youtube_transcript",
    "chess_analyze_position", "chess_legal_moves",  # chess position analysis via Lichess
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

_RESEARCH_DETECT = re.compile(
    r'\b(paper|article|study|studies|journal|published|author|researcher|professor|'
    r'arxiv|doi|isbn|pubchem|orcid|database|dataset|classification|compound|'
    r'species|genus|wikipedia|museum|collection|archive|standard|transcript|'
    r'video|episode|season|channel|github|issue|repo)\b', re.I,
)
_LEGAL_DETECT = re.compile(
    r'\b(§+\s*\d+|bgh|bverfg|bfh|bsg|bgh|hgb|bdb|stvo|dsgvo|gdpr|'
    r'vertrag|gesetz|recht|klage|straf|gmbh|ag\b|ug\b|insolvenz|'
    r'law|legal|statute|regulation|compliance|contract|court)\b', re.I,
)
_DATA_DETECT = re.compile(
    r'\b(berechne?|calculate|compute|average|median|stdev|hash|base64|'
    r'regex|cidr|subnet|subnet|ip.address|convert|unit|statistic|prozent|percent)\b', re.I,
)
_FILE_DETECT = re.compile(
    r'\b(attachment|datei|file|upload|image|foto|bild|pdf|spreadsheet|csv|graph|ontology)\b', re.I,
)


def _build_filtered_tool_desc(query: str, enable_graphrag: bool = False) -> str:
    """Return MCP tool description block filtered to the query's domain.

    Always includes CORE tools.  Adds RESEARCH, DATA, LEGAL, FILE groups
    when the query contains matching markers.  Falls back to the global
    MCP_TOOLS_DESCRIPTION if the per-tool dict is not yet populated.
    """
    if not _MCP_TOOLS_DICT:
        return MCP_TOOLS_DESCRIPTION

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
        for name, desc in _MCP_TOOLS_DICT.items()
        if name in active
    ]
    return "\n".join(lines) if lines else MCP_TOOLS_DESCRIPTION

# Categories handled by specialized nodes (not by expert LLMs)
NON_EXPERT_CATEGORIES = {"precision_tools", "research"}

# Routing thresholds, timeouts, feedback thresholds imported from config.py

# Confidence format instruction — mode-dependent (3A)
_CONF_FORMAT_DEFAULT = (
    "\n\nAlways structure your answer EXACTLY in this format:\n"
    "CORE_FINDING: [1-2 sentence main statement]\n"
    "CONFIDENCE: high | medium | low\n"
    "  high = established expert knowledge, clear source situation\n"
    "  medium = domain knowledge available, exceptions or nuances possible\n"
    "  low = data gaps, outdated knowledge, genuine uncertainty\n"
    "GAPS: [open sub-questions for other experts | none]\n"
    "REFERRAL: [expert category if handoff needed | —]\n"
    "DETAILS:\n"
    "[full answer here]"
)
_CONF_FORMAT_CODE = (
    "\n\nInsert a comment as the very first line:\n"
    "# CONFIDENCE: high | medium | low\n"
    "Then ONLY source code."
)
_CONF_FORMAT_CONCISE = (
    "\n\nBegin with: CONFIDENCE: high | medium | low — then your brief answer."
)

def _conf_format_for_mode(mode: str) -> str:
    if mode == "code":    return _CONF_FORMAT_CODE
    if mode == "concise": return _CONF_FORMAT_CONCISE
    if mode in ("agent", "agent_orchestrated"):  return ""   # No CONFIDENCE block — coding agents need clean output
    if mode == "research": return ""   # Research uses its own report structure
    return _CONF_FORMAT_DEFAULT

# Categories where low confidence triggers a web fallback (3C)
_SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}

# System prompts per expert role — define identity and behavior
DEFAULT_EXPERT_PROMPTS: Dict[str, str] = {
    "memory_recall": (
        "You are a conversation memory expert. Your ONLY source of information is the "
        "conversation history provided in this context — not the internet, not training data.\n"
        "Rules:\n"
        "1. Read ALL previous turns carefully and completely before answering.\n"
        "2. For multi-part questions (e.g. 'What is X AND what is Y?'): answer EVERY "
        "part explicitly. Use a structured list if there are 3+ items. Include exact "
        "values (numbers, names, versions) as stated — never paraphrase or round.\n"
        "   IMPORTANT: facts about the same entity may appear in DIFFERENT user turns. "
        "Scan ALL turns to find each requested fact — do not stop after the first match.\n"
        "3. When a fact was corrected or updated — whether explicit ('Korrektur:', "
        "'Correction:', 'Update:') or implicit ('wurde auf X erhöht', 'ist jetzt X', "
        "'wurde geändert auf', 'was changed to', 'is now', 'has been updated to') — "
        "use ONLY the most recent value. Later statements always supersede earlier ones.\n"
        "4. If a specific fact was NOT mentioned in this conversation, say exactly: "
        "'Diese Information wurde in unserem Gespräch nicht erwähnt.' "
        "(or in English: 'This information was not mentioned in our conversation.')\n"
        "5. Never guess, infer, or use external knowledge. Only recall from conversation.\n"
        "6. Be complete: a partial answer that omits requested facts is incorrect."
    ),
    "general": (
        "You are a versatile, fact-based expert. "
        "Answer precisely, in a structured manner. "
        "Stick to verifiable facts."
    ),
    "math": (
        "You are a mathematics and physics expert. "
        "Always show the complete solution steps. "
        "Use LaTeX notation for formulas. "
        "Verify your result by back-substitution."
    ),
    "technical_support": (
        "You are an experienced IT engineer and DevOps specialist. "
        "Answer with concrete, executable solution steps. "
        "Name relevant commands, configurations and error codes."
    ),
    "creative_writer": (
        "You are a creative author and copywriter. "
        "Write vividly, originally and with stylistic confidence. "
        "Adapt tone and register to the context."
    ),
    "code_reviewer": (
        "You are a senior software engineer focused on code quality and security. "
        "Identify bugs, security vulnerabilities, performance issues and improvement potential. "
        "Return concrete, improved code and explain why."
    ),
    "medical_consult": (
        "You are an experienced physician. "
        "Provide well-founded, objective medical information based on current guidelines. "
        "Always clearly emphasize that consulting a doctor is essential."
    ),
    "legal_advisor": (
        "You are an experienced lawyer specializing in German law. "
        "Explain the legal situation clearly, in a structured manner, with reference to relevant laws (§§). "
        "Point out the necessity of individual legal advice."
    ),
    "translation": (
        "You are a professional translator with native-level proficiency in German, English, French and Spanish. "
        "Translate precisely, idiomatically and faithfully to context. "
        "Preserve the tone, register and technical terminology of the original. "
        "Note cultural particularities when relevant."
    ),
    "reasoning": (
        "You are an analytical thinker specialized in complex multi-step problems. "
        "Decompose problems into explicit sub-steps, show your chain of thought. "
        "Explicitly name assumptions, uncertainties and alternative interpretations. "
        "Arrive at a clear, well-reasoned conclusion."
    ),
    "vision": (
        "You are a vision AI expert for image and document analysis. "
        "Describe content, context and relevant details systematically and in a structured manner. "
        "For text in images: transcribe completely and verbatim. "
        "For diagrams/charts: extract data points and explain the message. "
        "For screenshots: identify UI elements, errors and states precisely."
    ),
    "data_analyst": (
        "You are a data science and data analysis expert. "
        "Analyze data structures, patterns and relationships with statistical precision. "
        "Write Python code (pandas, numpy, matplotlib/seaborn) when visualization or transformation is requested. "
        "Interpret results and name statistical limitations."
    ),
    "science": (
        "You are a natural scientist with expertise in chemistry, biology, physics and environmental sciences. "
        "Explain concepts precisely based on current research and recognized theories. "
        "Distinguish established knowledge from active research areas. "
        "Use correct technical terminology; explain foreign terms at first occurrence."
    ),
    "agentic_coder": (
        "You are a context manager for code tasks on systems with limited VRAM. "
        "ABSOLUTE RULE: NEVER read entire files. Context window: 4096–8192 tokens. "
        "Mandatory workflow: 1) repo_map → overview, 2) read_file_chunked → targeted max. 50 lines, "
        "3) lsp_query → signatures/references. Plan first, then read minimally. "
        "Answer with code and line numbers, no filler text."
    ),
}

# _CUSTOM_EXPERT_PROMPTS imported from config.py

def _get_expert_prompt(cat: str, user_experts: Optional[dict] = None) -> str:
    """Returns the system prompt for a category.
    Priority: Template system prompt > Custom (Admin UI) > Default > general fallback.
    Tool injection: appends context-dependent tool definitions to the prompt.
    """
    from tool_injector import inject_tools
    if user_experts:
        cat_models = user_experts.get(cat, [])
        if cat_models and cat_models[0].get("_system_prompt"):
            return inject_tools(cat_models[0]["_system_prompt"], cat)
    base = (_CUSTOM_EXPERT_PROMPTS.get(cat)
            or DEFAULT_EXPERT_PROMPTS.get(cat)
            or DEFAULT_EXPERT_PROMPTS["general"])
    return inject_tools(base, cat)

# All PROM_* metrics imported from metrics.py (single registry, no duplicate names)
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
            if _userdb_pool is not None:
                async with _userdb_pool.connection() as conn:
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


def _extract_session_id(request: Request) -> Optional[str]:
    """Extract or derive a session ID for semantic memory continuity.

    Priority:
    1. Explicit headers (client-provided, most reliable)
    2. Conversation fingerprint derived from the request body's first user
       message — gives a stable, client-agnostic session ID without any
       client configuration. Clients that send the same conversation history
       across turns will naturally hash to the same session_id.

    Supported explicit headers:
    - Claude Code / Anthropic SDK:  x-stainless-session-id
    - Continue.dev:                 x-request-id
    - OpenCode / OpenAI SDK:        x-stainless-session-id
    - Generic:                      x-session-id, x-conversation-id
    """
    h = request.headers
    explicit = (
        h.get("x-claude-code-session-id") or
        h.get("x-stainless-session-id") or
        h.get("x-session-id") or
        h.get("x-conversation-id") or
        h.get("x-request-id")
    )
    if explicit:
        return explicit
    # Derive from conversation fingerprint: hash of the first user message.
    # Stable across requests in the same conversation (same opening message).
    try:
        body_bytes = request.state._body if hasattr(request.state, "_body") else None
        if body_bytes:
            import hashlib as _hashlib
            body = json.loads(body_bytes)
            msgs = body.get("messages", [])
            # Use first 3 user messages for a stable fingerprint that survives
            # generic openers ("OK", "Hi") without collisions across conversations.
            user_msgs = [
                str(m.get("content", ""))[:200]
                for m in msgs if m.get("role") == "user"
            ][:3]
            if user_msgs:
                # Include user_id in hash so two users asking the same question
                # get separate memory namespaces.
                user_id = request.state.user_id if hasattr(request.state, "user_id") else ""
                seed = user_id + "".join(user_msgs)
                fp = _hashlib.sha256(seed.encode()).hexdigest()[:24]
                return f"fp-{fp}"
    except Exception:
        pass
    return None


_model_avail_cache: Dict[str, tuple] = {}  # {node: (monotonic_ts, frozenset[model_names])}
_MODEL_AVAIL_TTL = 60.0  # seconds


async def _get_available_models(node: str) -> Optional[frozenset]:
    """Queries available models of a node (60s cache).
    Returns None if the node is unreachable → request is not blocked."""
    now = time.monotonic()
    with _cache_lock:
        if node in _model_avail_cache:
            ts, models = _model_avail_cache[node]
            if now - ts < _MODEL_AVAIL_TTL:
                return models
    url = URL_MAP.get(node, "").rstrip("/")
    token = TOKEN_MAP.get(node, "ollama")
    api_type = API_TYPE_MAP.get(node, "ollama")
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as _c:
            if api_type == "ollama":
                _r = await _c.get(f"{url}/api/tags",
                                  headers={"Authorization": f"Bearer {token}"})
                models = frozenset(m["name"] for m in _r.json().get("models", [])) \
                         if _r.status_code == 200 else None
            else:  # openai-compatible
                _r = await _c.get(f"{url}/v1/models",
                                  headers={"Authorization": f"Bearer {token}"})
                models = frozenset(m["id"] for m in _r.json().get("data", [])) \
                         if _r.status_code == 200 else None
        if models is not None:
            with _cache_lock:
                _model_avail_cache[node] = (now, models)
        return models
    except Exception as _e:
        logger.debug(f"Model availability check failed for {node}: {_e}")
        return None


_endpoint_semaphores: Dict[str, asyncio.Semaphore] = {}
_endpoint_gpu_indices: Dict[str, int] = {}

async def _init_semaphores():
    """Create per-endpoint semaphores in the event-loop context, derived from INFERENCE_SERVERS_LIST."""
    global _endpoint_semaphores, _endpoint_gpu_indices
    for s in INFERENCE_SERVERS_LIST:
        name  = s["name"]
        count = int(s.get("gpu_count", 1))
        _endpoint_semaphores[name]  = asyncio.Semaphore(count)
        _endpoint_gpu_indices[name] = 0
    logger.info(f"🎮 GPU semaphores: { {s['name']: s.get('gpu_count', 1) for s in INFERENCE_SERVERS_LIST} }")

async def assign_gpu(endpoint: str = "") -> int:
    srv   = next((s for s in INFERENCE_SERVERS_LIST if s["name"] == endpoint), None)
    count = int(srv["gpu_count"]) if srv else 1
    with _gpu_lock:
        idx = _endpoint_gpu_indices.get(endpoint, 0) % max(count, 1)
        _endpoint_gpu_indices[endpoint] = idx + 1
    return idx

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

# JUDGE_MODEL imported from config.py; LLM instances constructed here
judge_llm     = ChatOpenAI(model=JUDGE_MODEL,   base_url=JUDGE_URL,   api_key=JUDGE_TOKEN,   timeout=JUDGE_TIMEOUT)
planner_llm   = ChatOpenAI(model=PLANNER_MODEL, base_url=PLANNER_URL, api_key=PLANNER_TOKEN, timeout=PLANNER_TIMEOUT)
# Ingest LLM: dedicated model for background GraphRAG extraction.
# Falls back to judge_llm when GRAPH_INGEST_MODEL is not configured.
ingest_llm = (
    ChatOpenAI(
        model=GRAPH_INGEST_MODEL,
        base_url=GRAPH_INGEST_URL,
        api_key=GRAPH_INGEST_TOKEN,
        timeout=JUDGE_TIMEOUT,
    )
    if GRAPH_INGEST_MODEL and GRAPH_INGEST_URL
    else None  # resolved to judge_llm at call site
)
# _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG imported from config.py
search: Optional[SearxSearchWrapper] = (
    SearxSearchWrapper(searx_host=_SEARXNG_URL) if _SEARXNG_URL else None
)
if search is None:
    logger.info("SEARXNG_URL not set — web search disabled")
# Mutable globals — set by lifespan (graph_manager, redis_client, kafka_producer in state.py in next step)
graph_manager:  Optional[GraphRAGManager]  = None
redis_client:   Optional[aioredis.Redis]   = None
kafka_producer: Optional[AIOKafkaProducer] = None

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

def _truncate_history(messages: List[Dict], max_turns: int = None, max_chars: int = None) -> List[Dict]:
    """Wrapper: forwards to parsing._truncate_history_pure with app-level defaults and Prometheus counters."""
    return _truncate_history_pure(
        messages, max_turns, max_chars,
        default_max_turns=HISTORY_MAX_TURNS,
        default_max_chars=HISTORY_MAX_CHARS,
        prom_unlimited=PROM_HISTORY_UNLIMITED,
        prom_compressed=PROM_HISTORY_COMPRESSED,
    )


async def _apply_semantic_memory(
    raw_history: List[Dict],
    kept_history: List[Dict],
    user_input: str,
    session_id: Optional[str],
    enabled: bool,
    user_id: str = "",
    team_ids: Optional[List[str]] = None,
    cross_session_enabled: bool = False,
    cross_session_scopes: Optional[List[str]] = None,
    # Template-level memory tuning
    n_results: int = 0,
    ttl_hours: int = 0,
    # User preference overrides (loaded from DB)
    prefer_fresh: bool = False,
    share_with_team: bool = False,
) -> List[Dict]:
    """Store evicted turns (Tier-2 write) and inject relevant past turns as warm context.

    Privacy hierarchy:
      - session: only current session turns (default)
      - private cross-session: all sessions owned by user_id (scope=private)
      - team cross-session: sessions shared within user's teams (scope=team)
      - shared cross-session: tenant-visible knowledge (scope=shared)

    User flags:
      prefer_fresh     — disables cross-session; each session starts clean
      share_with_team  — stores turns as scope=team so team members can retrieve them
    """
    if not enabled or not session_id:
        return kept_history

    from memory_retrieval import SCOPE_PRIVATE, SCOPE_TEAM, _N_RESULTS
    store      = get_memory_store()
    _team_ids  = team_ids or []
    _scopes    = cross_session_scopes or [SCOPE_PRIVATE]
    _n         = n_results or _N_RESULTS
    store_scope = SCOPE_TEAM if (share_with_team and _team_ids) else SCOPE_PRIVATE

    # Write: embed evicted turns in background (non-blocking)
    evicted = compute_evicted_turns(raw_history, kept_history)
    if evicted:
        async def _store_bg() -> None:
            n = await store.store_turns(
                session_id, evicted,
                user_id=user_id,
                team_id=_team_ids[0] if _team_ids else "",
                scope=store_scope,
                ttl_hours=ttl_hours,
            )
            if n:
                PROM_SEMANTIC_MEMORY_STORED.inc(n)
        asyncio.create_task(_store_bg())

    # Read: current-session retrieval (always)
    current_turns = await store.retrieve_relevant(session_id, user_input, n_results=_n)

    # Read: cross-session retrieval (if enabled, user_id known, and user hasn't opted out)
    cross_turns: List[Dict] = []
    if cross_session_enabled and user_id and not prefer_fresh:
        cross_turns = await store.retrieve_cross_session(
            session_id=session_id,
            query=user_input,
            user_id=user_id,
            team_ids=_team_ids,
            allowed_scopes=_scopes,
        )

    # Merge
    retrieved = store.merge_session_results(current_turns, cross_turns)
    if not retrieved:
        return kept_history

    warm_block = store.build_warm_context_block(retrieved)
    if not warm_block:
        return kept_history

    PROM_SEMANTIC_MEMORY_HITS.inc()
    cross_count = sum(1 for t in retrieved if t.get("cross"))
    logger.info(
        f"💾 Semantic memory: {len(retrieved)} warm turns "
        f"({len(retrieved)-cross_count} session, {cross_count} cross-session) "
        f"session={session_id[:8]}…"
    )

    warm_messages: List[Dict] = [
        {"role": "user",      "content": warm_block},
        {"role": "assistant", "content": "[Acknowledged: I have access to these earlier conversation turns.]"},
    ]
    return warm_messages + kept_history


# _DOMAIN_SCORES, _domain_score, _reliability_label — see web_search.py

async def _web_search_with_citations(query: str, ddg_fallback: Optional[bool] = None) -> str:
    """Wrapper: forwards to web_search._web_search_with_citations_impl with app-level search singleton.

    ddg_fallback: None = use global WEB_SEARCH_FALLBACK_DDG env var;
                  True/False = explicit per-call override (from template state).
    """
    use_ddg = _WEB_SEARCH_FALLBACK_DDG if ddg_fallback is None else ddg_fallback
    return await _web_search_with_citations_impl(query, search, ddg_fallback=use_ddg)

async def _store_response_metadata(
    response_id: str,
    user_input: str,
    expert_models_used: List[str],
    chroma_doc_id: str,
    plan: Optional[List[Dict]] = None,
    cost_tier: str = "",
) -> None:
    """Stores response metadata for later feedback in Valkey (TTL 7 days)."""
    if redis_client is None:
        return
    try:
        meta = {
            "input":               user_input[:300],
            "expert_models_used":  json.dumps(expert_models_used),
            "chroma_doc_id":       chroma_doc_id,
            "ts":                  datetime.now().isoformat(),
            "plan_cats":           json.dumps([t.get("category", "") for t in (plan or [])]),
            "cost_tier":           cost_tier,
        }
        key = f"moe:response:{response_id}"
        await redis_client.hset(key, mapping=meta)
        await redis_client.expire(key, 60 * 60 * 24 * 7)  # 7 Tage
    except Exception as e:
        logger.warning(f"Failed to save response metadata: {e}")

async def _self_evaluate(
    response_id: str,
    question: str,
    answer: str,
    chroma_id: str,
    template_name: str = "",
    complexity: str = "",
) -> None:
    """Judge LLM evaluates its own response async — does not block the response to the user."""
    try:
        eval_prompt = (
            "Rate the following answer on a scale of 1–5.\n"
            "1=incomplete/wrong, 3=adequate, 5=complete/correct\n\n"
            f"QUESTION: {question[:200]}\n\n"
            f"ANSWER: {answer[:600]}\n\n"
            "Reply ONLY with: SELF_RATING: N"
        )
        eval_res = await judge_llm.ainvoke(eval_prompt)
        m = re.search(r'SELF_RATING:\s*([1-5])', eval_res.content)
        score = int(m.group(1)) if m else 3
        PROM_SELF_EVAL.observe(score)
        if redis_client:
            await redis_client.hset(f"moe:response:{response_id}", "self_score", score)
        asyncio.create_task(_telemetry.record_self_score(
            _userdb_pool, response_id, score, template_name=template_name, complexity=complexity
        ))
        # Cost-tier learning loop: track low-quality responses routed as local_7b.
        # If the downgrade rate > 20% for a category, it signals the tier is too low.
        if redis_client and score <= 2:
            try:
                _meta = await redis_client.hgetall(f"moe:response:{response_id}")
                _tier = _meta.get("cost_tier", "") if isinstance(_meta, dict) else ""
                if _tier == "local_7b":
                    _plan_cats = json.loads(_meta.get("plan_cats", "[]") if isinstance(_meta, dict) else "[]")
                    for _cat in set(_plan_cats):
                        if _cat:
                            await redis_client.zincrby("moe:cost:downgrade", 1, f"local_7b:{_cat}")
                            await redis_client.expire("moe:cost:downgrade", 60 * 60 * 24 * 180)
                    logger.info(f"📉 Cost-tier downgrade recorded for local_7b (score={score})")
            except Exception:
                pass
        # Down-weight low-rated answers in the cache
        if score <= EVAL_CACHE_FLAG_THRESHOLD and chroma_id:
            await asyncio.to_thread(cache_collection.update, ids=[chroma_id], metadatas=[{"flagged": True, "self_score": score}])
            logger.info(f"⚠️ Self-rating {score}/5 — cache entry {chroma_id} flagged")
        else:
            logger.debug(f"🧐 Self-rating {score}/5 for response {response_id}")
    except Exception as e:
        logger.debug(f"Self-evaluation failed: {e}")


async def _neo4j_terms_exist(terms: List[str]) -> set:
    """Checks which terms are already present as entities in Neo4j (batch check)."""
    if graph_manager is None:
        return set()
    try:
        async with graph_manager.driver.session() as session:
            result = await session.run(
                "UNWIND $terms AS t "
                "MATCH (e:Entity) WHERE toLower(e.name) = toLower(t) OR toLower(e.aliases_str) CONTAINS toLower(t) "
                "RETURN DISTINCT toLower(t) AS found",
                {"terms": terms}
            )
            return {r["found"] async for r in result}
    except Exception as e:
        logger.debug(f"_neo4j_terms_exist failed: {e}")
        return set()


# ─── PROGRESS REPORTING ──────────────────────────────────────────────────────
# Any node can call _report() → message appears in the <think> block of the stream.

_progress_queue: contextvars.ContextVar[Optional[asyncio.Queue]] = \
    contextvars.ContextVar("_progress_queue", default=None)

async def _report(msg: str) -> None:
    q = _progress_queue.get()
    if q is not None:
        await q.put(msg)

# ─── KAFKA HELPERS ───────────────────────────────────────────────────────────
# _kafka_publish is now the authoritative version in services/kafka.py
from services.kafka import _kafka_publish


async def _shadow_request(user_input: str, user_id: str, api_key: str) -> None:
    """Sends a fire-and-forget shadow request to the BENCHMARK_SHADOW_TEMPLATE.

    The response is discarded from the user perspective but stored in Kafka
    (moe.requests) with shadow=True for quality gate analysis.
    Called every BENCHMARK_SHADOW_RATE-th production request.
    """
    if not BENCHMARK_SHADOW_TEMPLATE or not api_key:
        return
    try:
        payload = {
            "model":    BENCHMARK_SHADOW_TEMPLATE,
            "messages": [{"role": "user", "content": user_input[:1000]}],
            "stream":   False,
        }
        timeout = httpx.Timeout(120.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "http://localhost:8002/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            shadow_resp = ""
            if r.status_code == 200:
                data = r.json()
                shadow_resp = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        # Log shadow result to Kafka for comparator analysis
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "shadow":          True,
            "shadow_template": BENCHMARK_SHADOW_TEMPLATE,
            "user_id":         user_id,
            "input":           user_input[:300],
            "answer":          shadow_resp[:500],
            "ts":              datetime.now().isoformat(),
        }))
        logger.debug(f"🔬 Shadow request completed ({len(shadow_resp)} chars)")
    except Exception as e:
        logger.debug(f"Shadow request failed (non-critical): {e}")


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
                    if graph_manager is not None:
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
                                _itype = await graph_manager.ingest_synthesis(
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
                            await graph_manager.extract_and_ingest(
                                payload.get("input", ""),
                                payload.get("answer", ""),
                                _llm_for_ingest,
                                domain=payload.get("domain"),
                                source_model=payload.get("source_model", "unknown"),
                                confidence=float(payload.get("confidence", 0.5)),
                                knowledge_type=payload.get("knowledge_type", "factual"),
                                expert_domain=_source_expert,
                                tenant_id=payload.get("tenant_id"),
                                redis_client=redis_client,
                            )
                    # Detect ontology gaps: terms not present in Neo4j.
                    # Skip when the request came from an ontology curator template —
                    # those responses are classifications of existing gaps, not
                    # sources of new ones. Counting them would produce a self-
                    # replenishing loop (resolve one, add five).
                    if redis_client is not None and graph_manager is not None and not _is_curator:
                        try:
                            terms = graph_manager._extract_terms(payload.get("answer", ""))
                            if terms:
                                existing = await _neo4j_terms_exist(terms)
                                gaps = [t for t in terms if t not in existing]
                                if gaps:
                                    pipe = redis_client.pipeline()
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
                    if payload.get("positive") and redis_client is not None:
                        try:
                            rid = payload.get("response_id", "")
                            meta = await redis_client.hgetall(f"moe:response:{rid}")
                            plan_cats = json.loads(meta.get("plan_cats", "[]"))
                            if plan_cats:
                                sig = "+".join(sorted(set(plan_cats)))
                                await redis_client.zincrby("moe:planner_success", 1, sig)
                                await redis_client.expire("moe:planner_success", 60 * 60 * 24 * 180)  # 180 days
                                logger.debug(f"📈 Planner pattern saved: {sig}")
                        except Exception as e:
                            logger.debug(f"Planner pattern save failed: {e}")
                elif msg.topic == KAFKA_TOPIC_LINTING:
                    if graph_manager is not None:
                        _llm_for_lint = ingest_llm if ingest_llm is not None else judge_llm
                        async def _run_linting_tracked(_llm=_llm_for_lint):
                            PROM_LINTING_RUNS.inc()
                            try:
                                _result = await asyncio.wait_for(
                                    graph_manager.run_graph_linting(
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
_ROUTE_PROTOTYPES: Dict[str, List[str]] = {
    "math": [
        "Calculate the integral of x² dx",
        "What is the solution to 3x + 5 = 20?",
        "Calculate the square root of 144",
        "What is 15% of 280?",
        "Solve the quadratic equation x²-4x+3=0",
    ],
    "code_reviewer": [
        "Check this Python code for bugs",
        "What is wrong with this JavaScript function?",
        "Optimize this SQL query",
        "Refactor this C++ code",
        "Find security vulnerabilities in this PHP script",
    ],
    "technical_support": [
        "How do I install Docker on Ubuntu?",
        "Configure Nginx as a reverse proxy",
        "Explain how Kubernetes works",
        "How do I set up an SSL certificate?",
        "What is the difference between TCP and UDP?",
    ],
    "medical_consult": [
        "What are the side effects of ibuprofen?",
        "What are the symptoms of a heart attack?",
        "How is type 2 diabetes treated?",
        "Interactions between metformin and aspirin",
        "What does elevated blood pressure mean?",
    ],
    "legal_advisor": [
        "What does §242 BGB regulate?",
        "How does the right of termination work in Germany?",
        "What are my rights as a tenant under tenancy law?",
        "Explain the GDPR principles",
        "What is a restraining order?",
    ],
    "creative_writer": [
        "Write a short story about a robot",
        "Write a poem about autumn",
        "Create a creative product description text",
        "Write a dialogue script for a scene",
    ],
    "research": [
        "Research the latest developments in quantum computing",
        "Summarize the current state of AI research",
        "What are the latest climate research findings?",
        "Analyze the economic situation in Germany 2024",
    ],
    "precision_tools": [
        "Calculate the SHA256 hash of 'hello world'",
        "Convert 100 km/h to m/s",
        "What is the difference in days between 01/01/2020 and 07/15/2024?",
        "Which subnets does 192.168.1.0/24 contain?",
        "Extract all email addresses from this text",
    ],
    "general": [
        "What is the capital of France?",
        "Explain the concept of the theory of relativity to me",
        "How did the universe originate?",
        "What is the difference between AI and ML?",
    ],
}


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
            await asyncio.to_thread(route_collection.upsert, documents=docs, ids=ids, metadatas=metas)
            logger.info(f"🧭 Semantic Router: {len(docs)} prototypes upserted in ChromaDB")
    except Exception as e:
        logger.warning(f"⚠️ Semantic Router seeding failed: {e}")


# --- NODES ---

async def cache_lookup_node(state: AgentState):
    logger.debug("--- [NODE] CACHE LOOKUP ---")
    # Template toggle: skip cache if disabled
    if not state.get("enable_cache", True):
        logger.info("Cache disabled by template toggle")
        return {"cached_facts": "", "cache_hit": False}
    # Non-default modes bypass the cache — format mismatch would deliver wrong answers
    if state.get("mode", "default") != "default":
        return {"cached_facts": "", "cache_hit": False}
    await _report("🔍 Cache-Lookup...")
    # Normalized query for similarity search — pipeline input stays unchanged
    _cache_query = re.sub(r'\s+', ' ', state["input"].lower().strip().rstrip('?!.,;'))

    # L0: Exact query hash cache (Valkey, instant, before ChromaDB)
    if state.get("no_cache"):
        pass  # skip L0 cache — no_cache flag set by client
    elif redis_client:
        try:
            import hashlib as _hl
            _q_hash = _hl.sha256(_cache_query.encode()).hexdigest()[:24]
            _l0_key = f"moe:qcache:{_q_hash}"
            _l0_hit = await redis_client.get(_l0_key)
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
    res = await asyncio.to_thread(cache_collection.query, query_texts=[_cache_query], n_results=3)
    cached = ""
    hit = False
    if not state.get("no_cache") and res['documents'] and res['documents'][0]:
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


async def semantic_router_node(state: AgentState):
    """
    Semantic pre-router — runs after cache_lookup_node, before planner_node.
    Compares the user query semantically against prototypical task queries per category.
    If a clear match is found (dist < ROUTE_THRESHOLD, gap > ROUTE_GAP),
    'direct_expert' is set and a synthetic single-task plan is created.
    planner_node then skips the LLM call and uses this plan directly.
    On ambiguity or cache hit: no intervention.
    """
    # Don't route if cache hit (will be skipped anyway) or non-default mode
    if state.get("cache_hit") or state.get("mode", "default") != "default":
        return {"direct_expert": ""}

    _query = re.sub(r'\s+', ' ', state["input"].lower().strip().rstrip('?!.,;'))
    try:
        res = await asyncio.to_thread(route_collection.query, query_texts=[_query], n_results=2)
        docs  = res.get("documents",  [[]])[0]
        dists = res.get("distances",  [[1.0, 1.0]])[0]
        metas = res.get("metadatas",  [[{}, {}]])[0]

        if len(dists) < 2 or not docs:
            return {"direct_expert": ""}

        top_dist  = dists[0]
        gap       = dists[1] - dists[0]
        category  = metas[0].get("category", "")

        if top_dist < ROUTE_THRESHOLD and gap > ROUTE_GAP and category:
            synthetic_plan = [{"task": state["input"], "category": category}]
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


def _validate_tool_result(result_str: str, tool: str) -> tuple[bool, str]:
    """Sanity-check MCP tool output before it enters working memory."""
    if not result_str or len(result_str.strip()) < 3:
        return False, "empty_result"
    lower = result_str.lower()
    if lower.startswith("[") and "error" in lower[:30]:
        return False, "error_prefix"
    return True, ""


async def mcp_node(state: AgentState):
    """Executes precision tool calls via MCP server — all in parallel."""
    if state.get("cache_hit"):
        return {"mcp_result": ""}

    precision_tasks = [
        t for t in state.get("plan", [])
        if isinstance(t, dict) and t.get("category") == "precision_tools" and t.get("mcp_tool")
    ]
    if not precision_tasks:
        return {"mcp_result": ""}

    # Per-User MCP-Tool Permission-Check
    allowed_mcp = state.get("user_permissions", {}).get("mcp_tool")
    if allowed_mcp is not None and "*" not in allowed_mcp:
        precision_tasks = [t for t in precision_tasks if t.get("mcp_tool") in allowed_mcp]
        if not precision_tasks:
            logger.info("⛔ MCP tools not enabled for this user")
            return {"mcp_result": ""}

    tool_names = [t.get("mcp_tool") for t in precision_tasks]
    await _report(f"⚙️ MCP Precision Tools: {', '.join(tool_names)}")
    logger.info(f"--- [NODE] MCP ({len(precision_tasks)} Tools parallel) ---")

    # Working Memory accumulators — carry over facts from previous iterations
    _wm: dict = dict(state.get("working_memory") or {})
    _log: list = list(state.get("tool_calls_log") or [])
    _failures: list = list(state.get("tool_failures") or [])
    _ts_now = lambda: datetime.utcnow().isoformat() + "Z"

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
        _schema = MCP_TOOL_SCHEMAS.get(tool, {})
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
                _pre_fix_res = await _invoke_judge_with_retry(state, _pre_fix_prompt, max_retries=1, temperature=0.05)
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
                    "chat_id": state.get("chat_id", ""), "user_id": state.get("user_id", ""),
                    "tool": tool, "args": args, "task": desc, "result": None,
                    "error": err_str, "latency_s": _mcp_dt,
                    "caller": "orchestrator_pipeline", "template": state.get("template_name", ""),
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
                    fix_res = await _invoke_judge_with_retry(state, fix_prompt, max_retries=1)
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
                "chat_id": state.get("chat_id", ""), "user_id": state.get("user_id", ""),
                "tool": tool, "args": args, "task": desc, "result": result_str[:500],
                "error": None, "latency_s": _mcp_dt,
                "caller": "orchestrator_pipeline", "template": state.get("template_name", ""),
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
                "chat_id": state.get("chat_id", ""), "user_id": state.get("user_id", ""),
                "tool": tool, "args": args, "task": desc, "result": None,
                "error": str(e)[:300], "latency_s": _mcp_dt,
                "caller": "orchestrator_pipeline", "template": state.get("template_name", ""),
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


async def graph_rag_node(state: AgentState):
    """Fetch structured graph context from Neo4j — parallel to LLM experts.
    When GRAPH_VIA_MCP=true, the MCP server is used as interface (graph-as-a-tool),
    otherwise direct access to graph_manager (fallback, backwards compatible).
    """
    if state.get("cache_hit"):
        return {"graph_context": ""}
    # Template toggle: skip GraphRAG if disabled
    if not state.get("enable_graphrag", True):
        logger.info("GraphRAG disabled by template toggle")
        return {"graph_context": ""}
    # Complexity routing: skip for trivial requests (complexity_estimator sets skip_graph)
    if state.get("skip_graph"):
        logger.info("⚡ GraphRAG skipped (complexity routing: trivial/skip_graph)")
        return {"graph_context": ""}
    if not GRAPH_VIA_MCP and graph_manager is None:
        return {"graph_context": ""}
    plan = state.get("plan", [])
    categories = [t.get("category", "") for t in plan if isinstance(t, dict)]

    # GraphRAG on-demand: skip the Neo4j query for queries that are clearly about
    # public external facts (papers, databases, media) rather than internal ontology.
    # We still run if the plan explicitly includes knowledge_healing (graph needed).
    _has_knowledge_healing = "knowledge_healing" in categories
    _is_public_fact_query = bool(_RESEARCH_DETECT.search(state.get("input", "")))
    if _is_public_fact_query and not _has_knowledge_healing:
        logger.info("⚡ GraphRAG skipped (public-fact query — internal graph not relevant)")
        await _report("⚡ GraphRAG: skipped (external research query)")
        return {"graph_context": ""}

    # GraphRAG-Cache (Valkey, TTL=3600s)
    import hashlib as _hashlib
    _graph_cache_key = f"moe:graph:{_hashlib.sha256((state['input'][:200] + ''.join(sorted(categories))).encode()).hexdigest()[:16]}"
    if redis_client is not None:
        try:
            _cached_ctx = await redis_client.get(_graph_cache_key)
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
                    json={"tool": "graph_query", "args": {"query": state["input"], "categories": categories}},
                )
                _resp.raise_for_status()
                ctx = _resp.json().get("result", "")
        else:
            # Direct access (default, backwards compatible)
            _tenant_ids = state.get("tenant_ids", [])
            ctx = await graph_manager.query_context(
                state["input"], categories, tenant_ids=_tenant_ids or None,
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
            if redis_client is not None:
                asyncio.create_task(redis_client.setex(_graph_cache_key, 3600, ctx))
        else:
            await _report("🔗 GraphRAG: no matching context found")

        # Domain-filtered ChromaDB retrieval using planner-extracted metadata_filters
        _meta_filters = state.get("metadata_filters") or {}
        if _meta_filters and cache_collection is not None:
            try:
                _where: Dict = {k: {"$eq": v} for k, v in _meta_filters.items() if isinstance(v, str) and v}
                if len(_where) > 1:
                    _where = {"$and": [{k: v} for k, v in _where.items()]}
                _chroma_res = await asyncio.to_thread(
                    cache_collection.query,
                    query_texts=[state["input"]],
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
            for _m in _ent_re.finditer(ctx[:4000]):
                _entity_meta.append({
                    "name": _m.group(1).strip(),
                    "type": _m.group(2).strip(),
                    "confidence": float(_m.group(3)),
                })

        return {"graph_context": ctx, "graphrag_entities": _entity_meta}
    except Exception as e:
        logger.warning(f"GraphRAG query_context error: {e}")
        return {"graph_context": ""}


async def math_node_wrapper(state: AgentState):
    if state.get("cache_hit"):
        return {"math_result": ""}
    plan = state.get("plan", [])
    has_math = any(t.get("category") == "math" for t in plan if isinstance(t, dict))
    has_precision = any(t.get("category") == "precision_tools" for t in plan if isinstance(t, dict))
    # Skip if no math task or precision_tools already covers the math
    if not has_math or has_precision:
        return {"math_result": ""}
    logger.debug("--- [NODE] MATH CALCULATION ---")
    await _report("🧮 Math module (SymPy)...")
    result = await math_node(state)
    await _report("🧮 Math computation complete")
    return {"math_result": result["math_result"]}

def _sanitize_plan(raw: list, fallback_input: str) -> list:
    """
    Ensures all plan entries are valid task dicts.
    Strings, empty dicts or dicts without 'task' key are discarded.
    Returns at least one fallback task.
    """
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


async def planner_node(state: AgentState):
    _output_skill = ""  # Initialize early to prevent UnboundLocalError
    # Cache hit: no LLM call needed
    if state.get("cache_hit"):
        logger.info("📋 Planner skipped (cache hit)")
        return {"plan": []}
    # Semantic pre-routing: direct expert path without LLM call
    if state.get("direct_expert") and state.get("plan"):
        logger.info(f"📋 Planner skipped (semantic router → '{state['direct_expert']}')")
        return {"plan": state["plan"]}

    # Emit pending reports (e.g. skill resolution) from state
    for _pr in (state.get("pending_reports") or []):
        await _report(_pr)

    # ── Agentic loop: read config from template state ───────────────────────
    _agentic_iteration  = state.get("agentic_iteration") or 0
    _agentic_max_rounds = state.get("agentic_max_rounds") or 0
    _is_agentic_replan  = _agentic_iteration > 0 and _agentic_max_rounds > 0

    # Planner result cache: same request → same plan (Valkey, TTL=30 min)
    # Skip cache entirely during agentic re-planning or when the caller requests no cache.
    # no_cache=True bypasses both L0 LLM cache and planner cache to ensure a fresh plan
    # is generated — important for benchmark runs that follow cache pre-warming.
    import hashlib as _hashlib
    _no_cache_flag  = state.get("no_cache", False)
    # Include a short config fingerprint so the plan cache auto-invalidates when
    # the MCP tool set or planner prompt changes between deployments.
    _cfg_fp = _hashlib.md5(
        f"{len(MCP_TOOLS_DESCRIPTION)}:{(state.get('planner_prompt') or '')[:80]}"
        .encode()
    ).hexdigest()[:6]
    # Include chat_history presence in key: same query needs different plan
    # in conversation context (memory_recall) vs. standalone (research).
    _has_history = "h" if len(state.get("chat_history") or []) >= 2 else "n"
    _plan_cache_key = f"moe:plan:{_cfg_fp}:{_has_history}:{_hashlib.sha256(state['input'][:300].encode()).hexdigest()[:16]}"
    if redis_client is not None and not _is_agentic_replan and not _no_cache_flag:
        try:
            _cached_plan_raw = await redis_client.get(_plan_cache_key)
            if _cached_plan_raw:
                _cached_plan = json.loads(_cached_plan_raw)
                logger.info(f"📋 Planner cache hit (Valkey) — skipping LLM")
                await _report("📋 Planner: plan loaded from Valkey cache")
                return {"plan": _cached_plan, "prompt_tokens": 0, "completion_tokens": 0}
        except Exception as _pe:
            logger.debug(f"Planner cache read error: {_pe}")

    # Complexity estimation: determine routing hints before LLM planner call
    from complexity_estimator import estimate_complexity, complexity_routing_hint
    _complexity = estimate_complexity(state["input"])
    # Day-2 upgrade: factual questions inside a multi-turn conversation are
    # almost always asking about something the user stated earlier — not
    # web-searchable facts. Upgrade trivial AND moderate to memory_recall
    # when substantive chat_history is present. Complex/research queries are
    # already routed differently by estimate_complexity before we reach here,
    # so upgrading moderate is safe for recall-heavy conversation patterns.
    _chat_hist = state.get("chat_history") or []
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
            state["input"], re.I,
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
    _explicit_temp = state.get("query_temperature")  # set by HTTP handler from request
    _query_temp    = _explicit_temp if _explicit_temp is not None else _detect_query_temperature(state["input"])
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
    if state.get("mode") == "agent":
        logger.info("📋 Agent mode: forcing code_reviewer + technical_support")
        await _report("📋 Agent mode: code experts activated...")
        return {
            "plan": [
                {"task": state["input"], "category": "code_reviewer"},
                {"task": state["input"], "category": "technical_support"},
            ],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    # memory_recall fast-path: if complexity is memory_recall AND the template has a
    # dedicated memory_recall expert configured, skip the LLM planner entirely.
    # The planner LLM would misroute recall questions (e.g. routing to "research")
    # because it cannot distinguish facts from this conversation vs. external knowledge.
    # This bypass is template-driven — only activates when memory_recall is in user_experts.
    _user_experts_map = state.get("user_experts") or {}
    if _complexity == "memory_recall" and "memory_recall" in _user_experts_map:
        logger.info("🧠 memory_recall fast-path: dedicated expert configured, LLM planner skipped")
        await _report("🧠 Memory Expert: Analysiere Konversationshistorie...")
        return {
            **_complexity_state_update,
            "plan": [{"task": state["input"], "category": "memory_recall"}],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    logger.debug("--- [NODE] PLANNER ---")
    await _report("📋 Planner analyzing request...")
    # agentic_coder is an optional category — it only appears when the template has it enabled
    # or the mode requires it. DEFAULT_EXPERT_PROMPTS contains the fallback prompt.
    expert_categories = list(EXPERTS.keys())
    if "agentic_coder" not in expert_categories and (
        state.get("mode") in ("agent_orchestrated", "code")
        or "agentic_coder" in (state.get("user_experts") or {})
    ):
        expert_categories = expert_categories + ["agentic_coder"]

    # Annotate images in planner input so routing triggers 'vision'
    # The marker must be exactly "[BILD-EINGABE vorhanden]" — as specified in the planner rule
    images = state.get("images") or []
    if images:
        state = dict(state)
        _img_hint = f"[BILD-EINGABE vorhanden] ({len(images)} Bild(er))"
        if "[BILD-EINGABE vorhanden]" not in state["input"]:
            state["input"] = f"{_img_hint} {state['input']}"

    # SELF_EVAL quality hint — informs planner about historical performance
    _quality_hint = ""
    try:
        from telemetry import get_quality_hint as _get_quality_hint
        _quality_hint = await _get_quality_hint(
            _userdb_pool, state.get("template_name", ""), _complexity
        )
        if _quality_hint:
            _quality_hint = f"\n{_quality_hint}\n"
    except Exception:
        pass

    # Load proven plan patterns from positive user feedback
    success_hint = ""
    if redis_client is not None:
        try:
            patterns = await redis_client.zrevrange("moe:planner_success", 0, 4, withscores=True)
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
        _few_shot_hint = await _get_fsc(_plan_categories, redis_client, max_per_cat=2)
    except Exception:
        pass

    # Show agentic code tools only when mode matches or agentic_coder category is active
    _inject_agentic = (
        state.get("mode") in ("agent_orchestrated", "code")
        or "agentic_coder" in (state.get("user_experts") or {})
    )
    _agentic_code_block = (
        f"\nCODE NAVIGATION TOOLS (only for 'agentic_coder' category — NOT for other experts!):\n"
        f"Use these tools when code files are to be analyzed/edited.\n"
        f"{AGENTIC_CODE_TOOLS_DESCRIPTION}\n"
        f"Format: {{\"task\": \"...\", \"category\": \"precision_tools\", "
        f"\"mcp_tool\": \"repo_map|read_file_chunked|lsp_query\", \"mcp_args\": {{...}}}}\n"
        f"THEN use agentic_coder expert for analysis/implementation.\n"
    ) if _inject_agentic and AGENTIC_CODE_TOOLS_DESCRIPTION else ""

    _planner_role = (state.get("planner_prompt") or "").strip() or DEFAULT_PLANNER_ROLE

    # ── Agentic re-plan: inject gap context and clear stale single-string results ──
    _agentic_context_block = ""
    _agentic_state_reset: dict = {}
    if _is_agentic_replan:
        _gap            = (state.get("agentic_gap") or "").strip()
        _history        = state.get("agentic_history") or []
        _prev_found     = _history[-1].get("findings", "") if _history else ""
        _wm             = state.get("working_memory") or {}
        _failures       = state.get("tool_failures") or []
        _tried_queries  = state.get("attempted_queries") or []
        _strategy_hint  = (state.get("search_strategy_hint") or "").strip()

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
        _disc_domains: list = state.get("discovered_domains") or []
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
{_build_filtered_tool_desc(state["input"], enable_graphrag=state.get("enable_graphrag", False)) if state.get("complexity_level") != "trivial" else "  - calculate: arithmetic and math  - date_diff: date calculations  - unit_convert: unit conversions"}
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

Request: {state['input']}

JSON array:"""
    await _report(f"📋 Planner prompt ({len(prompt)} chars):\n{prompt}")
    total_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    plan: Optional[list] = None
    for attempt in range(PLANNER_RETRIES):
        _planner_llm_inst = await _get_planner_llm(state)
        _planner_url = (state.get("planner_url_override") or PLANNER_URL or "").rstrip("/")
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
                    _skill_body = _load_skill_body(_skill_name)
                    if _skill_body:
                        _output_skill = _skill_body
                        logger.info(f"🎯 Planner suggested skill: /{_skill_name}")
                    break
            plan = _sanitize_plan(raw, state["input"])
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
            plan = [{"task": state["input"], "category": "general"}]
            _extracted_filters = {}
    # Unload planner model — unless the same model is immediately needed as expert.
    # Use the template-specific planner model/URL when the template overrides them,
    # so we unload from the correct node instead of always hitting the global default.
    _actual_planner_model = (state.get("planner_model_override") or PLANNER_MODEL).strip()
    _actual_planner_url   = (state.get("planner_url_override")   or PLANNER_URL or "").strip()
    _actual_planner_base  = _actual_planner_url.rstrip("/").removesuffix("/v1")
    _upcoming_expert_models: set = set()
    for _task_item in plan:
        _cat = _task_item.get("category", "general")
        _experts_for_cat = (state.get("user_experts") or {}).get(_cat) or EXPERTS.get(_cat, [])
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
    if redis_client is not None and plan:
        asyncio.create_task(redis_client.setex(_plan_cache_key, 1800, json.dumps(plan)))
    if _extracted_filters:
        logger.info(f"📋 Planner metadata_filters: {_extracted_filters}")
    _skill_state = {"output_skill_body": _output_skill} if _output_skill else {}
    return {"plan": plan, "metadata_filters": _extracted_filters,
            **total_usage, **_complexity_state_update, **_skill_state, **_agentic_state_reset}

def _topological_levels(tasks: list[tuple[int, dict]]) -> list[list[tuple[int, dict]]]:
    """Group (index, task) pairs into dependency levels for mixed parallel/sequential execution.

    Tasks within the same level have no dependency on each other and run in parallel.
    A task in level N+1 depends on at least one task in level ≤ N.

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


async def fuzzy_router_node(state: AgentState):
    """Replace heuristic binary routing flags with fuzzy t-norm conjunction scores.

    The planner currently sets skip_research and enable_graphrag as binary flags
    derived from complexity heuristics. This node replaces that decision with a
    quantitative approach: independent confidence scores are computed from the
    plan content and combined via the Gödel t-norm (minimum) for a conservative
    gate — both signals must be strong to activate a retrieval node.

    Mathematical foundation:
        Fuzzy logics as the most general framework — de Vries (2007),
        arXiv:0707.2161. T-norm conjunction over [0,1]-valued truth degrees
        replaces Boolean routing. Gödel t-norm T_G(a,b) = min(a,b) (Gödel
        1932, discussed in de Vries 2007, §4); Łukasiewicz t-norm
        T_Ł(a,b) = max(0, a+b-1) (Łukasiewicz 1920, de Vries 2007, §4).

    Thresholds (configurable via env):
        FUZZY_VECTOR_THRESHOLD (default 0.30): below → skip_research=True
        FUZZY_GRAPH_THRESHOLD  (default 0.35): below → enable_graphrag=False
    """
    if state.get("cache_hit"):
        return {}

    plan             = state.get("plan") or []
    complexity_level = state.get("complexity_level") or "moderate"
    enable_graphrag  = state.get("enable_graphrag", True)
    skip_research    = state.get("skip_research", False)

    vector_conf, graph_conf = _compute_routing_confidence(
        plan, complexity_level, enable_graphrag
    )

    # Complexity as a second signal: map to [0,1] for t-norm input
    _complexity_weight = {"trivial": 0.1, "memory_recall": 0.0, "moderate": 0.5, "complex": 1.0}
    complexity_score   = _complexity_weight.get(complexity_level, 0.5)

    # Gödel t-norm: conservative gate — both signals must be strong
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
        f"🔀 Fuzzy Router (Gödel t-norm): "
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


async def expert_worker(state: AgentState):
    if state.get("cache_hit"):
        return {"expert_results": []}

    plan         = state.get("plan", [])
    chat_history = state.get("chat_history") or []
    expert_tasks = [
        (i, t) for i, t in enumerate(plan)
        if isinstance(t, dict) and t.get("category", "general") not in NON_EXPERT_CATEGORIES
    ]
    if not expert_tasks:
        return {"expert_results": []}

    logger.info(f"--- [NODE] EXPERTS ({len(expert_tasks)} Tasks, Two-Tier) ---")

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
            semaphore  = asyncio.Semaphore(999)  # LiteLLM verwaltet eigene Concurrency
        else:
            # Direct access (default): _select_node() selects based on tier/warm/load
            raw_ep     = model_cfg.get("endpoints") or [model_cfg.get("endpoint", "")]
            # Floating mode: if endpoint is empty, search ALL nodes for the model
            if not raw_ep or raw_ep == [""] or raw_ep == [""]:
                raw_ep = [s["name"] for s in INFERENCE_SERVERS_LIST]
                logger.info(f"🌐 Floating mode: searching all {len(raw_ep)} nodes for {model_name}")
            selected   = await _select_node(model_name, raw_ep, user_id=state.get("user_id", ""))
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

            mode        = state.get("mode", "default")
            mode_cfg    = MODES.get(mode, MODES["default"])
            sys_prompt = (
                _get_expert_prompt(cat, state.get("user_experts"))
                + mode_cfg["expert_suffix"]
                + _conf_format_for_mode(mode)
            )
            # Agent mode: embed file/code context from the client's system message
            agent_ctx = state.get("system_prompt", "")
            if agent_ctx and mode in ("agent", "agent_orchestrated"):
                sys_prompt += f"\n\n--- USER CODE CONTEXT ---\n{agent_ctx[:4000]}"
            # Inject correction memory for this category (avoids repeat mistakes)
            if CORRECTION_MEMORY_ENABLED and graph_manager is not None:
                try:
                    _driver = graph_manager.driver if hasattr(graph_manager, 'driver') else None
                    _corr = await _query_corrections(_driver, state.get("input", ""), cat)
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
                redis_client=redis_client,
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
            expert_images = state.get("images") or []
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
            _expert_temp = state.get("query_temperature")  # None = API default
            _llm_kwargs: dict = {"model": model_name, "base_url": url, "api_key": token,
                                 "timeout": _expert_node_timeout}
            if _expert_temp is not None:
                _llm_kwargs["temperature"] = _expert_temp
            llm = ChatOpenAI(**_llm_kwargs)
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
                content = res.content[:_expert_max_output]
                if len(res.content) > _expert_max_output:
                    content += "\n[…truncated]"
                await _report(f"✅ Expert [{model_name} / {cat}]:\n{content}\n---")
                # Token metrics
                _uid = state.get("user_id", "anon")
                PROM_TOKENS.labels(model=model_name, token_type="prompt",      node=endpoint, user_id=_uid).inc(usage.get("prompt_tokens", 0))
                PROM_TOKENS.labels(model=model_name, token_type="completion",  node=endpoint, user_id=_uid).inc(usage.get("completion_tokens", 0))
                # Confidence automatically as performance signal → no waiting for user feedback needed
                conf = _parse_expert_confidence(content)
                await _report(
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
                    _judge_model_name = (state.get("judge_model_override") or JUDGE_MODEL).strip()
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
        effective_experts = state.get("user_experts") or EXPERTS
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
        if state.get("force_tier1") and tier1:
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
                t1_has_high = any(
                    _parse_expert_confidence(r.get("res", "")) == "high"
                    for r in t1_results if r.get("res")
                )
                if t1_has_high or not tier2:
                    if t1_has_high:
                        logger.info(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                        await _report(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                    return task_results
            elif not tier2:
                return task_results

        if tier2:
            t2_names = ", ".join(e["model"] for _, e in tier2)
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

async def research_node(state: AgentState):
    if state.get("cache_hit"):
        return {"web_research": ""}
    # Template toggle: skip web research if disabled
    if not state.get("enable_web_research", True):
        logger.info("Web research disabled by template toggle")
        return {"web_research": ""}
    # Complexity routing: trivial requests skip research
    if state.get("skip_research"):
        logger.info("⚡ Research node skipped (trivial/moderate routing)")
        return {"web_research": ""}
    plan = state.get("plan", [])
    research_tasks = [t for t in plan if isinstance(t, dict) and t.get("category") == "research"]
    if not research_tasks:
        return {"web_research": ""}

    mode = state.get("mode", "default")
    _is_agentic_replan = state.get("agentic_iteration", 0) > 0 and state.get("agentic_max_rounds", 0) > 0
    _prev_queries: list = list(state.get("attempted_queries") or [])

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
        query = task.get("search_query", state["input"])
        domain_hint = task.get("domain", "")
        # Also extract site: prefix if planner embedded it in the query string
        if not domain_hint:
            _site_m = re.search(r'\bsite:(\S+)', query)
            if _site_m:
                domain_hint = _site_m.group(1)

        _cache_key = _search_cache_key(query, domain_hint)
        _ttl = _SEARCH_CACHE_TTL_AGNT if _is_agentic_replan else _SEARCH_CACHE_TTL

        # Cache read: skipped during agentic re-plan to guarantee fresh results
        if redis_client is not None and not _is_agentic_replan:
            try:
                cached = await redis_client.get(_cache_key)
                if cached:
                    logger.debug("🗄️ Search cache hit: %s", query[:60])
                    return cached.decode("utf-8", errors="replace"), query, "ok-cached"
            except Exception:
                pass

        await _report(f"🌐 Web search [{idx+1}/{total}]: '{query[:60]}'...")
        raw = await _web_search_with_citations(query, ddg_fallback=state.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG))
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
            if redis_client is not None and _effective_ttl > 0:
                try:
                    await redis_client.set(_cache_key, raw.encode("utf-8"), ex=_effective_ttl)
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
        _prev_domains: list = list(state.get("discovered_domains") or [])
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
        query = research_tasks[0].get("search_query", state["input"])
        logger.info(f"--- [NODE] WEB RESEARCH: '{query[:80]}' ---")
        raw, q, quality = await _fetch_one(research_tasks[0], 0, 1)
        _prev_domains = list(state.get("discovered_domains") or [])
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


async def merger_node(state: AgentState):
    # Cache hit: direct answer, no LLM call needed
    if state.get("cache_hit"):
        logger.info("--- [NODE] MERGER (cache hit, direct return) ---")
        await _report("💨 Merger: cached response delivered directly")
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id": state.get("response_id", ""),
            "input":       state["input"][:300],
            "cache_hit":   True,
            "ts":          datetime.now().isoformat(),
        }))
        return {"final_response": state.get("cached_facts", "")}

    logger.info("--- [NODE] MERGER & INGEST ---")
    await _report("🔀 Merger analyzing expert confidence...")

    _all_expert_raw = state.get("expert_results") or []
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

    web            = state.get("web_research")    or ""
    cached         = state.get("cached_facts")    or ""
    math_res       = state.get("math_result")     or ""
    mcp_res        = state.get("mcp_result")      or ""
    graph_ctx      = state.get("graph_context")   or ""
    reasoning      = state.get("reasoning_trace") or ""

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
                _gap_res = await _invoke_judge_with_retry(state,gap_prompt)
                gap_feedback_text = _gap_res.content.strip()
                # Persist refinement reason in state for causal-path logging
                state["judge_reason"] = gap_feedback_text[:500]
                state["judge_refined"] = True
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
                refined = await _refine_expert_response(_cat, cat_feedback, state)
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
                    if CORRECTION_MEMORY_ENABLED and graph_manager is not None:
                        PROM_CORRECTIONS_STORED.labels(source="judge_refinement").inc()
                        asyncio.create_task(_store_correction(
                            graph_manager.driver if hasattr(graph_manager, 'driver') else None,
                            prompt=state.get("input", "")[:500],
                            wrong=old_result[:500],
                            correct=refined[:500],
                            category=_cat,
                            source_model=state.get("judge_model_override") or "",
                            correction_source="judge_refinement",
                            tenant_id=",".join(state.get("tenant_ids", [])),
                        ))
            expert_results = new_expert_results
            if not any_improvement:
                await _report(f"⏹️ Refinement stopped: no significant improvement "
                              f"(< {JUDGE_REFINE_MIN_IMPROVEMENT:.0%})")
                break

    await _report("🔀 Merger synthesizing final response...")

    # ── 3B: Confidence-aware merger instruction ────────────────────────────
    mode     = state.get("mode", "default")
    mode_cfg = MODES.get(mode, MODES["default"])

    # Only include non-empty sections in the prompt
    sections: List[str] = [f"REQUEST: {state['input']}"]
    if reasoning:
        sections.append(f"REASONING ANALYSIS:\n{reasoning}")
    if graph_ctx:
        _gctx = graph_ctx
        # Compute per-template GraphRAG char budget: explicit override > auto from
        # judge model context window > global MAX_GRAPH_CONTEXT_CHARS.
        _judge_model = state.get("judge_model_override") or JUDGE_MODEL
        _tpl_limit = state.get("graphrag_max_chars", 0)
        # Use async context-window lookup (Redis-cache → Ollama → static table) for judge model
        from context_budget import get_model_ctx_async as _ctx_async_grag
        _judge_url = (state.get("judge_url_override") or JUDGE_URL or "").rstrip("/")
        _judge_tok = (state.get("judge_token_override") or JUDGE_TOKEN or "ollama")
        _judge_ctx = await _ctx_async_grag(model=_judge_model, base_url=_judge_url,
                                           token=_judge_tok, redis_client=redis_client)
        # Override static-table result with live value when available
        _effective_limit = graphrag_budget_chars(
            model=_judge_model,
            query_chars=len(state.get("input", "")),
            override_chars=_tpl_limit,
        )
        # If async lookup found a larger (or known) context window, recompute
        if _judge_ctx > 0 and _tpl_limit <= 0:
            from context_budget import (MERGER_FIXED_TOKENS, MERGER_HEADROOM_TOKENS,
                                        CHARS_PER_TOKEN, MIN_GRAPHRAG_CHARS)
            _q_tok = (len(state.get("input", "")) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
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
        state["graphrag_entities"] = state.get("graphrag_entities") or []
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
        _judge_model_for_web = state.get("judge_model_override") or JUDGE_MODEL
        MAX_WEB_BLOCKS, MAX_BLOCK_CHARS = web_research_budget(
            model=_judge_model_for_web,
            query_chars=len(state.get("input", "")),
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
    soft_examples = state.get("soft_cache_examples") or ""
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

    _custom_judge = (state.get("judge_prompt") or "").strip()
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
    _skill_body = state.get("output_skill_body", "")
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
    _plan_cats_early = [t.get("category", "") for t in state.get("plan", []) if isinstance(t, dict)]
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
        if len(fast_resp) > CACHE_MIN_RESPONSE_LEN and not state.get("no_cache", False):
            # Deterministic ID (SHA-256 of content) prevents duplicate entries under
            # concurrent writes — upsert is idempotent if same response races twice.
            # Skipped when no_cache=True to avoid polluting the vector store.
            _fp_cid = hashlib.sha256(fast_resp.encode()).hexdigest()[:32]
            await asyncio.to_thread(
                cache_collection.upsert,
                ids=[_fp_cid],
                documents=[fast_resp],
                metadatas=[{"ts": datetime.now().isoformat(), "input": state["input"][:200], "flagged": False, "expert_domain": _expert_domain}],
            )
            # L0: Write to query-hash cache for instant hits on identical queries
            if not state.get("no_cache") and redis_client:
                try:
                    import hashlib as _hl
                    _q_norm = re.sub(r'\s+', ' ', state["input"].lower().strip().rstrip('?!.,;'))
                    _q_hash = _hl.sha256(_q_norm.encode()).hexdigest()[:24]
                    asyncio.create_task(redis_client.setex(f"moe:qcache:{_q_hash}", 1800, fast_resp))
                except Exception:
                    pass
            asyncio.create_task(_store_response_metadata(
                state.get("response_id", ""), state["input"],
                state.get("expert_models_used", []), _fp_cid,
                plan=state.get("plan", []), cost_tier=state.get("cost_tier", "")))
            asyncio.create_task(_self_evaluate(
                state.get("response_id", ""), state["input"], fast_resp, _fp_cid,
                template_name=state.get("template_name", ""),
                complexity=state.get("complexity_level", ""),
            ))
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id": state.get("response_id", ""),
            "input":       state["input"][:300],
            "fast_path":   True,
            "ts":          datetime.now().isoformat(),
        }))
        return {"final_response": fast_resp, "prompt_tokens": 0, "completion_tokens": 0}

    await _report(f"🔀 Merger prompt ({len(prompt)} chars):\n{prompt}")
    try:
        res = await _invoke_judge_with_retry(state, prompt, temperature=state.get("query_temperature"))
    except Exception as e:
        logger.error(f"❌ Merger Judge LLM error: {e}")
        await _report(f"❌ Merger: Judge LLM unreachable ({e})")
        fallback = "\n\n".join(s for s in sections[1:] if s)  # raw sections as emergency response
        return {"final_response": fallback or "Error: Merger could not generate a response."}
    await _report(f"🔀 Merger response ({len(res.content)} chars):\n{res.content}")
    merger_usage = _extract_usage(res)
    _uid = state.get("user_id", "anon")
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

    # Strip all 【...】 citation/reference brackets leaked from tool results:
    # covers 【1†source】, 【https://arxiv.org/...】, 【n】 etc.
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
        _expert_results = state.get("expert_results") or []
        _non_leak = [r for r in _expert_results if r and not _LEAK_FALLBACK_RE.search(r)]
        _best_expert = (
            next((r for r in _non_leak if _parse_expert_confidence(r) == "high"), None)
            or (_non_leak[0] if _non_leak else None)
        )
        if _best_expert:
            res_content_clean = _best_expert.strip()
            logger.warning("⚠️ Merger output empty after strip — using best non-leak expert result as fallback")

    _no_cache_write = state.get("no_cache", False)
    if len(res_content_clean) > CACHE_MIN_RESPONSE_LEN and not _no_cache_write:
        # Deterministic ID (SHA-256 of content) prevents duplicate entries under
        # concurrent writes — upsert is idempotent if same response races twice.
        # Skipped when no_cache=True: unique benchmark/research queries would only
        # pollute the vector store with entries that are never read again.
        chroma_doc_id = hashlib.sha256(res_content_clean.encode()).hexdigest()[:32]
        await asyncio.to_thread(
            cache_collection.upsert,
            ids=[chroma_doc_id],
            documents=[res_content_clean],
            metadatas=[{"ts": datetime.now().isoformat(), "input": state["input"][:200], "flagged": False, "expert_domain": _expert_domain}],
        )
        # L0: Write to query-hash cache (30 min TTL)
        if not state.get("no_cache") and redis_client:
            try:
                import hashlib as _hl
                _q_norm = re.sub(r'\s+', ' ', state["input"].lower().strip().rstrip('?!.,;'))
                _q_hash = _hl.sha256(_q_norm.encode()).hexdigest()[:24]
                asyncio.create_task(redis_client.setex(f"moe:qcache:{_q_hash}", 1800, res_content_clean))
            except Exception:
                pass
        # Save response metadata for feedback tracking in Valkey (non-blocking)
        asyncio.create_task(
            _store_response_metadata(
                state.get("response_id", ""),
                state["input"],
                state.get("expert_models_used", []),
                chroma_doc_id,
                plan=state.get("plan", []),
                cost_tier=state.get("cost_tier", ""),
            )
        )
        # Self-evaluation via judge LLM (async, fire-and-forget — no latency overhead)
        asyncio.create_task(_self_evaluate(
            state.get("response_id", ""), state["input"], res_content_clean, chroma_doc_id,
            template_name=state.get("template_name", ""),
            complexity=state.get("complexity_level", ""),
        ))
        # Routing telemetry → PostgreSQL (async, fire-and-forget)
        asyncio.create_task(_telemetry.record_routing_decision(
            _userdb_pool, state.get("response_id", ""), state,
            wall_clock_ms=int((time.time() - state.get("_start_time", time.time())) * 1000),
        ))
        # Request-Audit-Log → Kafka moe.requests
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id":        state.get("response_id", ""),
            "input":              state["input"][:300],
            "answer":             res_content_clean[:500],
            "expert_models_used": state.get("expert_models_used", []),
            "cache_hit":          False,
            "ts":                 datetime.now().isoformat(),
        }))
        # GraphRAG Ingest → Kafka moe.ingest (consumer processes asynchronously)
        # Reuse the domain already computed above for ChromaDB metadata
        ingest_domain = _expert_domain
        # Dominant model from expert_models_used as provenance source
        _used_models = state.get("expert_models_used", [])
        _ingest_model = _used_models[0] if _used_models else JUDGE_MODEL
        # Derive confidence from expert results (high=0.9, medium=0.6, low=0.3)
        _conf_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
        _expert_confs = [
            _conf_map.get(_parse_expert_confidence(r), 0.5)
            for r in state.get("expert_results", [])
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
        _tenant_ids = state.get("tenant_ids", [])
        # Use personal namespace as ingest target so knowledge starts private.
        # The first element is always user:{id} when a real user is logged in.
        _ingest_tenant_id = _tenant_ids[0] if _tenant_ids else None
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_INGEST, {
            "response_id":      state.get("response_id", ""),
            "input":            state["input"],
            "answer":           res_content_clean,
            "domain":           ingest_domain,
            "source_expert":    ingest_domain,   # expert category for domain-isolated memory
            "source_model":     _ingest_model,
            "template_name":    state.get("template_name", ""),
            "confidence":       round(_ingest_confidence, 2),
            "knowledge_type":   _knowledge_type,
            "synthesis_insight": _synthesis_payload,  # None if no synthesis was generated
            "tenant_id":        _ingest_tenant_id,
        }))

        # Self-Correction Loop (OBJ 3): Numerical discrepancies → few-shot examples
        from self_correction import process_merger_output as _sc_process
        asyncio.create_task(_sc_process(
            query=state["input"],
            expert_results=state.get("expert_results") or [],
            final_response=res_content_clean,
            plan=state.get("plan") or [],
            redis_client=redis_client,
        ))

    # ── Agentic gap detection: assess if another iteration is needed ─────────
    _agentic_max  = state.get("agentic_max_rounds") or 0
    _agentic_iter = state.get("agentic_iteration") or 0
    _agentic_gap  = ""
    _agentic_history = list(state.get("agentic_history") or [])
    _agentic_extra: dict = {}

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
            _used_tokens = state.get("prompt_tokens", 0) + merger_usage.get("prompt_tokens", 0)

            # Expert-leak detection FIRST: capability disclaimers must override confidence gate.
            # "We need to browse." is ≤15 words and would pass the confidence gate falsely.
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
            _expert_results_combined = " ".join(state.get("expert_results") or [])
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
                for r in (state.get("expert_results") or []) if r
            )
            _answer_is_short = len(res_content_clean.split()) <= 5  # ≤5 words: single-token answers like "backtick", "Fred", "42"
            # In research mode a short answer is NOT a reliability signal — complex research
            # questions that need web lookups should still be re-checked even when compact.
            _is_research_mode = (state.get("mode") or "") == "research"
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
                    f"ORIGINAL QUESTION:\n{state['input'][:600]}\n\n"
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
                    _gap_res = await _invoke_judge_with_retry(state, _gap_prompt)
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
        _max_rounds = state.get("max_agentic_rounds", 2)
        _wm_merged: dict = dict(state.get("working_memory") or {})
        if (_agentic_gap != "COMPLETE"
                and _agentic_iter < _max_rounds - 1
                and state.get("prompt_tokens", 0) < 90_000):
            _extract_prompt = (
                "Extract the key facts from the text below as a flat JSON object "
                "{\"key\": \"value\"}. Keys must be short snake_case. "
                "Values must be concrete facts only (no opinions, no explanations). "
                "Return ONLY valid JSON, no markdown, no extra text.\n\n"
                f"TEXT:\n{res_content_clean[:500]}"
            )
            try:
                _fact_res = await _invoke_judge_with_retry(state, _extract_prompt, max_retries=1)
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


async def research_fallback_node(state: AgentState):
    """
    Runs after all parallel nodes — only when a gap-detector strategy_hint provides
    a *different* search query than already attempted. Avoids re-running identical
    SearXNG queries that the research_node already executed (which produced the
    low-confidence result in the first place).
    """
    if state.get("cache_hit"):
        return {"web_research": state.get("web_research", "")}

    expert_results   = state.get("expert_results") or []
    existing_web     = state.get("web_research") or ""
    strategy_hint    = (state.get("search_strategy_hint") or "").strip()
    attempted        = {q.get("query", "") for q in (state.get("attempted_queries") or [])}

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
    seen = {strategy_hint}

    logger.info(f"--- [NODE] RESEARCH FALLBACK (strategy: '{strategy_hint[:60]}') ---")
    await _report(f"🔍 Research fallback — new strategy: '{strategy_hint[:60]}'")

    async def _search_one(item: dict) -> str:
        query = item["query"][:180]
        cat_s = item["category"]
        await _report(f"🌐 Fallback search [{cat_s}]: '{query[:60]}'...")
        raw = await _web_search_with_citations(query, ddg_fallback=state.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG))
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


async def thinking_node(state: AgentState):
    """
    Simulates structured reasoning before synthesis.
    Activated for complex plans (>1 task) or when experts report low confidence.
    Magistral:24b generates explicit chain-of-thought that serves as context for the merger.
    """
    if state.get("cache_hit"):
        return {"reasoning_trace": ""}
    # Agent mode: skip thinking node — coding agents need low latency, not CoT
    if state.get("mode") == "agent":
        return {"reasoning_trace": ""}
    # Complexity routing: trivial/moderate requests skip thinking
    if state.get("skip_thinking"):
        logger.info("⚡ Thinking node skipped (complexity routing)")
        return {"reasoning_trace": ""}

    mode     = state.get("mode", "default")
    mode_cfg = MODES.get(mode, MODES["default"])
    force    = mode_cfg.get("force_think", False)

    plan           = state.get("plan", [])
    expert_results = state.get("expert_results") or []

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

    sections = [f"QUESTION: {state['input']}"]
    if expert_results:
        conf_summary = ", ".join(
            f"{_expert_category(r) or '?'}={_parse_expert_confidence(r)}"
            for r in expert_results
        )
        sections.append(f"EXPERT CONFIDENCE: {conf_summary}")
    if state.get("web_research"):
        sections.append(f"WEB CONTEXT (excerpt):\n{state['web_research'][:1000]}")
    if state.get("graph_context"):
        sections.append(f"GRAPH CONTEXT (excerpt):\n{state['graph_context'][:500]}")

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
        res   = await _invoke_judge_with_retry(state,reasoning_prompt)
        usage = _extract_usage(res)
        trace = res.content.strip()
        await _report(f"🧠 Reasoning result ({len(trace)} chars):\n{trace}")
        logger.info(f"🧠 Reasoning Trace: {trace[:200]}")
        return {"reasoning_trace": trace, **usage}
    except Exception as e:
        logger.warning(f"Thinking node error: {e}")
        return {"reasoning_trace": ""}


def _should_replan(state: AgentState) -> str:
    """Router: decides whether merger should loop back to planner or proceed to critic."""
    _max   = state.get("agentic_max_rounds") or 0
    _iter  = state.get("agentic_iteration") or 0
    if _max <= 0:
        return "critic"
    if _iter >= _max:
        return "critic"
    _gap = (state.get("agentic_gap") or "").strip()
    if not _gap or _gap.upper() == "COMPLETE" or _gap.lower() in ("none", ""):
        return "critic"
    # agentic_iteration is incremented in merger_node via state return — no direct mutation here.
    logger.info(f"🔄 Agentic router: iteration {_iter}/{_max}, gap='{_gap[:60]}'")
    return "planner"


async def resolve_conflicts_node(state: AgentState):
    """Evaluate the paraconsistent conflict registry and mark entries as resolved.

    Paraconsistent logic (de Vries 2007, arXiv:0707.2161, §2) tolerates
    contradictions — this node does not eliminate them but makes them explicit
    so downstream nodes (critic, agentic re-planner) can act on them.

    Resolution strategy is implemented by the user-facing TODO below.
    Until resolved, all entries remain 'pending' and are visible in the
    audit trail (conflict_registry in AgentState).
    """
    conflicts = state.get("conflict_registry") or []
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
                arb_res = await _invoke_judge_with_retry(state, arbitration_prompt)
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


async def critic_node(state: AgentState):
    """
    Fact-check for safety-critical domains (medical_consult, legal_advisor).
    Checks the merger answer for factual errors and returns a corrected version if needed.
    """
    if state.get("cache_hit"):
        return {"final_response": state.get("final_response", "")}

    plan      = state.get("plan", [])
    plan_cats = {t.get("category", "") for t in plan if isinstance(t, dict)}
    active    = plan_cats & _SAFETY_CRITICAL_CATS
    if not active:
        return {"final_response": state.get("final_response", "")}

    final_response = state.get("final_response", "")
    if not final_response or len(final_response) < 100:
        return {"final_response": final_response}

    logger.info(f"--- [NODE] CRITIC (fact-check: {active}) ---")
    await _report(f"🔎 Critic: fact-check for {', '.join(sorted(active))}...")

    critic_prompt = (
        f"You are a critical reviewer for {', '.join(sorted(active))} answers.\n"
        "Check the following answer for factual errors, dangerous statements or misleading information.\n\n"
        f"REQUEST: {state['input']}\n\n"
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
        res          = await _invoke_judge_with_retry(state,critic_prompt)
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


# --- GRAPH ---
def _route_cache(state: AgentState) -> str:
    """On cache hit go directly to merger — entire pipeline is skipped."""
    return "merger" if state.get("cache_hit") else "semantic_router"

builder = StateGraph(AgentState)
builder.add_node("cache",              cache_lookup_node)
builder.add_node("semantic_router",    semantic_router_node)
builder.add_node("planner",            planner_node)
builder.add_node("workers",            expert_worker)
builder.add_node("research",           research_node)
builder.add_node("math",               math_node_wrapper)
builder.add_node("mcp",                mcp_node)
builder.add_node("graph_rag",          graph_rag_node)
builder.add_node("research_fallback",  research_fallback_node)
builder.add_node("thinking",           thinking_node)
builder.add_node("fuzzy_router",       fuzzy_router_node)
builder.add_node("merger",             merger_node)
builder.add_node("resolve_conflicts",  resolve_conflicts_node)
builder.add_node("critic",             critic_node)

builder.set_entry_point("cache")
builder.add_conditional_edges("cache", _route_cache, {"semantic_router": "semantic_router", "merger": "merger"})
builder.add_edge("semantic_router", "planner")
builder.add_edge("planner", "fuzzy_router")
builder.add_edge("fuzzy_router", "workers")
builder.add_edge("fuzzy_router", "research")
builder.add_edge("fuzzy_router", "math")
builder.add_edge("fuzzy_router", "mcp")
builder.add_edge("fuzzy_router", "graph_rag")
builder.add_edge(["workers", "research", "math", "mcp", "graph_rag"], "research_fallback")
builder.add_edge("research_fallback", "thinking")
builder.add_edge("thinking", "merger")
builder.add_conditional_edges(
    "merger",
    _should_replan,
    {"planner": "planner", "critic": "resolve_conflicts"},
)
builder.add_edge("resolve_conflicts", "critic")
builder.add_edge("critic", END)

# --- SERVER ---
app_graph = None

async def _init_graph_rag() -> None:
    """Initialize the Neo4j GraphRAG Manager with retry logic.

    Skipped immediately when NEO4J_URI or NEO4J_PASS is empty — this is the
    intended state for lightweight deployments that don't include Neo4j.
    """
    global graph_manager
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
    global MCP_TOOLS_DESCRIPTION, AGENTIC_CODE_TOOLS_DESCRIPTION, MCP_TOOL_SCHEMAS, _MCP_TOOLS_DICT
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
                    _MCP_TOOLS_DICT[t['name']] = t['description']
                # Store schema for pre-call validation
                MCP_TOOL_SCHEMAS[t["name"]] = {
                    "required": t.get("required_args", t.get("required", [])),
                    "args": t.get("args", t.get("parameters", {})),
                }
            MCP_TOOLS_DESCRIPTION = "\n".join(general_lines)
            AGENTIC_CODE_TOOLS_DESCRIPTION = "\n".join(agentic_lines)
            logger.info(
                f"✅ MCP server: {len(tools)} tools loaded ({len(agentic_lines)} code-nav exclusive)"
            )
    except Exception as e:
        logger.warning(f"⚠️ MCP server unreachable ({e}) — planner without tool descriptions")
        MCP_TOOLS_DESCRIPTION = (
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
        AGENTIC_CODE_TOOLS_DESCRIPTION = (
            "  - repo_map: AST/regex skeleton of a repo (file paths + classes/functions)\n"
            "  - read_file_chunked: Paginated file reading (start_line/end_line, max 200 lines)\n"
            "  - lsp_query: Python LSP features: signature, find_references, completions (.py only)"
        )


async def _init_kafka() -> None:
    """Start the Kafka producer with retry logic."""
    global kafka_producer
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
    global _enterprise_reachable
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
    _enterprise_reachable = reachable_count > 0
    import state as _state; _state._enterprise_reachable = _enterprise_reachable
    if _enterprise_reachable:
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
            if graph_manager is not None:
                try:
                    stats = await graph_manager.get_stats()
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
            if redis_client is not None:
                try:
                    PROM_PLANNER_PATS.set(await redis_client.zcard("moe:planner_success"))
                    PROM_ONTOLOGY_GAPS.set(await redis_client.zcard("moe:ontology_gaps"))
                    active_keys = await redis_client.keys("moe:active:*")
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
                                        if redis_client and _current_names:
                                            try:
                                                _skip = False
                                                _blk = await redis_client.sismember("moe:blocked_servers", _sname)
                                                _fld = await redis_client.sismember("moe:floating_disabled_servers", _sname)
                                                _skip = bool(_blk or _fld)
                                                if not _skip:
                                                    _now = time.time()
                                                    for _mn in _current_names:
                                                        _reg_key = f"moe:model_registry:{_mn.split(':')[0]}"
                                                        await redis_client.zadd(_reg_key, {_sname: _now})
                                                        await redis_client.expire(_reg_key, 120)  # 2 min TTL
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


@asynccontextmanager
async def lifespan(app_: FastAPI):
    import state as _state
    global app_graph, redis_client, _userdb_pool
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    _state.redis_client = redis_client  # expose to route modules via state.*
    logger.info("✅ Valkey client initialized")
    # moe_userdb pool: opened lazily, fails open so SQL-less startup is possible for tests
    try:
        _userdb_pool = AsyncConnectionPool(
            MOE_USERDB_URL,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"autocommit": False},
        )
        await _userdb_pool.open()
        await _userdb_pool.wait()
        logger.info("✅ moe_userdb pool verbunden (%s)", MOE_USERDB_URL.split("@")[-1])
    except Exception as e:
        logger.warning("moe_userdb pool nicht verbunden: %s", e)
        _userdb_pool = None
    _state._userdb_pool = _userdb_pool
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
    _stale_keys = await redis_client.keys("moe:active:*")
    if _stale_keys:
        await redis_client.delete(*_stale_keys)
        logger.info(f"🧹 {len(_stale_keys)} orphaned moe:active:* keys deleted on startup")
    # Initialize semaphores in event loop context
    await _init_semaphores()
    # Ensure skill registry schema and populate from filesystem
    await _ensure_skill_registry_schema()
    await _bootstrap_skill_registry()
    # Ensure causal-path columns exist in routing_telemetry
    await _telemetry.ensure_causal_columns(_userdb_pool)
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
            redis_client=redis_client,
            inference_servers=INFERENCE_SERVERS_LIST,
            kafka_producer=kafka_producer,
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
    if kafka_producer is not None:
        await kafka_producer.stop()
        logger.info("🔌 Kafka Producer stopped")
    if graph_manager is not None:
        await graph_manager.close()
        logger.info("🔌 Neo4j connection closed")
    if redis_client is not None:
        await redis_client.aclose()
        logger.info("🔌 Valkey client closed")
    if _userdb_pool is not None:
        await _userdb_pool.close()
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

async def _check_ip_rate_limit(request: Request) -> bool:
    """Token-bucket rate limit per client IP using Redis.

    Returns True if the request is allowed, False if rate-limited.
    Limit: MAX_REQUESTS_PER_MINUTE per IP (default 60). Unauthenticated
    requests use a stricter limit (default 20/min) to slow credential bruteforce.
    """
    if redis_client is None:
        return True  # fails open — no Redis, no rate limiting
    try:
        _ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.headers.get("x-real-ip", "")
            or (request.client.host if request.client else "unknown")
        )
        _window = 60  # seconds
        _key    = f"moe:ratelimit:ip:{_ip}"
        _limit  = MAX_REQUESTS_PER_MINUTE  # from config.py
        _count  = await redis_client.incr(_key)
        if _count == 1:
            await redis_client.expire(_key, _window)
        return _count <= _limit
    except Exception:
        return True  # fails open on Redis errors


# /health, /metrics → routes/health.py
# /api/watchdog/*, /api/starfleet/features → routes/watchdog.py

# ── Starfleet: Watchdog Alerts (moved to routes/watchdog.py) ─────────────────

async def starfleet_features_endpoint():
    """Return current state of all Starfleet feature toggles."""
    return await _starfleet.get_all_feature_states(redis_client)


async def watchdog_config_set(request: Request):
    """Merge-update watchdog configuration (hot-reload, no restart needed)."""
    patch = await request.json()
    return await _watchdog.save_config(redis_client, patch)


async def watchdog_node_status():
    """Return real-time per-node status via direct live health checks (3 s timeout).

    Results are cached in Valkey for 20 s so rapid dashboard refreshes don't
    hammer the inference nodes. Combines liveness from /api/tags (Ollama) or
    /models (OpenAI-compat) with VRAM data from the Prometheus gauges.
    """
    _CACHE_KEY = "moe:watchdog:node_status_cache"
    _CACHE_TTL = 20

    # Return cached result if fresh enough.
    if redis_client is not None:
        try:
            cached = await redis_client.get(_CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    results = []

    async def _check_node(srv: dict) -> dict:
        name      = srv["name"]
        url       = srv.get("url", "").rstrip("/")
        api_type  = srv.get("api_type", "ollama")
        token     = srv.get("token", "")
        vram_gb   = int(srv.get("vram_gb", 0))
        up        = False
        models_available = 0
        models_loaded    = 0
        vram_used_gb     = 0.0

        # Build auth header — Ollama uses "ollama" as a dummy token (no real auth),
        # OpenAI-compat endpoints require a real Bearer token.
        headers = {}
        if token and api_type != "ollama":
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with httpx.AsyncClient(timeout=3.0, headers=headers) as hc:
                if api_type == "ollama":
                    base = url[:-3] if url.endswith("/v1") else url
                    r = await hc.get(f"{base}/api/tags")
                    if r.status_code == 200:
                        up = True
                        models_available = len(r.json().get("models", []))
                    try:
                        rps = await hc.get(f"{base}/api/ps")
                        if rps.status_code == 200:
                            loaded = rps.json().get("models") or []
                            models_loaded = len(loaded)
                            vram_used_gb  = round(sum(
                                int(m.get("size_vram") or 0) for m in loaded
                            ) / 1e9, 1)
                    except Exception:
                        pass
                else:
                    # OpenAI-compatible: /models requires Bearer auth.
                    r = await hc.get(f"{url}/models")
                    if r.status_code == 200:
                        up = True
                        models_available = len(r.json().get("data", []))
                    elif r.status_code in (401, 403) and not token:
                        # Has an endpoint but no token configured — treat as unknown.
                        up = None
        except Exception:
            pass

        vram_pct = round(vram_used_gb / vram_gb * 100, 1) if vram_gb > 0 else None
        # up: True=online, False=offline, None=unknown (no token / unreachable)
        return {
            "name":             name,
            "api_type":         api_type,
            "up":               up,
            "models_available": models_available,
            "models_loaded":    models_loaded,
            "vram_used_gb":     vram_used_gb,
            "vram_total_gb":    vram_gb,
            "vram_pct":         vram_pct,
            "source":           "admin",
        }

    results = await asyncio.gather(*[_check_node(s) for s in INFERENCE_SERVERS_LIST])
    payload  = {"nodes": list(results), "live": True, "cache_ttl_seconds": _CACHE_TTL}

    if redis_client is not None:
        try:
            await redis_client.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(payload))
        except Exception:
            pass

    return payload


# ── Starfleet: Mission Context ────────────────────────────────────────────────

async def mission_context_set(request: Request):
    """Replace the mission context with the provided JSON body."""
    if not await _starfleet.is_feature_enabled("mission_context", redis_client):
        return JSONResponse({"enabled": False}, status_code=409)
    data = await request.json()
    return await _mission_context.set_context(data)


async def prometheus_metrics():
    """Prometheus scrape endpoint — returns all moe_* metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# _CLAUDE_PRETTY_NAMES, _model_display_name imported from config.py
from config import _CLAUDE_PRETTY_NAMES, _model_display_name


async def graph_stats():
    if graph_manager is None:
        return {"status": "unavailable"}
    stats = await graph_manager.get_stats()
    return {"status": "ok", **stats}

async def graph_knowledge_export(
    domains: Optional[str] = None,
    min_trust: float = 0.3,
    include_syntheses: bool = True,
    strip_sensitive: bool = True,
):
    """Export knowledge graph as a community-shareable JSON-LD bundle."""
    if graph_manager is None:
        return JSONResponse(status_code=503, content={"error": "GraphRAG unavailable"})
    domain_list = [d.strip() for d in domains.split(",") if d.strip()] if domains else None
    bundle = await graph_manager.export_knowledge_bundle(
        domains=domain_list,
        min_trust=min_trust,
        include_syntheses=include_syntheses,
        strip_sensitive=strip_sensitive,
    )
    filename = f"moe-knowledge-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return Response(
        content=json.dumps(bundle, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def graph_knowledge_validate(raw_request: Request):
    """Dry-run import to preview what would be imported."""
    if graph_manager is None:
        return JSONResponse(status_code=503, content={"error": "GraphRAG unavailable"})
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    bundle = body.get("bundle", body)
    stats = await graph_manager.import_knowledge_bundle(
        bundle=bundle, dry_run=True,
    )
    return {"status": "ok", "dry_run": True, **stats}


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

class MemoryIngestRequest(BaseModel):
    """Request body for the /v1/memory/ingest endpoint."""
    session_summary: str
    key_decisions: List[str] = []
    domain: str = "session"
    source_model: str = "claude-code-hook"
    confidence: float = 0.8


async def memory_ingest(request: Request, body: MemoryIngestRequest):
    """
    Accepts a session summary from external hooks (e.g., Claude Code) and
    enqueues it for async GraphRAG ingest via the moe.ingest Kafka topic.
    Requires a valid API key (Authorization: Bearer <key>).
    """
    combined_answer = body.session_summary
    if body.key_decisions:
        combined_answer += "\n\nKey decisions:\n" + "\n".join(f"- {d}" for d in body.key_decisions)
    asyncio.create_task(_kafka_publish(KAFKA_TOPIC_INGEST, {
        "input":            "Claude Code session summary",
        "answer":           combined_answer[:4000],
        "domain":           body.domain,
        "source_expert":    "session",
        "source_model":     body.source_model,
        "confidence":       min(max(float(body.confidence), 0.0), 1.0),
        "knowledge_type":   "factual",
        "synthesis_insight": None,
    }))
    logger.info(f"💾 memory_ingest queued: domain={body.domain} len={len(combined_answer)}")
    return {"status": "queued", "domain": body.domain, "length": len(combined_answer)}


# _oai_content_to_str — see parsing.py


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



    payload: dict = {
        "model":      effective_model,
        "messages":   oai_messages,
        "stream":     False,        # collect tool calls completely, then convert
        "max_tokens": max_tokens,
    }
    if oai_tools:
        payload["tools"] = oai_tools
        # Guard: if the last message is a tool_result (synthesis turn), don't force tool_use
        _has_tool_results = any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "tool_result" for b in m.get("content", []))
            for m in messages
        )
        _effective_tool_choice = (
            "auto" if (_has_tool_results and not tools) else CLAUDE_CODE_TOOL_CHOICE
        )
        payload["tool_choice"] = _effective_tool_choice
    else:
        _effective_tool_choice = "auto"

    # Pre-check: if this endpoint is known to be rate-limited, fail fast instead of timing out.
    # This prevents Claude Code CLI from making 10 retry attempts and risking a DDoS ban.
    _tool_ep = CLAUDE_CODE_TOOL_ENDPOINT if (effective_url == _CLAUDE_CODE_TOOL_URL) else None
    if _tool_ep and _check_rate_limit_exhausted(_tool_ep):
        _rl_entry = _provider_rate_limits.get(_tool_ep, {})
        _reset_str = ""
        if _rl_entry.get("reset_time"):
            import datetime as _dt
            _reset_str = f" Reset: {_dt.datetime.fromtimestamp(_rl_entry['reset_time']).strftime('%H:%M:%S')}."
        _err_body = {"type": "error", "error": {"type": "overloaded_error",
            "message": f"Provider token limit exhausted.{_reset_str} Remaining: {_rl_entry.get('remaining_tokens', 0)}"}}
        asyncio.create_task(_deregister_active_request(chat_id))
        if do_stream:
            async def _rl_err_stream():
                yield f"data: {json.dumps(_err_body)}\n\n"
            return StreamingResponse(_rl_err_stream(), media_type="text/event-stream", status_code=529)
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(content=_err_body, status_code=529)

    _llm_t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_node_timeout) as client:
            resp = await client.post(
                f"{effective_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {effective_token}"}
            )
            # Parse and cache rate limit headers from the provider response
            if _tool_ep:
                _update_rate_limit_headers(_tool_ep, resp.headers, resp.status_code)
            if resp.status_code == 429:
                _rl_entry = _provider_rate_limits.get(_tool_ep or "", {})
                _reset_str = ""
                if _rl_entry.get("reset_time"):
                    import datetime as _dt
                    _reset_str = f" Reset: {_dt.datetime.fromtimestamp(_rl_entry['reset_time']).strftime('%H:%M:%S')}."
                _err_body = {"type": "error", "error": {"type": "overloaded_error",
                    "message": f"Provider Rate-Limit (429).{_reset_str}"}}
                asyncio.create_task(_deregister_active_request(chat_id))
                from fastapi.responses import JSONResponse as _JSONResponse
                return _JSONResponse(content=_err_body, status_code=529)
            resp.raise_for_status()
            oai_resp = resp.json()
    except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError) as _tex:
        _llm_latency = time.monotonic() - _llm_t0
        PROM_TOOL_TIMEOUTS.labels(node=effective_node, model=effective_model).inc()
        PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="llm_call").observe(_llm_latency)
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "timeout",
            "timeout_s": _node_timeout, "elapsed_s": round(_llm_latency, 3),
        }))
        logger.warning(f"⏱️ Tool handler timeout after {_llm_latency:.1f}s on {effective_node} (limit={_node_timeout}s)")
        # Return valid Anthropic response — Claude Code can abort or restart the prompt
        _timeout_text = (
            f"⚠️ The inference server '{effective_node}' did not respond within "
            f"{int(_node_timeout)}s. Please simplify the prompt or "
            f"choose a faster server."
        )
        _timeout_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _timeout_text}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _timeout_text}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return _timeout_resp
    except httpx.HTTPStatusError as _hex:
        _llm_latency = time.monotonic() - _llm_t0
        _status_code = _hex.response.status_code
        logger.warning(f"⚠️ Tool handler HTTP error {_status_code} from {effective_node}: {_hex}")
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "http_error",
            "status_code": _status_code, "elapsed_s": round(_llm_latency, 3),
        }))
        _err_text = (
            f"⚠️ The inference server '{effective_node}' returned an error "
            f"(HTTP {_status_code}). Please try again."
        )
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _err_text}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }

    _llm_latency = time.monotonic() - _llm_t0
    PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="llm_call").observe(_llm_latency)

    choice = oai_resp["choices"][0]
    msg    = choice["message"]
    usage  = oai_resp.get("usage", {})
    in_tok  = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=effective_model, moe_mode="cc_tool",
            prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
        if not is_user_conn:
            asyncio.create_task(_increment_user_budget(user_id, in_tok + out_tok, prompt_tokens=in_tok, completion_tokens=out_tok))

    # Format detection: raw Qwen tags in text content indicate missing tool schema
    _raw_text = msg.get("content") or ""
    _has_qwen_tags = "<|invoke|>" in _raw_text or "<|plugin|>" in _raw_text
    _has_tool_calls = bool(msg.get("tool_calls"))
    _format_detected = "unknown"

    # Fallback detection: some models (e.g. Gemma, Mistral) return tool calls as
    # JSON in the content field instead of the tool_calls field.
    # Detection: content is valid JSON with "name"+"arguments"/"parameters"/"input" keys
    # and the name matches a known tool — then synthesize into tool_calls.
    if not _has_tool_calls and not _has_qwen_tags and _raw_text and tools:
        _known_tool_names = {t.get("name", "") for t in tools}
        _extracted_tcs: list = []
        # Strip optional markdown code fences
        _probe = re.sub(r"^```(?:json)?\s*|\s*```$", "", _raw_text.strip(), flags=re.DOTALL).strip()
        _json_candidates: list = []
        try:
            _parsed = json.loads(_probe)
            _json_candidates = [_parsed] if isinstance(_parsed, dict) else (_parsed if isinstance(_parsed, list) else [])
        except (json.JSONDecodeError, ValueError):
            # Try to find individual JSON objects (handles trailing text)
            for _m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', _probe):
                try:
                    _json_candidates.append(json.loads(_m.group()))
                except Exception:
                    pass
        for _cand in _json_candidates:
            if not isinstance(_cand, dict):
                continue
            _tc_name = _cand.get("name") or _cand.get("tool") or _cand.get("function")
            _tc_args = _cand.get("arguments") or _cand.get("parameters") or _cand.get("input") or {}
            if _tc_name and _tc_name in _known_tool_names:
                _extracted_tcs.append({
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": _tc_name,
                        "arguments": json.dumps(_tc_args) if isinstance(_tc_args, dict) else str(_tc_args),
                    },
                })
        if _extracted_tcs:
            msg["tool_calls"] = _extracted_tcs
            msg["content"] = None   # No text — tool call only
            _has_tool_calls = True
            _format_detected = "json_in_text"
            PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
            PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="json_in_text").inc()
            _tool_eval_logger.warning(json.dumps({
                "ts": datetime.utcnow().isoformat() + "Z",
                "chat_id": chat_id, "model": effective_model, "node": effective_node,
                "phase": "tool_call", "event": "format_recovered", "format": "json_in_text",
                "tools": [t["function"]["name"] for t in _extracted_tcs],
                "snippet": _raw_text[:200],
            }))
            logger.info(f"🔧 JSON-in-text tool call detected and converted: {[t['function']['name'] for t in _extracted_tcs]}")

    if _has_qwen_tags:
        _format_detected = "qwen_raw"
        PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="qwen_raw").inc()
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "format_error", "format": "qwen_raw",
            "snippet": _raw_text[:200],
        }))
        logger.warning(f"⚠️ Qwen raw format detected on {effective_node} — tools were not passed correctly!")
    elif _has_tool_calls and not (_format_detected == "json_in_text"):
        _format_detected = "json_tool_use"
        PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
    elif not _has_tool_calls:
        _format_detected = "text_only" if _raw_text else "empty"
        if not _raw_text:
            PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="empty").inc()

    PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="total").observe(time.monotonic() - _eval_t0)

    # OpenAI-Response → Anthropic Content-Blocks
    content_blocks: list = []
    if msg.get("content"):
        content_blocks.append({"type": "text", "text": msg["content"]})
    stop_reason = "end_turn"
    if msg.get("tool_calls"):
        stop_reason = "tool_use"
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                args = {}
            content_blocks.append({
                "type":  "tool_use",
                "id":    tc["id"],
                "name":  fn.get("name", "unknown"),
                "input": args
            })
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    # Tool-eval structured log
    _log_tool_eval({
        "ts":              datetime.utcnow().isoformat() + "Z",
        "chat_id":         chat_id,
        "model":           effective_model,
        "node":            effective_node,
        "input_type":      _last_msg_type,
        "tools_available": len(tools),
        "output_type":     stop_reason,
        "tools_called":    [b["name"] for b in content_blocks if b.get("type") == "tool_use"],
        "tool_call_count": sum(1 for b in content_blocks if b.get("type") == "tool_use"),
        "has_text":        any(b["type"] == "text" and b.get("text") for b in content_blocks),
        "latency_s":       round(time.monotonic() - _eval_t0, 3),
        "llm_latency_s":   round(_llm_latency, 3),
        "input_tokens":    in_tok,
        "output_tokens":   out_tok,
        "tool_choice_sent": _effective_tool_choice,
        "format_detected": _format_detected,
    })

    asyncio.create_task(_deregister_active_request(chat_id))
    if not do_stream:
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": content_blocks, "model": model_id,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok}
        }

    return StreamingResponse(
        _anthropic_content_blocks_to_sse(
            content_blocks, chat_id, model_id, in_tok, out_tok, stop_reason
        ),
        media_type="text/event-stream"
    )


async def _anthropic_reasoning_handler(body: dict, chat_id: str, user_id: str = "anon", api_key_id: str = "", session_id: str = None):
    """Text requests via reasoning expert (deepseek-r1/qwq) with <think> parsing.

    Returns responses in Anthropic Extended Thinking format with optional
    thinking block when the model outputs <think>...</think> tags.
    """
    model_id  = body.get("model", "claude-sonnet-4-6")
    messages  = body.get("messages", [])
    system    = body.get("system") or ""
    do_stream = body.get("stream", False)

    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        empty_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        if do_stream:
            async def _empty():
                async for chunk in _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": ""}], chat_id, model_id, 0, 0, "end_turn"
                ):
                    yield chunk
            return StreamingResponse(_empty(), media_type="text/event-stream")
        return empty_resp

    oai_messages = _anthropic_to_openai_messages(messages, system)

    # Determine model/node — explicit override takes precedence over dynamic selection
    _reasoning_node_name = "unknown"
    if CLAUDE_CODE_REASONING_MODEL and _CLAUDE_CODE_REASONING_URL:
        reasoning_model = CLAUDE_CODE_REASONING_MODEL
        reasoning_url   = _CLAUDE_CODE_REASONING_URL
        reasoning_token = "ollama"
        _reasoning_node_name = CLAUDE_CODE_REASONING_ENDPOINT or "unknown"
        _reasoning_timeout = float(_server_info(_reasoning_node_name).get("timeout", EXPERT_TIMEOUT))
        logger.info(f"🧠 Reasoning: Override {reasoning_model} @ {CLAUDE_CODE_REASONING_ENDPOINT}")
    else:
        reasoning_experts = EXPERTS.get("reasoning", [])
        if reasoning_experts:
            scored = []
            for e in reasoning_experts:
                score = await _get_expert_score(e["model"], "reasoning")
                scored.append((score, e))
            scored.sort(key=lambda x: -x[0])
            best = scored[0][1]
            reasoning_model = best["model"]
            node = await _select_node(reasoning_model, best.get("endpoints") or [best.get("endpoint", "")])
            reasoning_url   = node.get("url") or URL_MAP.get(node["name"])
            reasoning_token = node.get("token", "ollama")
            _reasoning_node_name = node.get("name", "unknown")
            _reasoning_timeout = float(node.get("timeout", EXPERT_TIMEOUT))
            logger.info(f"🧠 Reasoning: Dynamic {reasoning_model} @ {node.get('name','?')} (score={scored[0][0]:.2f})")
        else:
            reasoning_model = CLAUDE_CODE_TOOL_MODEL
            reasoning_url   = _CLAUDE_CODE_TOOL_URL
            reasoning_token = "ollama"
            _reasoning_node_name = CLAUDE_CODE_TOOL_ENDPOINT or "unknown"
            _reasoning_timeout = float(_server_info(_reasoning_node_name).get("timeout", EXPERT_TIMEOUT))
            logger.info(f"🧠 Reasoning: Fallback to tool model {reasoning_model}")

    payload = {
        "model":      reasoning_model,
        "messages":   oai_messages,
        "stream":     False,
        "max_tokens": body.get("max_tokens", REASONING_MAX_TOKENS),
    }
    try:
        async with httpx.AsyncClient(timeout=_reasoning_timeout) as client:
            resp = await client.post(
                f"{reasoning_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {reasoning_token}"}
            )
            resp.raise_for_status()
            oai_resp = resp.json()
    except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError):
        asyncio.create_task(_deregister_active_request(chat_id))
        _err_text = f"⚠️ The reasoning server '{_reasoning_node_name}' did not respond in time."
        if do_stream:
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}], chat_id, model_id, 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return {"id": chat_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": _err_text}], "model": model_id,
                "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
    except httpx.HTTPStatusError as _hex:
        asyncio.create_task(_deregister_active_request(chat_id))
        _err_text = f"⚠️ The reasoning server '{_reasoning_node_name}' returned HTTP {_hex.response.status_code}."
        if do_stream:
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}], chat_id, model_id, 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return {"id": chat_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": _err_text}], "model": model_id,
                "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}

    raw   = oai_resp["choices"][0]["message"].get("content", "") or ""
    usage = oai_resp.get("usage", {})
    in_tok  = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=reasoning_model, moe_mode="cc_reasoning",
            prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
        asyncio.create_task(_increment_user_budget(user_id, in_tok + out_tok, prompt_tokens=in_tok, completion_tokens=out_tok))

    # Parse <think>...</think> blocks (deepseek-r1, qwq)
    think_match = re.search(r'<think>(.*?)</think>', raw, re.S)
    if think_match:
        thinking_text = think_match.group(1).strip()
        answer_text   = raw[think_match.end():].strip()
    else:
        thinking_text = ""
        answer_text   = raw.strip()

    content_blocks = []
    if thinking_text:
        content_blocks.append({"type": "thinking", "thinking": thinking_text})
    content_blocks.append({"type": "text", "text": answer_text or ""})

    asyncio.create_task(_deregister_active_request(chat_id))
    if not do_stream:
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": content_blocks, "model": model_id,
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok}
        }

    return StreamingResponse(
        _anthropic_content_blocks_to_sse(content_blocks, chat_id, model_id, in_tok, out_tok, "end_turn"),
        media_type="text/event-stream"
    )


async def _anthropic_moe_handler(body: dict, chat_id: str,
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
                                  user_id: str = "anon",
                                  api_key_id: str = "",
                                  session_id: str = None,
                                  enable_semantic_memory: bool = False,
                                  cross_session_enabled: bool = False,
                                  cross_session_scopes: Optional[List[str]] = None):
    """Route pure text requests through the MoE agent pipeline."""
    model_id   = body.get("model", "moe-orchestrator-agent")
    messages   = body.get("messages", [])
    system     = body.get("system") or ""
    do_stream  = body.get("stream", False)

    # Last user message as pipeline input
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        empty_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        if do_stream:
            async def _empty():
                async for chunk in _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": ""}], chat_id, model_id, 0, 0, "end_turn"
                ):
                    yield chunk
            return StreamingResponse(_empty(), media_type="text/event-stream")
        return empty_resp

    last_user_content = last_user.get("content", "")
    allowed_skills = (user_permissions or {}).get("skill")
    _raw_cc_input = _anthropic_content_to_text(last_user_content)
    user_input  = await _resolve_skill_secure(_raw_cc_input, allowed_skills, user_id=user_id, session_id=session_id)
    _cc_pending_reports: List[str] = []
    if user_input != _raw_cc_input:
        _csm = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)", _raw_cc_input)
        _csname = _csm.group(1) if _csm else "?"
        _csargs = _raw_cc_input[len(_csname)+1:].strip() if _csm else ""
        _cc_pending_reports.append(
            f"🎯 Skill /{_csname} resolved (args: '{_csargs}', {len(user_input)} chars):\n{user_input}"
        )
    user_images = _extract_images(last_user_content)
    history_raw = [
        {"role": m["role"],
         "content": _anthropic_content_to_text(m.get("content", ""))}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant")
    ]
    history = _truncate_history(history_raw)
    _sm_team_ids_h: List[str] = []
    _sm_prefs_h:   dict      = {}
    if enable_semantic_memory and user_id and user_id != "anon":
        try:
            from admin_ui.database import (
                get_user_teams        as _gut_h,
                get_user_memory_prefs as _gmp_h,
            )
            if cross_session_enabled:
                _sm_team_ids_h = await _gut_h(user_id)
            _sm_prefs_h = await _gmp_h(user_id)
        except Exception:
            pass
    history = await _apply_semantic_memory(
        history_raw, history, user_input, session_id,
        enabled=enable_semantic_memory,
        user_id=user_id,
        team_ids=_sm_team_ids_h,
        cross_session_enabled=cross_session_enabled,
        cross_session_scopes=cross_session_scopes or ["private"],
        n_results=0,   # anthropic handler: use template default (passed via caller)
        ttl_hours=0,
        prefer_fresh=_sm_prefs_h.get("prefer_fresh", False),
        share_with_team=_sm_prefs_h.get("share_with_team", False),
    )

    invoke_state = {
        "input": user_input, "response_id": chat_id,
        "mode": "agent_orchestrated" if CLAUDE_CODE_MODE == "moe_orchestrated" else "agent",
        "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
        "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
        "chat_history": history, "reasoning_trace": "", "system_prompt": system,
        "images": user_images,
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
        "template_name":          body.get("model", "") or "",
        "pending_reports": _cc_pending_reports,
    }
    invoke_cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}

    if do_stream:
        async def _moe_stream():
            _did_deregister = False
            try:
                # Set up progress queue so _report() calls don't hang
                progress_q: asyncio.Queue = asyncio.Queue()
                ctx_token = _progress_queue.set(progress_q)
                result_box: dict = {}

                async def _run():
                    try:
                        result_box["data"] = await app_graph.ainvoke(invoke_state, invoke_cfg)
                    except Exception as e:
                        result_box["error"] = str(e)
                    finally:
                        await progress_q.put(None)

                asyncio.create_task(_run())

                # Immediately send message_start + content_block_start
                # → client sees response start, keeps connection open
                yield _sse_event("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": chat_id, "type": "message", "role": "assistant",
                        "content": [], "model": model_id,
                        "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 1}
                    }
                })
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}
                })
                yield _sse_event("ping", {"type": "ping"})

                # Wait for pipeline end; stream progress messages as text when _CC_STREAM_THINK is set
                while True:
                    try:
                        msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                        if msg is None:
                            break
                        if _CC_STREAM_THINK and msg:
                            think_chunk = f"\n{msg}\n"
                            yield _sse_event("content_block_delta", {
                                "type": "content_block_delta", "index": 0,
                                "delta": {"type": "text_delta", "text": think_chunk}
                            })
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"

                _progress_queue.reset(ctx_token)

                # Stream result
                data    = result_box.get("data", {})
                content = data.get("final_response", "") if "data" in result_box \
                          else f"Error: {result_box.get('error', 'Unknown')}"
                in_tok  = data.get("prompt_tokens", 0)
                out_tok = data.get("completion_tokens", 0)

                if user_id != "anon":
                    asyncio.create_task(_log_usage_to_db(
                        user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                        model="moe-orchestrator", moe_mode="cc_moe",
                        prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
                    _uc_p = data.get("user_conn_prompt_tokens", 0)
                    _uc_c = data.get("user_conn_completion_tokens", 0)
                    _bill_p = max(0, in_tok - _uc_p)
                    _bill_c = max(0, out_tok - _uc_c)
                    asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))

                for i in range(0, max(len(content), 1), SSE_CHUNK_SIZE):
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": content[i:i+SSE_CHUNK_SIZE]}
                    })
                    await asyncio.sleep(0.005)

                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
                yield _sse_event("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": out_tok}
                })
                yield _sse_event("message_stop", {"type": "message_stop"})
                asyncio.create_task(_deregister_active_request(chat_id))
                _did_deregister = True
            finally:
                if not _did_deregister:
                    asyncio.create_task(_deregister_active_request(chat_id))

        return StreamingResponse(_moe_stream(), media_type="text/event-stream")

    # Non-Streaming
    result  = await app_graph.ainvoke(invoke_state, invoke_cfg)
    content = result.get("final_response", "")
    _p_tok = result.get("prompt_tokens", 0)
    _c_tok = result.get("completion_tokens", 0)
    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model="moe-orchestrator", moe_mode="cc_moe",
            prompt_tokens=_p_tok, completion_tokens=_c_tok, session_id=session_id))
        _uc_p = result.get("user_conn_prompt_tokens", 0)
        _uc_c = result.get("user_conn_completion_tokens", 0)
        _bill_p = max(0, _p_tok - _uc_p)
        _bill_c = max(0, _c_tok - _uc_c)
        asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))
    asyncio.create_task(_deregister_active_request(chat_id))
    return {
        "id": chat_id, "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {
            "input_tokens":  _p_tok,
            "output_tokens": _c_tok
        }
    }


async def anthropic_messages(request: Request):
    """Anthropic Messages API — drop-in compatible with Claude Code CLI and Anthropic SDK.

    Routing:
    - Claude Code sessions (claude-* model ID):
        - tool_use / tool_result  → devstral:24b (code specialist, robust function calling)
        - pure text requests      → MoE agent pipeline (mode=agent)
    - Standard MoE sessions:
        - tools / tool_results    → judge_llm (magistral:24b)
        - pure text requests      → MoE agent pipeline

    Configuration for Claude Code:
        ANTHROPIC_BASE_URL=http://<server>:8002
        ANTHROPIC_API_KEY=<any>        # not validated
        CLAUDE_MODEL=claude-sonnet-4-6  (or other claude-* ID)
    """
    body       = await request.json()
    chat_id    = f"msg_{uuid.uuid4().hex[:24]}"
    session_id = _extract_session_id(request)
    messages   = body.get("messages", [])
    tools      = body.get("tools", [])
    model     = body.get("model", "")

    # Auth
    raw_key  = _extract_api_key(request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        if user_ctx["error"] == "budget_exceeded":
            return JSONResponse(status_code=429, content={"error": {
                "message": f"Budget exceeded ({user_ctx.get('limit_type', 'unknown')} limit reached)",
                "type": "insufficient_quota", "code": "budget_exceeded"
            }})
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key", "type": "invalid_request_error", "code": "invalid_api_key"
        }})
    _user_id      = user_ctx.get("user_id", "anon")
    _api_key_id   = user_ctx.get("key_id", "")
    _user_perms   = json.loads(user_ctx.get("permissions_json", "{}"))
    _user_tmpls_json2   = user_ctx.get("user_templates_json", "{}")
    _user_conns_json2   = user_ctx.get("user_connections_json", "{}")
    _user_experts = _resolve_user_experts(user_ctx.get("permissions_json", ""),
                                           user_templates_json=_user_tmpls_json2,
                                           user_connections_json=_user_conns_json2) or {}
    _user_tmpl_prompts = _resolve_template_prompts(user_ctx.get("permissions_json", ""),
                                                    user_templates_json=_user_tmpls_json2,
                                                    user_connections_json=_user_conns_json2)

    # ─── Resolve per-user CC profile ──────────────────────────────────────────
    # Priority: 1. key-specific profile  2. user default  3. first available
    _cc_profile_ids = _user_perms.get("cc_profile", [])
    _user_cc_profiles_json = user_ctx.get("user_cc_profiles_json", "{}")
    _user_cc_map: dict = {}
    try:
        _user_cc_map = json.loads(_user_cc_profiles_json or "{}")
    except Exception:
        pass
    _effective_cc_mode       = CLAUDE_CODE_MODE
    _effective_tool_model    = CLAUDE_CODE_TOOL_MODEL
    _effective_tool_endpoint = CLAUDE_CODE_TOOL_ENDPOINT
    _effective_tool_url      = _CLAUDE_CODE_TOOL_URL
    _effective_tool_token    = _CLAUDE_CODE_TOOL_TOKEN
    _user_cc_profile         = None
    if _cc_profile_ids:
        def _resolve_cc_profile(profile_id: str):
            if not profile_id:
                return None
            return (_user_cc_map.get(profile_id)
                    or next((p for p in _read_cc_profiles() if p.get("id") == profile_id), None))

        _key_profile_id     = user_ctx.get("key_cc_profile_id", "") or ""
        _default_profile_id = user_ctx.get("default_cc_profile_id", "") or ""
        _user_cc_profile = (
            _resolve_cc_profile(_key_profile_id)
            or _resolve_cc_profile(_default_profile_id)
            or next((v for pid in _cc_profile_ids for v in [_user_cc_map.get(pid)] if v), None)
            or next((p for p in _read_cc_profiles() if p.get("id") in _cc_profile_ids and p.get("enabled", True)), None)
        )
        if _user_cc_profile:
            _effective_cc_mode       = _user_cc_profile.get("moe_mode", CLAUDE_CODE_MODE)
            _effective_tool_model    = _user_cc_profile.get("tool_model", CLAUDE_CODE_TOOL_MODEL).strip().rstrip("*")
            _effective_tool_endpoint = _user_cc_profile.get("tool_endpoint", CLAUDE_CODE_TOOL_ENDPOINT)
            _effective_tool_url      = URL_MAP.get(_effective_tool_endpoint)
            _effective_tool_token    = TOKEN_MAP.get(_effective_tool_endpoint, "ollama")
            _effective_tool_is_user_conn = False  # tracks whether tool_url came from a user connection
            if not _effective_tool_url:
                # Fallback: resolve tool_endpoint as a user private connection
                _cc_conns: dict = {}
                try:
                    _cc_conns = json.loads(_user_conns_json2 or "{}")
                except Exception:
                    pass
                _uc = _cc_conns.get(_effective_tool_endpoint)
                if _uc:
                    _effective_tool_url          = _uc["url"]
                    _effective_tool_token        = _uc.get("api_key") or "ollama"
                    _effective_tool_is_user_conn = True
                else:
                    _effective_tool_url = _CLAUDE_CODE_TOOL_URL
            # CC profile can force an expert template (admin_override bypasses permission check)
            _cc_tmpl_id = _user_cc_profile.get("expert_template_id") or None
            if _cc_tmpl_id:
                _user_experts = _resolve_user_experts(
                    user_ctx.get("permissions_json", ""),
                    override_tmpl_id=_cc_tmpl_id,
                    user_templates_json=_user_tmpls_json2,
                    admin_override=True,
                    user_connections_json=_user_conns_json2,
                ) or {}
                _user_tmpl_prompts = _resolve_template_prompts(
                    user_ctx.get("permissions_json", ""),
                    override_tmpl_id=_cc_tmpl_id,
                    user_templates_json=_user_tmpls_json2,
                    admin_override=True,
                    user_connections_json=_user_conns_json2,
                )

    # Detect whether request originates from Claude Code / Anthropic SDK
    is_claude_code = model.startswith("claude-") or model in CLAUDE_CODE_MODELS

    has_tool_results = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m.get("content", []))
        for m in messages
    )

    # Per-node timeout for the configured tool model (profile override takes precedence)
    _cc_tool_node_cfg = _server_info(_effective_tool_endpoint)
    _cc_tool_timeout  = int(_cc_tool_node_cfg.get("timeout", JUDGE_TIMEOUT))
    if _user_cc_profile and _user_cc_profile.get("tool_timeout"):
        _cc_tool_timeout = int(_user_cc_profile["tool_timeout"])

    # Live monitoring: register request
    _cc_moe_mode = (
        "cc_tool"      if (tools or has_tool_results or _effective_cc_mode == "native") else
        "cc_reasoning" if _effective_cc_mode == "moe_reasoning" else
        "cc_moe"
    )
    _cc_req_type   = "streaming" if body.get("stream", False) else "batch"
    _cc_client_ip  = request.client.host if request.client else ""
    # Backend model for live monitoring: shows the actual LLM / template in parentheses
    _cc_backend_model = _effective_tool_model
    if _cc_moe_mode == "cc_moe":
        _cc_tmpl_id_for_display = _user_cc_profile.get("expert_template_id") if _user_cc_profile else None
        if _cc_tmpl_id_for_display:
            _cc_backend_model = next(
                (t.get("name", _cc_tmpl_id_for_display) for t in _read_expert_templates()
                 if t.get("id") == _cc_tmpl_id_for_display),
                _cc_tmpl_id_for_display
            )
        else:
            _cc_backend_model = "MoE"
    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=_user_id, model=model,
        moe_mode=_cc_moe_mode, req_type=_cc_req_type, client_ip=_cc_client_ip,
        backend_model=_cc_backend_model,
        api_key_id=_api_key_id,
    ))

    try:
        # Mode 1: Native — pass everything directly to the configured tool model
        if _effective_cc_mode == "native":
            return await _anthropic_tool_handler(
                body, chat_id,
                tool_model=_effective_tool_model,
                tool_url=_effective_tool_url,
                tool_token=_effective_tool_token,
                tool_timeout=_cc_tool_timeout,
                tool_node=_effective_tool_endpoint,
                user_id=_user_id,
                api_key_id=_api_key_id,
                session_id=session_id,
                is_user_conn=_effective_tool_is_user_conn,
            )

        # All modes: tool calls always go to the tool model (precise function calling needed)
        if tools or has_tool_results:
            if is_claude_code:
                return await _anthropic_tool_handler(
                    body, chat_id,
                    tool_model=_effective_tool_model,
                    tool_url=_effective_tool_url,
                    tool_token=_effective_tool_token,
                    tool_timeout=_cc_tool_timeout,
                    tool_node=_effective_tool_endpoint,
                    user_id=_user_id,
                    api_key_id=_api_key_id,
                    session_id=session_id,
                    is_user_conn=_effective_tool_is_user_conn,
                )
            return await _anthropic_tool_handler(body, chat_id, user_id=_user_id, api_key_id=_api_key_id, session_id=session_id)

        # Mode 2: MoE Reasoning — reasoning expert with <think> parsing and thinking blocks
        if _effective_cc_mode == "moe_reasoning":
            return await _anthropic_reasoning_handler(body, chat_id, user_id=_user_id, api_key_id=_api_key_id, session_id=session_id)

        # Mode 3 + fallback: MoE Orchestrated — full planner, all experts
        return await _anthropic_moe_handler(body, chat_id,
                                             user_permissions=_user_perms,
                                             user_experts=_user_experts,
                                             planner_prompt=_user_tmpl_prompts["planner_prompt"],
                                             judge_prompt=_user_tmpl_prompts["judge_prompt"],
                                             judge_model_override=_user_tmpl_prompts["judge_model_override"],
                                             judge_url_override=_user_tmpl_prompts["judge_url_override"],
                                             judge_token_override=_user_tmpl_prompts["judge_token_override"],
                                             planner_model_override=_user_tmpl_prompts["planner_model_override"],
                                             planner_url_override=_user_tmpl_prompts["planner_url_override"],
                                             planner_token_override=_user_tmpl_prompts["planner_token_override"],
                                             user_id=_user_id,
                                             api_key_id=_api_key_id,
                                             session_id=session_id,
                                             enable_semantic_memory=_user_tmpl_prompts.get("enable_semantic_memory", False),
                                             cross_session_enabled=_user_tmpl_prompts.get("enable_cross_session_memory", False),
                                             cross_session_scopes=_user_tmpl_prompts.get("cross_session_scopes", ["private"]))
    except Exception as _exc:
        logger.error("Messages-Endpoint unbehandelte Exception (chat_id=%s): %s", chat_id, _exc, exc_info=True)
        asyncio.create_task(_deregister_active_request(chat_id))
        return JSONResponse(status_code=500, content={"error": {
            "type": "api_error", "message": f"Internal server error: {_exc}"
        }})


_ONTOLOGY_RUN_KEY = "moe:maintenance:ontology:run"
_ONTOLOGY_RUNS_HISTORY_KEY = "moe:maintenance:ontology:runs"


async def _set_healer_status(**fields) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.hset(_ONTOLOGY_RUN_KEY, mapping={k: str(v) for k, v in fields.items()})
        await redis_client.expire(_ONTOLOGY_RUN_KEY, 86400)
    except Exception:
        pass


async def _run_healer_task(concurrency: int, batch_size: int, run_id: str) -> None:
    """Spawn gap_healer_templates.py --once in the orchestrator container."""
    import time as _t
    import uuid as _uuid
    start = _t.time()
    # Clear stale fields from previous runs first.
    if redis_client is not None:
        try:
            await redis_client.delete(_ONTOLOGY_RUN_KEY)
        except Exception:
            pass
    await _set_healer_status(
        status="running", run_id=run_id, started_at=str(start),
        concurrency=concurrency, batch_size=batch_size,
        processed=0, written=0, failed=0, message="",
    )
    env = os.environ.copy()
    env["CONCURRENCY"] = str(concurrency)
    env["BATCH_SIZE"] = str(batch_size)
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    # The healer calls /v1/chat/completions which requires a valid Bearer.
    # Use the SYSTEM_API_KEY (installed with an active api_keys row).
    sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
    if sys_key:
        env["MOE_API_KEY"] = sys_key
    proc = await asyncio.create_subprocess_exec(
        "python3", "/app/scripts/gap_healer_templates.py", "--once",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stats = {"processed": 0, "written": 0, "failed": 0}
    assert proc.stdout is not None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            if "✓" in text and "→" in text:
                stats["written"] += 1
            elif "?" in text and "→" in text:
                stats["processed"] += 1
            elif "✗" in text:
                stats["failed"] += 1
            if any(stats.values()):
                await _set_healer_status(status="running", **stats)
        rc = await proc.wait()
    except Exception as e:
        await _set_healer_status(status="failed", message=str(e)[:200])
        return
    final = "ready" if rc == 0 else "failed"
    await _set_healer_status(
        status=final, run_id=run_id, finished_at=str(_t.time()),
        exit_code=rc, **stats,
    )
    if redis_client is not None:
        try:
            entry = json.dumps({
                "run_id": run_id, "type": "oneshot", "template": "",
                "started_at": start, "finished_at": _t.time(),
                "exit_code": rc, **stats,
            })
            await redis_client.lpush(_ONTOLOGY_RUNS_HISTORY_KEY, entry)
            await redis_client.ltrim(_ONTOLOGY_RUNS_HISTORY_KEY, 0, 99)
        except Exception:
            pass


async def clear_ontology_healer_status():
    """Delete the healer run status from Redis (dismiss failed/stale entries)."""
    if redis_client is None:
        return {"ok": False, "reason": "no_redis"}
    try:
        cur = await redis_client.hgetall(_ONTOLOGY_RUN_KEY)
        if cur and cur.get("status") == "running":
            return {"ok": False, "reason": "still_running"}
        await redis_client.delete(_ONTOLOGY_RUN_KEY)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:100]}


# ─── Dedicated Ontology Gap Healer (permanent loop, single node) ─────────────

_DEDICATED_HEALER_KEY = "moe:ontology:dedicated"
# _dedicated_healer_proc lives in state.py
# Mutex prevents concurrent auto-restart tasks (stream task + watchdog can both trigger).
# _dedicated_healer_restart_lock lives in state.py


async def _auto_resume_dedicated_healer() -> None:
    """Resume the dedicated healer after a container restart if Redis shows it was running.

    Called as a background task during startup. A short sleep lets the ASGI
    server finish binding before the healer subprocess sends its first request
    to the local API.
    """
    # state._dedicated_healer_proc is in state — no global needed
    import time as _t

    await asyncio.sleep(5)  # wait for ASGI server to start serving

    if redis_client is None:
        return
    try:
        data = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
    except Exception:
        return

    if not data or data.get("status") != "running":
        return

    template = data.get("template", "").strip()
    if not template:
        return

    # Confirm the previous PID is dead — if still alive, no restart needed.
    pid = int(data.get("pid", 0))
    if pid:
        try:
            os.kill(pid, 0)
            logger.info("🔄 Dedicated healer PID %s still alive — skipping auto-resume", pid)
            return
        except OSError:
            pass  # process dead; container was restarted

    logger.info("🔄 Auto-resuming dedicated healer (template=%s, prev_pid=%s)", template, pid)

    # Clean stale fields before resuming so the watchdog gets a fresh baseline.
    try:
        await redis_client.delete(_DEDICATED_HEALER_KEY)
        await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
            "status": "starting",
            "template": template,
            "processed": "0",
            "written": "0",
            "failed": "0",
            "stalled": "0",
            "started_at": str(_t.time()),
            "auto_restart": "1",
        })
    except Exception:
        pass

    env = os.environ.copy()
    env["TEMPLATE_POOL"] = template
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    sys_key = (os.environ.get("SYSTEM_API_KEY", "") or os.environ.get("MOE_API_KEY", "")).strip()
    if sys_key:
        env["MOE_API_KEY"] = sys_key

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "/app/scripts/gap_healer_templates.py",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        state._dedicated_healer_proc = proc
        await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"pid": str(proc.pid)})

        # Early-exit check: if the process dies within 3 s, mark as stopped.
        first_line: "bytes | None" = None
        try:
            fl_task = asyncio.create_task(proc.stdout.readline())
            wt_task = asyncio.create_task(proc.wait())
            done, pending = await asyncio.wait({fl_task, wt_task}, timeout=3.0,
                                               return_when=asyncio.FIRST_COMPLETED)
            if wt_task in done:
                rc = wt_task.result()
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                    "status": "stopped", "exit_code": str(rc),
                })
                state._dedicated_healer_proc = None
                logger.error("❌ Dedicated healer exited immediately on resume (rc=%s)", rc)
                return
            wt_task.cancel()
            try:
                await wt_task
            except asyncio.CancelledError:
                pass
            if fl_task in done:
                first_line = fl_task.result() or None
            else:
                fl_task.cancel()
                try:
                    await fl_task
                except asyncio.CancelledError:
                    pass
        except Exception:
            pass

        await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "running"})
        asyncio.create_task(_stream_dedicated_healer(proc, first_line=first_line))
        logger.info("✅ Dedicated healer auto-resumed — PID %s (template=%s)", proc.pid, template)
    except Exception as e:
        logger.error("❌ Failed to auto-resume dedicated healer: %s", e)
        try:
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "stopped"})
        except Exception:
            pass


_HEALER_STALL_SECONDS = 300  # 5 minutes without output → stalled
_HEALER_RESTART_DELAY_S = 30  # pause between auto-restarts


async def _watchdog_dedicated_healer() -> None:
    """Periodic watchdog: escalate or auto-restart a stalled dedicated healer.

    Runs every 60 s. If the healer reports 'running' in Redis but has not
    produced any stdout output for HEALER_STALL_SECONDS, it is marked 'stalled'
    and the subprocess is restarted automatically.
    """
    import time as _t
    # state._dedicated_healer_proc is in state — no global needed

    while True:
        await asyncio.sleep(60)
        if redis_client is None:
            continue
        try:
            data = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
        except Exception:
            continue

        if not data or data.get("status") not in ("running", "stalled"):
            continue

        # PID liveness check — catches clean exits that didn't update status.
        pid = int(data.get("pid", 0))
        pid_alive = False
        if pid:
            try:
                os.kill(pid, 0)
                pid_alive = True
            except OSError:
                pass
        if not pid_alive:
            logger.warning(
                "⚠️  Dedicated healer PID %d is dead but status=%s — triggering auto-restart.",
                pid, data.get("status", "?"),
            )
            asyncio.create_task(_dedicated_healer_auto_restart_if_needed(
                template_hint=data.get("template", ""),
            ))
            continue

        last_ts = float(data.get("last_activity_ts") or 0)
        age = _t.time() - last_ts if last_ts else float("inf")

        if age < _HEALER_STALL_SECONDS:
            # Remove stalled flag if activity resumed.
            if data.get("stalled") == "1":
                try:
                    await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"stalled": "0", "status": "running"})
                except Exception:
                    pass
            continue

        logger.warning(
            "⚠️  Dedicated healer stalled — no output for %.0f s (template=%s). Auto-restarting.",
            age, data.get("template", "?"),
        )

        # Mark as stalled so the UI can show a warning immediately.
        try:
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"stalled": "1", "status": "stalled"})
        except Exception:
            pass

        # Kill the stuck subprocess before restarting.
        proc = state._dedicated_healer_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Re-use the same template to restart.
        template = data.get("template", "").strip()
        if not template:
            continue
        env = os.environ.copy()
        env["TEMPLATE_POOL"] = template
        env.setdefault("REQUEST_TIMEOUT", "900")
        env.setdefault("MOE_API_BASE", "http://localhost:8000")
        sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
        if sys_key:
            env["MOE_API_KEY"] = sys_key
        try:
            new_proc = await asyncio.create_subprocess_exec(
                "python3", "/app/scripts/gap_healer_templates.py",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            state._dedicated_healer_proc = new_proc
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "running",
                "stalled": "0",
                "pid": str(new_proc.pid),
                "started_at": str(_t.time()),
                "last_activity_ts": str(_t.time()),
            })
            asyncio.create_task(_stream_dedicated_healer(new_proc))
            logger.info("✅ Dedicated healer auto-restarted after stall — PID %s", new_proc.pid)
        except Exception as exc:
            logger.error("❌ Dedicated healer restart failed: %s", exc)


# _set_healer_status_dedicated, _stream_dedicated_healer,
# _dedicated_healer_auto_restart_if_needed moved to services/healer.py
from services.healer import (
    _set_healer_status_dedicated,
    _stream_dedicated_healer,
    _dedicated_healer_auto_restart_if_needed,
    _DEDICATED_HEALER_KEY,
    _HEALER_STALL_SECONDS,
    _HEALER_RESTART_DELAY_S,
)


async def stop_dedicated_healer():
    """Stop the running dedicated healer loop."""
    # state._dedicated_healer_proc is in state — no global needed
    import signal as _sig

    stopped = False
    # Try in-memory handle first
    if state._dedicated_healer_proc is not None and state._dedicated_healer_proc.returncode is None:
        try:
            state._dedicated_healer_proc.terminate()
            await asyncio.wait_for(state._dedicated_healer_proc.wait(), timeout=5.0)
        except Exception:
            try:
                state._dedicated_healer_proc.kill()
            except Exception:
                pass
        state._dedicated_healer_proc = None
        stopped = True

    # Also kill by PID from Redis (handles cross-restart cases)
    template_name = ""
    if redis_client is not None:
        try:
            cur = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
            template_name = cur.get("template", "")
            pid = int(cur.get("pid", 0))
            if pid and not stopped:
                try:
                    os.kill(pid, _sig.SIGTERM)
                    stopped = True
                except OSError:
                    pass
            # Full clean: delete all fields, keep only template + stopped status so the
            # UI can display which template was last used without stale counters.
            await redis_client.delete(_DEDICATED_HEALER_KEY)
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "stopped",
                "template": template_name,
                "auto_restart": "0",
            })
        except Exception:
            pass

    return {"ok": True, "stopped": stopped}


async def verify_dedicated_healer():
    """Verify that the dedicated healer is genuinely running.

    Returns whether the subprocess PID is alive AND whether the healer has
    produced an active API request visible in the live-monitoring table
    (moe:active:* keys with model prefix 'moe-ontology-curator').

    The UI polls this endpoint after clicking 'Dauerlauf starten' to confirm
    the process actually started before switching the button to 'running'.
    """
    import json as _json
    data: dict = {}
    if redis_client is not None:
        try:
            data = await redis_client.hgetall(_DEDICATED_HEALER_KEY) or {}
        except Exception:
            pass

    pid = int(data.get("pid", 0))
    pid_alive = False
    if pid:
        try:
            os.kill(pid, 0)
            pid_alive = True
        except OSError:
            pass
    # Also trust the in-memory handle if PID lookup fails (same container).
    if not pid_alive and state._dedicated_healer_proc is not None and state._dedicated_healer_proc.returncode is None:
        pid_alive = True

    # Scan moe:active:* for a live healer request.
    active_chat_id = None
    if redis_client is not None:
        try:
            async for key in redis_client.scan_iter("moe:active:*"):
                try:
                    raw = await redis_client.get(key)
                    if raw:
                        meta = _json.loads(raw)
                        if (meta.get("model") or "").startswith("moe-ontology-curator"):
                            active_chat_id = meta.get("chat_id")
                            break
                except Exception:
                    continue
        except Exception:
            pass

    import time as _t
    last_ts = float(data.get("last_activity_ts") or 0)
    activity_age = round(_t.time() - last_ts) if last_ts else None
    # Consider the healer "active" if it has a live API request OR produced output within 60 s.
    is_active = active_chat_id is not None or (activity_age is not None and activity_age < 60)

    return {
        "pid_alive": pid_alive,
        "has_active_request": active_chat_id is not None,
        "is_active": is_active,
        "chat_id": active_chat_id,
        "status": data.get("status", "stopped"),
        "pid": pid,
        "activity_age_seconds": activity_age,
    }


# ─── Benchmark Node Reservation ──────────────────────────────────────────────

_BENCHMARK_RESERVED_KEY  = "moe:benchmark_reserved"
_BENCHMARK_LOCK_META_KEY = "moe:benchmark_lock_meta"


async def benchmark_unlock():
    """Release all benchmark node reservations."""
    if redis_client is None:
        return {"ok": False, "reason": "redis_unavailable"}
    meta = await redis_client.hgetall(_BENCHMARK_LOCK_META_KEY) or {}
    try:
        released = json.loads(meta.get("nodes", "[]"))
    except Exception:
        released = []
    await redis_client.delete(_BENCHMARK_RESERVED_KEY)
    await redis_client.delete(_BENCHMARK_LOCK_META_KEY)
    logger.info(f"🔓 Benchmark lock released: nodes={released}")
    return {"ok": True, "released": released}


async def get_knowledge_stats():
    """Aggregate Neo4j counters for the stats dashboard."""
    try:
        from neo4j import AsyncGraphDatabase
        uri = os.environ.get("NEO4J_URI", "bolt://neo4j-knowledge:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        pwd = os.environ.get("NEO4J_PASSWORD") or os.environ.get("NEO4J_PASS") or ""
        driver = AsyncGraphDatabase.driver(uri, auth=(user, pwd))
    except Exception as e:
        return {"error": f"neo4j init: {e}"}
    stats: dict = {}
    try:
        async with driver.session() as s:
            r = await s.run("MATCH (e:Entity) RETURN count(e) AS n")
            stats["entities_total"] = (await r.single())["n"]
            r = await s.run("MATCH ()-[r]->() RETURN count(r) AS n")
            stats["relations_total"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.created_at >= datetime() - duration('P1D') "
                "RETURN count(e) AS n"
            )
            stats["entities_last_24h"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.created_at >= datetime() - duration('P7D') "
                "RETURN count(e) AS n"
            )
            stats["entities_last_7d"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.source IS NOT NULL "
                "RETURN e.source AS source, count(e) AS n ORDER BY n DESC LIMIT 10"
            )
            stats["entities_by_source"] = [
                {"source": rec["source"], "n": rec["n"]} async for rec in r
            ]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.type IS NOT NULL "
                "RETURN e.type AS type, count(e) AS n ORDER BY n DESC LIMIT 10"
            )
            stats["top_types"] = [
                {"type": rec["type"], "n": rec["n"]} async for rec in r
            ]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.curator_template IS NOT NULL "
                "RETURN e.curator_template AS template, count(e) AS n "
                "ORDER BY n DESC LIMIT 20"
            )
            stats["entities_by_curator"] = [
                {"template": rec["template"], "n": rec["n"]} async for rec in r
            ]
    except Exception as e:
        stats["error"] = str(e)
    finally:
        await driver.close()
    return stats


async def get_planner_patterns(limit: int = 20):
    """Shows proven planner patterns based on positive user feedback."""
    if redis_client is None:
        return {"error": "Valkey not available"}
    try:
        patterns = await redis_client.zrevrange("moe:planner_success", 0, limit - 1, withscores=True)
        return {"patterns": [{"signature": sig, "count": int(score)} for sig, score in patterns]}
    except Exception as e:
        return {"error": str(e)}


_PL_SORT_COLS = {
    "requested_at": "ul.requested_at",
    "model":        "ul.model",
    "moe_mode":     "ul.moe_mode",
    "username":     "u.username",
    "total_tokens": "ul.total_tokens",
    "latency_ms":   "ul.latency_ms",
    "complexity_level": "ul.complexity_level",
}

async def pipeline_log(
    raw_request: Request,
    limit: int = 100,
    offset: int = 0,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    model: Optional[str] = None,
    moe_mode: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    complexity_level: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    sort_by: str = "requested_at",
    sort_dir: str = "desc",
    format: str = "json",
) -> Response:
    """Pipeline Transparency Log — query routing decisions, expert domains, and latency per request.

    Supports filtering by user, model, mode, date range, complexity level, and cache hit status.
    Supports sorting by any column via sort_by/sort_dir.
    Returns JSON (default) or CSV for BI/export use. Auth: admin API key required.
    """
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    _sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
    _is_sys_key = bool(_sys_key and raw_key == _sys_key)
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})
    if not (_is_sys_key or user_ctx.get("is_admin")):
        return JSONResponse(status_code=403, content={"error": "Admin access required"})

    try:
        if _userdb_pool is None:
            return JSONResponse(status_code=503, content={"error": "Database unavailable"})

        conditions: list[str] = []
        params: list = []

        if user_id:
            conditions.append("ul.user_id = %s")
            params.append(user_id)
        if username:
            conditions.append("u.username ILIKE %s")
            params.append(f"%{username}%")
        if model:
            conditions.append("ul.model ILIKE %s")
            params.append(f"%{model}%")
        if moe_mode:
            conditions.append("ul.moe_mode = %s")
            params.append(moe_mode)
        if from_date:
            conditions.append("ul.requested_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("ul.requested_at <= %s")
            params.append(to_date + "T23:59:59")
        if complexity_level:
            conditions.append("ul.complexity_level = %s")
            params.append(complexity_level)
        if cache_hit is not None:
            conditions.append("ul.cache_hit = %s")
            params.append(cache_hit)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        _sort_col = _PL_SORT_COLS.get(sort_by, "ul.requested_at")
        _sort_ord = "ASC" if sort_dir.lower() == "asc" else "DESC"
        params.extend([limit, offset])

        async with _userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""SELECT ul.request_id, ul.user_id, u.username,
                               ul.model, ul.moe_mode, ul.session_id,
                               ul.prompt_tokens, ul.completion_tokens, ul.total_tokens,
                               ul.latency_ms, ul.complexity_level, ul.expert_domains,
                               ul.cache_hit, ul.agentic_rounds, ul.status, ul.requested_at
                        FROM usage_log ul
                        LEFT JOIN users u ON ul.user_id = u.id
                        {where}
                        ORDER BY {_sort_col} {_sort_ord}
                        LIMIT %s OFFSET %s""",
                    params,
                )
                rows = await cur.fetchall()
                cols = [d.name for d in cur.description]

                await cur.execute(
                    f"SELECT COUNT(*) FROM usage_log ul LEFT JOIN users u ON ul.user_id = u.id {where}",
                    params[:-2],
                )
                total = (await cur.fetchone())[0]

        records = [dict(zip(cols, row)) for row in rows]

        if format == "csv":
            import io, csv as _csv
            buf = io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=cols)
            writer.writeheader()
            writer.writerows(records)
            return Response(
                content=buf.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=pipeline_log.csv"},
            )

        return JSONResponse({
            "total": total,
            "limit": limit,
            "offset": offset,
            "records": records,
        })
    except Exception as e:
        logger.warning("Pipeline log query failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ─── OLLAMA COMPATIBILITY API (/api/*) ───────────────────────────────────────
#
# Translates the Ollama wire-protocol to the MoE pipeline and back.
# Auth: same Bearer-Token (moe-sk-*) as /v1/.
# Streaming: NDJSON (one JSON object per line, \n-separated) — not SSE.
#
# Supported:
#   GET  /api/version   – version stub
#   GET  /api/tags      – model list in Ollama format (auth required)
#   GET  /api/ps        – templates as "loaded" models
#   POST /api/show      – template details
#   POST /api/chat      – main chat endpoint (streaming NDJSON or single JSON)
#   POST /api/generate  – single-turn prompt (routes through /api/chat logic)
#   POST /api/pull      – fake pull-progress stream
#   DELETE /api/delete  – 400 stub (managed via Admin UI)
#   POST /api/copy|push|embed – 400 stubs
# ─────────────────────────────────────────────────────────────────────────────


def _ollama_now() -> str:
    """Return current UTC time in Ollama's nanosecond ISO8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def _ollama_model_entry(tmpl: dict, *, now_iso: str) -> dict:
    """Convert an admin template dict to an Ollama /api/tags model entry."""
    return {
        "name":        tmpl.get("name", tmpl["id"]),
        "model":       tmpl.get("name", tmpl["id"]),
        "modified_at": now_iso,
        "size":        0,
        "digest":      hashlib.sha256(tmpl["id"].encode()).hexdigest(),
        "details": {
            "parent_model":       "",
            "format":             "gguf",
            "family":             "moe",
            "families":           ["moe"],
            "parameter_size":     tmpl.get("description", ""),
            "quantization_level": "MoE",
        },
    }


def _ollama_messages_to_oai(messages: list) -> list:
    """Translate Ollama messages (with optional base64 images) to OpenAI format."""
    out = []
    for m in messages:
        content = m.get("content", "")
        images  = m.get("images", [])
        if images:
            parts = [{"type": "text", "text": content}]
            for img in images:
                parts.append({
                    "type":      "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img}"},
                })
            content = parts
        out.append({"role": m.get("role", "user"), "content": content})
    return out


def _ollama_options_to_oai(options: dict) -> dict:
    """Map Ollama generation options to OpenAI-compatible parameters."""
    result = {}
    if "temperature" in options:
        result["temperature"] = options["temperature"]
    if "num_predict" in options:
        result["max_tokens"] = options["num_predict"]
    if "top_p" in options:
        result["top_p"] = options["top_p"]
    if "seed" in options:
        result["seed"] = options["seed"]
    return result


async def _ollama_resolve_template(model_name: str, user_perms: dict, user_templates_json: str = "{}"):
    """Return (tmpl_id, tmpl_dict | None, error_response | None) for an Ollama model name."""
    all_tmpls   = _read_expert_templates()
    matched     = next((t for t in all_tmpls if t.get("name") == model_name or t.get("id") == model_name), None)
    owned_tmpls: dict = {}
    try:
        owned_tmpls = json.loads(user_templates_json or "{}")
    except Exception:
        pass
    if not matched:
        # Check user-owned templates before giving up
        for ut_id, ut_cfg in owned_tmpls.items():
            ut_name = ut_cfg.get("name", "")
            if ut_name == model_name or ut_id == model_name:
                return ut_id, ut_cfg, None
        return None, None, None  # No template match — will fall through to default MoE mode
    tmpl_id      = matched["id"]
    allowed      = user_perms.get("expert_template", [])
    if tmpl_id not in allowed and tmpl_id not in owned_tmpls:
        return tmpl_id, matched, JSONResponse(status_code=403, content={
            "error": f"Template '{model_name}' is not authorized for this API key"
        })
    return tmpl_id, matched, None


async def _ollama_internal_stream(
    user_ctx: dict,
    model_name: str,
    oai_messages: list,
    extra_params: dict,
):
    """Async generator: yields raw SSE lines from the MoE pipeline for an Ollama request.

    Performs full template resolution and calls stream_response() directly,
    identical to /v1/chat/completions after auth, so no pipeline logic is duplicated.
    """
    chat_id   = f"chatcmpl-{uuid.uuid4()}"
    user_id   = user_ctx.get("user_id", "anon")
    api_key_id = user_ctx.get("key_id", "")
    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))

    tmpl_id, matched_tmpl, err = await _ollama_resolve_template(
        model_name, user_perms, user_ctx.get("user_templates_json", "{}")
    )
    if err is not None:
        yield f"data: {json.dumps({'error': 'template_not_authorized'})}\n\n"
        return

    _user_tmpls_json = user_ctx.get("user_templates_json", "{}")
    _user_conns_json = user_ctx.get("user_connections_json", "{}")
    user_experts  = _resolve_user_experts(
        user_ctx.get("permissions_json", ""), override_tmpl_id=tmpl_id,
        user_templates_json=_user_tmpls_json, admin_override=False,
        user_connections_json=_user_conns_json) or {}
    tmpl_prompts  = _resolve_template_prompts(
        user_ctx.get("permissions_json", ""), override_tmpl_id=tmpl_id,
        user_templates_json=_user_tmpls_json, admin_override=False,
        user_connections_json=_user_conns_json)

    mode = _MODEL_ID_TO_MODE.get(model_name, "default")
    if matched_tmpl and matched_tmpl.get("force_think") and mode == "default":
        mode = "agent_orchestrated"

    system_msgs   = [m for m in oai_messages if m.get("role") == "system"]
    system_prompt = system_msgs[0]["content"] if system_msgs else ""

    user_msgs = [m for m in oai_messages if m.get("role") in ("user", "assistant")]
    last_user = next((m for m in reversed(oai_messages) if m.get("role") == "user"), None)
    user_input = last_user["content"] if last_user else ""
    if isinstance(user_input, list):
        user_input = " ".join(p.get("text", "") for p in user_input if isinstance(p, dict))

    _user_images: list = []
    if last_user:
        raw_content = last_user.get("content", "")
        if isinstance(raw_content, list):
            _user_images = _extract_oai_images(raw_content)

    raw_history = [
        {"role": m.get("role"), "content": m.get("content", "") if isinstance(m.get("content"), str) else ""}
        for m in oai_messages
        if m.get("role") in ("user", "assistant") and m is not last_user
    ]
    _hist_turns = tmpl_prompts.get("history_max_turns", 0) or None
    _hist_chars = tmpl_prompts.get("history_max_chars", 0) or None
    history = _truncate_history(raw_history, max_turns=_hist_turns, max_chars=_hist_chars)

    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=user_id, model=model_name,
        moe_mode=mode, req_type="streaming", template_name=model_name,
        client_ip="", backend_model="", backend_host="", api_key_id=api_key_id,
    ))

    async for sse_line in stream_response(
        user_input, chat_id, mode,
        chat_history=history,
        system_prompt=system_prompt,
        user_id=user_id,
        api_key_id=api_key_id,
        user_permissions=user_perms,
        user_experts=user_experts,
        planner_prompt=tmpl_prompts["planner_prompt"],
        judge_prompt=tmpl_prompts["judge_prompt"],
        judge_model_override=tmpl_prompts["judge_model_override"],
        judge_url_override=tmpl_prompts["judge_url_override"],
        judge_token_override=tmpl_prompts["judge_token_override"],
        planner_model_override=tmpl_prompts["planner_model_override"],
        planner_url_override=tmpl_prompts["planner_url_override"],
        planner_token_override=tmpl_prompts["planner_token_override"],
        model_name=model_name,
        pending_reports=[],
        images=_user_images,
        session_id=None,
        max_agentic_rounds=tmpl_prompts.get("max_agentic_rounds", 0),
        no_cache=False,
    ):
        yield sse_line


async def ollama_version():
    """Ollama version stub — clients use this to detect Ollama compatibility."""
    return {"version": "0.6.0"}


async def ollama_tags(raw_request: Request):
    """Return templates visible to this API key in Ollama model-list format."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    # Append user-owned templates (stored per-user in Valkey, not in admin DB)
    _ut_tags: dict = {}
    try:
        _ut_tags = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    _visible_names = {t.get("name", t["id"]) for t in visible}
    for _uid_t, _ucfg_t in _ut_tags.items():
        _m = {"id": _uid_t, **_ucfg_t}
        if _m.get("name", _uid_t) not in _visible_names:
            visible.append(_m)
    return {"models": [_ollama_model_entry(t, now_iso=now) for t in visible]}


async def ollama_ps(raw_request: Request):
    """Return templates as 'loaded' models (no real VRAM tracking in MoE)."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()
    expires    = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    # Append user-owned templates (stored per-user in Valkey, not in admin DB)
    _ut_ps: dict = {}
    try:
        _ut_ps = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    _vis_names_ps = {t.get("name", t["id"]) for t in visible}
    for _uid_ps, _ucfg_ps in _ut_ps.items():
        _m_ps = {"id": _uid_ps, **_ucfg_ps}
        if _m_ps.get("name", _uid_ps) not in _vis_names_ps:
            visible.append(_m_ps)
    models = []
    for t in visible:
        entry = _ollama_model_entry(t, now_iso=now)
        entry["expires_at"] = expires
        entry["size_vram"]  = 0
        models.append(entry)
    return {"models": models}


async def ollama_show(raw_request: Request):
    """Return template details in Ollama modelinfo format (no auth required for show)."""
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    model_name = body.get("model", body.get("name", ""))
    templates  = _read_expert_templates()
    tmpl = next(
        (t for t in templates if t.get("name") == model_name or t.get("id") == model_name),
        None,
    )
    if not tmpl:
        return JSONResponse(status_code=404, content={"error": f"model '{model_name}' not found"})
    return {
        "modelfile":  f"# MoE Sovereign Template: {tmpl.get('name', '')}",
        "parameters": "",
        "template":   "{{ .Prompt }}",
        "details": {
            "family":             "moe",
            "parameter_size":     tmpl.get("description", ""),
            "quantization_level": "MoE",
        },
        "model_info": {
            "general.name":        tmpl.get("name", ""),
            "general.description": tmpl.get("description", ""),
        },
    }


async def ollama_chat(raw_request: Request):
    """Ollama /api/chat — translates Ollama chat format to the MoE pipeline."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    model    = body.get("model", "")
    stream   = body.get("stream", True)
    options  = body.get("options", {})
    oai_msgs = _ollama_messages_to_oai(body.get("messages", []))

    async def _ndjson_stream():
        total_tokens = 0
        async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
            sse_line = sse_line.strip()
            if not sse_line or sse_line.startswith(":"):
                continue  # skip SSE keep-alives and empty lines
            if sse_line.startswith("data: "):
                payload = sse_line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta   = (chunk.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    total_tokens += 1
                yield json.dumps({
                    "model":      model,
                    "created_at": _ollama_now(),
                    "message":    {"role": "assistant", "content": content},
                    "done":       False,
                }) + "\n"
        yield json.dumps({
            "model":           model,
            "created_at":      _ollama_now(),
            "message":         {"role": "assistant", "content": ""},
            "done":            True,
            "done_reason":     "stop",
            "total_duration":  0,
            "eval_count":      total_tokens,
        }) + "\n"

    if stream:
        return StreamingResponse(_ndjson_stream(), media_type="application/x-ndjson")

    # Non-streaming: collect all content, return single response object
    content_parts = []
    async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
        sse_line = sse_line.strip()
        if not sse_line or sse_line.startswith(":"):
            continue
        if sse_line.startswith("data: "):
            payload = sse_line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            content_parts.append(delta.get("content", ""))
    return {
        "model":       model,
        "created_at":  _ollama_now(),
        "message":     {"role": "assistant", "content": "".join(content_parts)},
        "done":        True,
        "done_reason": "stop",
    }


async def ollama_generate(raw_request: Request):
    """Ollama /api/generate — single-turn prompt, routed as chat through the MoE pipeline.

    Response uses key 'response' (not 'message.content') per Ollama spec.
    """
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    model   = body.get("model", "")
    prompt  = body.get("prompt", "")
    system  = body.get("system", "")
    stream  = body.get("stream", True)
    options = body.get("options", {})

    oai_msgs = []
    if system:
        oai_msgs.append({"role": "system", "content": system})
    oai_msgs.append({"role": "user", "content": prompt})

    async def _gen_stream():
        total_tokens = 0
        async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
            sse_line = sse_line.strip()
            if not sse_line or sse_line.startswith(":"):
                continue
            if sse_line.startswith("data: "):
                payload = sse_line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta   = (chunk.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    total_tokens += 1
                yield json.dumps({
                    "model":      model,
                    "created_at": _ollama_now(),
                    "response":   content,
                    "done":       False,
                }) + "\n"
        yield json.dumps({
            "model":           model,
            "created_at":      _ollama_now(),
            "response":        "",
            "done":            True,
            "done_reason":     "stop",
            "total_duration":  0,
            "eval_count":      total_tokens,
        }) + "\n"

    if stream:
        return StreamingResponse(_gen_stream(), media_type="application/x-ndjson")

    content_parts = []
    async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
        sse_line = sse_line.strip()
        if not sse_line or sse_line.startswith(":"):
            continue
        if sse_line.startswith("data: "):
            payload = sse_line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            content_parts.append(delta.get("content", ""))
    return {
        "model":       model,
        "created_at":  _ollama_now(),
        "response":    "".join(content_parts),
        "done":        True,
        "done_reason": "stop",
    }


async def ollama_pull(raw_request: Request):
    """Fake pull-progress stream — MoE models are managed via Admin UI, not downloaded."""
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    do_stream = body.get("stream", True)
    if not do_stream:
        return {"status": "success"}

    async def _progress():
        for status in ["pulling manifest", "verifying sha256 digest", "writing manifest", "success"]:
            yield json.dumps({"status": status}) + "\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(_progress(), media_type="application/x-ndjson")


async def ollama_delete():
    """Model deletion is not supported — managed via Admin UI."""
    return JSONResponse(status_code=400, content={
        "error": "Model deletion is managed via Admin UI"
    })


async def ollama_not_supported():
    """Stub for Ollama endpoints not supported by MoE Sovereign."""
    return JSONResponse(status_code=400, content={
        "error": "Not supported by MoE Sovereign"
    })


# ─── End of OLLAMA COMPATIBILITY API ─────────────────────────────────────────


# Converts OpenAI Responses API (/v1/responses) to Chat Completions internally.
# Required by Codex CLI (wire_api = "responses").

class _ResponsesRequest(BaseModel):
    model: str
    input: Any                        # str or list of {role, content} items
    instructions: Optional[str] = None
    stream: bool = False
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    previous_response_id: Optional[str] = None
    # passthrough fields silently accepted
    tools: Optional[Any] = None
    text: Optional[Any] = None
    include: Optional[Any] = None
    background: Optional[bool] = None
    context_management: Optional[Any] = None
    max_agentic_rounds: Optional[int] = None
    no_cache: bool = False


def _flatten_responses_content(content: Any) -> str:
    """Flatten Responses API content blocks to a plain string for Ollama."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("text", "input_text", "output_text"):
                    parts.append(block.get("text", ""))
                elif btype == "input_file":
                    fname = block.get("filename", "attachment")
                    fdata = block.get("file_data") or block.get("text", "")
                    if fdata:
                        parts.append(f"\n\n--- {fname} ---\n{fdata}\n---")
                elif btype == "image_url":
                    pass  # text-only experts skip images
                else:
                    text = block.get("text", "") or str(block.get("value", ""))
                    if text:
                        parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _responses_input_to_messages(inp: Any, instructions: Optional[str]) -> list:
    """Convert Responses API input to Chat Completions messages list.

    Handles both simple strings and the richer Responses API format:
    items may be plain message dicts, type='message' wrappers, or direct
    text/file content blocks. Content blocks are flattened to strings so
    downstream Ollama experts receive only plain text.
    """
    messages: list = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            itype = item.get("type", "")
            # type="message" wrapper or plain role/content dict
            if itype == "message" or "role" in item:
                role = item.get("role", "user")
                if role == "developer":
                    role = "system"
                content = _flatten_responses_content(item.get("content", ""))
                if content:
                    messages.append({"role": role, "content": content})
            elif itype in ("input_text", "text"):
                # top-level text item without role wrapper → treat as user
                text = item.get("text", "")
                if text:
                    messages.append({"role": "user", "content": text})
    return messages


def _chat_completion_to_responses(chat_resp: dict, response_id: str) -> dict:
    """Convert a Chat Completions response dict to Responses API format."""
    choice = (chat_resp.get("choices") or [{}])[0]
    message = choice.get("message", {})
    content_text = message.get("content", "") or ""
    finish = choice.get("finish_reason", "stop")
    status = "completed" if finish in ("stop", "length", None) else "incomplete"
    usage = chat_resp.get("usage", {})
    ts = int(time.time())
    return {
        "id": response_id,
        "object": "response",
        "created_at": ts,
        "status": status,
        "model": chat_resp.get("model", ""),
        "output": [
            {
                "id": f"msg_{response_id}",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": content_text, "annotations": []}
                ],
            }
        ],
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


async def _invoke_pipeline_for_responses(
    raw_request: Request,
    request: "_ResponsesRequest",
    messages: list,
) -> tuple[str, int, int]:
    """Call the MoE pipeline directly and return (full_text, prompt_tokens, completion_tokens).

    Consumes stream_response() as an async generator so no HTTP self-call is needed.
    Collects only the final synthesised chat.completion chunk; skips all
    status/progress/debug delta lines emitted by the pipeline.
    """
    raw_key = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "missing_key"}
    if "error" in user_ctx:
        return "", 0, 0

    user_id = user_ctx.get("user_id", "anon")
    api_key_id = user_ctx.get("key_id", "")
    chat_id = f"chatcmpl-{uuid.uuid4()}"

    # Separate system messages from conversation history
    sys_msgs = [m for m in messages if m["role"] == "system"]
    conv = [m for m in messages if m["role"] != "system"]
    system_prompt = sys_msgs[-1]["content"] if sys_msgs else ""
    user_input = ""
    history: list = []
    if conv:
        *history, last = conv
        user_input = last.get("content", "") if isinstance(last.get("content"), str) else ""

    mode = _MODEL_ID_TO_MODE.get(request.model, "default")
    _tp = _resolve_template_prompts(
        user_ctx.get("permissions_json", ""),
        override_tmpl_id=request.model if request.model and request.model != "moe-orchestrator" else None,
        user_templates_json=user_ctx.get("user_templates_json", "{}"),
        admin_override=False,
        user_connections_json=user_ctx.get("user_connections_json", "{}"),
    )
    _max_rounds = (
        request.max_agentic_rounds
        if request.max_agentic_rounds is not None
        else _tp.get("max_agentic_rounds", 0)
    )

    full_text = ""
    prompt_tokens = 0
    completion_tokens = 0
    _text_parts: list[str] = []
    _in_think = False  # skip everything between <think> and </think> (pipeline internals)
    try:
        async for sse_line in stream_response(
            user_input=user_input,
            chat_id=chat_id,
            mode=mode,
            chat_history=history,
            system_prompt=system_prompt,
            user_id=user_id,
            api_key_id=api_key_id,
            planner_prompt=_tp.get("planner_prompt", ""),
            judge_prompt=_tp.get("judge_prompt", ""),
            judge_model_override=_tp.get("judge_model_override", ""),
            judge_url_override=_tp.get("judge_url_override", ""),
            judge_token_override=_tp.get("judge_token_override", ""),
            planner_model_override=_tp.get("planner_model_override", ""),
            planner_url_override=_tp.get("planner_url_override", ""),
            planner_token_override=_tp.get("planner_token_override", ""),
            model_name=request.model,
            session_id=_extract_session_id(raw_request),
            max_agentic_rounds=_max_rounds,
            no_cache=request.no_cache,
        ):
            if not isinstance(sse_line, str) or not sse_line.startswith("data:"):
                continue
            payload = sse_line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            # Usage chunk has choices=[] — extract token counts
            u = chunk.get("usage") or {}
            if u.get("prompt_tokens"):
                prompt_tokens = u["prompt_tokens"]
            if u.get("completion_tokens"):
                completion_tokens = u["completion_tokens"]
            # Content chunks carry delta.content — skip pipeline internals inside <think>
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            piece = delta.get("content", "")
            if not piece:
                continue
            if "<think>" in piece:
                _in_think = True
                continue
            if "</think>" in piece:
                _in_think = False
                continue
            if not _in_think:
                _text_parts.append(piece)
        full_text = "".join(_text_parts)
    except Exception as _pe:
        logger.warning("Responses API pipeline error: %s", _pe)

    return full_text, prompt_tokens, completion_tokens


async def _stream_responses_api(
    raw_request: Request,
    request: "_ResponsesRequest",
    response_id: str,
) -> AsyncGenerator[str, None]:
    """Stream Responses API SSE events matching OpenAI spec exactly.

    Uses sequence_number, output_index, content_index as required by Codex CLI.
    Sends keepalive SSE comments every 15 s while the pipeline runs.
    """
    messages = _responses_input_to_messages(request.input, request.instructions)
    ts = int(time.time())
    item_id = f"msg_{response_id}"
    seq = 0

    def _ev(event_type: str, data: dict) -> str:
        nonlocal seq
        data["sequence_number"] = seq
        seq += 1
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    yield _ev("response.created", {
        "type": "response.created",
        "response": {"id": response_id, "object": "response", "status": "in_progress",
                     "created_at": ts, "output": []},
    })
    yield _ev("response.output_item.added", {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {"id": item_id, "type": "message", "role": "assistant",
                 "status": "in_progress", "content": []},
    })
    yield _ev("response.content_part.added", {
        "type": "response.content_part.added",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": ""},
    })

    # Run pipeline as background task; send keepalives every 15 s while waiting
    pipeline_task = asyncio.create_task(
        _invoke_pipeline_for_responses(raw_request, request, messages)
    )
    while not pipeline_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(pipeline_task), timeout=15.0)
        except asyncio.TimeoutError:
            yield ": keep-alive\n\n"
    full_text, prompt_tokens, completion_tokens = pipeline_task.result()

    for i in range(0, max(len(full_text), 1), 50):
        piece = full_text[i:i + 50]
        if piece:
            yield _ev("response.output_text.delta", {
                "type": "response.output_text.delta",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "delta": piece,
            })

    yield _ev("response.output_text.done", {
        "type": "response.output_text.done",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "text": full_text,
    })
    yield _ev("response.content_part.done", {
        "type": "response.content_part.done",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": full_text, "annotations": []},
    })
    yield _ev("response.output_item.done", {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {"id": item_id, "type": "message", "role": "assistant",
                 "status": "completed",
                 "content": [{"type": "output_text", "text": full_text, "annotations": []}]},
    })
    yield _ev("response.completed", {
        "type": "response.completed",
        "response": {
            "id": response_id, "object": "response", "created_at": ts,
            "status": "completed", "model": request.model,
            "output": [{"id": item_id, "type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": full_text,
                                     "annotations": []}]}],
            "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens,
                      "output_tokens_details": {"reasoning_tokens": 0}},
        },
    })


async def responses_api(raw_request: Request, request: _ResponsesRequest):
    """OpenAI Responses API compatibility endpoint for Codex CLI."""
    response_id = f"resp_{uuid.uuid4().hex}"

    if request.stream:
        return StreamingResponse(
            _stream_responses_api(raw_request, request, response_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming: call pipeline directly (no HTTP self-call to avoid deadlock)
    messages = _responses_input_to_messages(request.input, request.instructions)
    try:
        full_text, prompt_tokens, completion_tokens = await _invoke_pipeline_for_responses(
            raw_request, request, messages
        )
        ts = int(time.time())
        return JSONResponse({
            "id": response_id,
            "object": "response",
            "created_at": ts,
            "status": "completed",
            "model": request.model,
            "output": [
                {
                    "id": f"msg_{response_id}",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": full_text, "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        })
    except Exception as _ie:
        logger.warning("Responses API non-streaming pipeline failed: %s", _ie)
        return JSONResponse(status_code=500, content={"error": {"message": str(_ie)}})


async def ollama_version():
    """Ollama version stub — clients use this to detect Ollama compatibility."""
    return {"version": "0.6.0"}


async def ollama_tags(raw_request: Request):
    """Return templates visible to this API key in Ollama model-list format."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    # Append user-owned templates (stored per-user in Valkey, not in admin DB)
    _ut_tags: dict = {}
    try:
        _ut_tags = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    _visible_names = {t.get("name", t["id"]) for t in visible}
    for _uid_t, _ucfg_t in _ut_tags.items():
        _m = {"id": _uid_t, **_ucfg_t}
        if _m.get("name", _uid_t) not in _visible_names:
            visible.append(_m)
    return {"models": [_ollama_model_entry(t, now_iso=now) for t in visible]}


async def ollama_ps(raw_request: Request):
    """Return templates as 'loaded' models (no real VRAM tracking in MoE)."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()
    expires    = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    # Append user-owned templates (stored per-user in Valkey, not in admin DB)
    _ut_ps: dict = {}
    try:
        _ut_ps = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    _vis_names_ps = {t.get("name", t["id"]) for t in visible}
    for _uid_ps, _ucfg_ps in _ut_ps.items():
        _m_ps = {"id": _uid_ps, **_ucfg_ps}
        if _m_ps.get("name", _uid_ps) not in _vis_names_ps:
            visible.append(_m_ps)
    models = []
    for t in visible:
        entry = _ollama_model_entry(t, now_iso=now)
        entry["expires_at"] = expires
        entry["size_vram"]  = 0
        models.append(entry)
    return {"models": models}


async def ollama_show(raw_request: Request):
    """Return template details in Ollama modelinfo format (no auth required for show)."""
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    model_name = body.get("model", body.get("name", ""))
    templates  = _read_expert_templates()
    tmpl = next(
        (t for t in templates if t.get("name") == model_name or t.get("id") == model_name),
        None,
    )
    if not tmpl:
        return JSONResponse(status_code=404, content={"error": f"model '{model_name}' not found"})
    return {
        "modelfile":  f"# MoE Sovereign Template: {tmpl.get('name', '')}",
        "parameters": "",
        "template":   "{{ .Prompt }}",
        "details": {
            "family":             "moe",
            "parameter_size":     tmpl.get("description", ""),
            "quantization_level": "MoE",
        },
        "model_info": {
            "general.name":        tmpl.get("name", ""),
            "general.description": tmpl.get("description", ""),
        },
    }


async def ollama_chat(raw_request: Request):
    """Ollama /api/chat — translates Ollama chat format to the MoE pipeline."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    model    = body.get("model", "")
    stream   = body.get("stream", True)
    options  = body.get("options", {})
    oai_msgs = _ollama_messages_to_oai(body.get("messages", []))

    async def _ndjson_stream():
        total_tokens = 0
        async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
            sse_line = sse_line.strip()
            if not sse_line or sse_line.startswith(":"):
                continue  # skip SSE keep-alives and empty lines
            if sse_line.startswith("data: "):
                payload = sse_line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta   = (chunk.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    total_tokens += 1
                yield json.dumps({
                    "model":      model,
                    "created_at": _ollama_now(),
                    "message":    {"role": "assistant", "content": content},
                    "done":       False,
                }) + "\n"
        yield json.dumps({
            "model":           model,
            "created_at":      _ollama_now(),
            "message":         {"role": "assistant", "content": ""},
            "done":            True,
            "done_reason":     "stop",
            "total_duration":  0,
            "eval_count":      total_tokens,
        }) + "\n"

    if stream:
        return StreamingResponse(_ndjson_stream(), media_type="application/x-ndjson")

    # Non-streaming: collect all content, return single response object
    content_parts = []
    async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
        sse_line = sse_line.strip()
        if not sse_line or sse_line.startswith(":"):
            continue
        if sse_line.startswith("data: "):
            payload = sse_line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            content_parts.append(delta.get("content", ""))
    return {
        "model":       model,
        "created_at":  _ollama_now(),
        "message":     {"role": "assistant", "content": "".join(content_parts)},
        "done":        True,
        "done_reason": "stop",
    }


async def ollama_generate(raw_request: Request):
    """Ollama /api/generate — single-turn prompt, routed as chat through the MoE pipeline.

    Response uses key 'response' (not 'message.content') per Ollama spec.
    """
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    model   = body.get("model", "")
    prompt  = body.get("prompt", "")
    system  = body.get("system", "")
    stream  = body.get("stream", True)
    options = body.get("options", {})

    oai_msgs = []
    if system:
        oai_msgs.append({"role": "system", "content": system})
    oai_msgs.append({"role": "user", "content": prompt})

    async def _gen_stream():
        total_tokens = 0
        async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
            sse_line = sse_line.strip()
            if not sse_line or sse_line.startswith(":"):
                continue
            if sse_line.startswith("data: "):
                payload = sse_line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta   = (chunk.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    total_tokens += 1
                yield json.dumps({
                    "model":      model,
                    "created_at": _ollama_now(),
                    "response":   content,
                    "done":       False,
                }) + "\n"
        yield json.dumps({
            "model":           model,
            "created_at":      _ollama_now(),
            "response":        "",
            "done":            True,
            "done_reason":     "stop",
            "total_duration":  0,
            "eval_count":      total_tokens,
        }) + "\n"

    if stream:
        return StreamingResponse(_gen_stream(), media_type="application/x-ndjson")

    content_parts = []
    async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
        sse_line = sse_line.strip()
        if not sse_line or sse_line.startswith(":"):
            continue
        if sse_line.startswith("data: "):
            payload = sse_line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            content_parts.append(delta.get("content", ""))
    return {
        "model":       model,
        "created_at":  _ollama_now(),
        "response":    "".join(content_parts),
        "done":        True,
        "done_reason": "stop",
    }


async def ollama_pull(raw_request: Request):
    """Fake pull-progress stream — MoE models are managed via Admin UI, not downloaded."""
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    do_stream = body.get("stream", True)
    if not do_stream:
        return {"status": "success"}

    async def _progress():
        for status in ["pulling manifest", "verifying sha256 digest", "writing manifest", "success"]:
            yield json.dumps({"status": status}) + "\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(_progress(), media_type="application/x-ndjson")


async def ollama_delete():
    """Model deletion is not supported — managed via Admin UI."""
    return JSONResponse(status_code=400, content={
        "error": "Model deletion is managed via Admin UI"
    })


async def ollama_not_supported():
    """Stub for Ollama endpoints not supported by MoE Sovereign."""
    return JSONResponse(status_code=400, content={
        "error": "Not supported by MoE Sovereign"
    })


# ─── End of OLLAMA COMPATIBILITY API ─────────────────────────────────────────


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=600,      # 10 min — prevents proxy/load-balancer from dropping long-running non-streaming requests
        timeout_graceful_shutdown=60,
    )

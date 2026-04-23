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
from typing import List, Annotated, Dict, Any, TypedDict, Optional, Union
from pipeline.state import AgentState
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

CORRECTION_MEMORY_ENABLED = os.getenv("CORRECTION_MEMORY_ENABLED", "true").lower() in ("1", "true", "yes")
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
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

import chromadb
from chromadb.utils import embedding_functions

# Import for the math node
from math_node import math_node
# Import for GraphRAG
from graph_rag import GraphRAGManager
# Import context window budget helper
from context_budget import graphrag_budget_chars

# --- CONFIG & LOGGING ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
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

KAFKA_BOOTSTRAP = os.getenv("KAFKA_URL", "kafka://moe-kafka:9092").replace("kafka://", "")
KAFKA_TOPIC_INGEST    = "moe.ingest"
KAFKA_TOPIC_REQUESTS  = "moe.requests"
KAFKA_TOPIC_FEEDBACK  = "moe.feedback"
KAFKA_TOPIC_LINTING   = "moe.linting"
KAFKA_TOPIC_AUDIT     = "moe.audit"

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

REDIS_URL  = os.getenv("REDIS_URL", "redis://terra_cache:6379")
# LangGraph checkpoints are persisted to a dedicated Postgres instance.
# Valkey-search/RediSearch is not available on this host (CPU without AVX2),
# so AsyncPostgresSaver replaces the former AsyncRedisSaver checkpointer.
POSTGRES_CHECKPOINT_URL = os.getenv("POSTGRES_CHECKPOINT_URL", "")
MOE_USERDB_URL = os.getenv(
    "MOE_USERDB_URL",
    "postgresql://moe_admin@terra_checkpoints:5432/moe_userdb",
)
# Module-global connection pool; initialised in the lifespan handler.
_userdb_pool: Optional[AsyncConnectionPool] = None

# OIDC / Authentik
AUTHENTIK_URL      = os.getenv("AUTHENTIK_URL", "")
OIDC_JWKS_URL      = os.getenv("OIDC_JWKS_URL", f"{AUTHENTIK_URL}/application/o/moe-sovereign/jwks/" if AUTHENTIK_URL else "")
OIDC_ISSUER        = os.getenv("OIDC_ISSUER", f"{AUTHENTIK_URL}/application/o/moe-sovereign/" if AUTHENTIK_URL else "")
OIDC_CLIENT_ID     = os.getenv("OIDC_CLIENT_ID", "moe-infra")
OIDC_ENABLED       = bool(AUTHENTIK_URL)

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
_gpu_lock   = threading.Lock()   # guards _endpoint_gpu_indices round-robin index
_cache_lock = threading.Lock()   # guards _model_avail_cache, _ps_cache, _jwks_cache
_shadow_lock = threading.Lock()  # guards _shadow_request_counter increment


def _read_expert_templates() -> list:
    """Return the current expert-templates list.

    Primary source: Postgres table ``admin_expert_templates`` (written by the
    Admin UI). The Orchestrator used to read EXPERT_TEMPLATES from /app/.env,
    but that blows past Linux MAX_ARG_STRLEN (128 kB) once enough templates
    exist, crashing sibling containers on exec. The DB is now authoritative;
    .env is a best-effort fallback for boot-time before DB is reachable.

    Cached for 30 s — templates created in the Admin UI become visible within
    half a minute without a container restart.
    """
    import time as _time
    now = _time.monotonic()
    cache = _read_expert_templates._cache
    if now - cache["ts"] < 30 and cache["data"] is not None:
        return cache["data"]

    data = _load_templates_from_db_sync()
    if data is None:
        # Fallback: read .env directly (legacy path, rarely exercised now).
        data = _load_templates_from_env_file()
    if data is None:
        data = json.loads(os.getenv("EXPERT_TEMPLATES", "[]"))

    cache["ts"] = now
    cache["data"] = data
    return data
_read_expert_templates._cache: dict = {"ts": 0.0, "data": None}


def _load_templates_from_db_sync() -> Optional[list]:
    """One-shot sync query against admin_expert_templates. Returns None on any
    failure so the caller can fall back to .env."""
    dsn = (
        os.getenv("MOE_USERDB_URL")
        or os.getenv("POSTGRES_CHECKPOINT_URL")
        or ""
    )
    if not dsn:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT id, name, description, config_json, is_active "
                    "FROM admin_expert_templates ORDER BY created_at ASC"
                )
                rows = cur.fetchall()
    except Exception:
        return None
    result: list = []
    for row in rows:
        cfg = row.get("config_json")
        if isinstance(cfg, str):
            try:
                tmpl = json.loads(cfg)
            except json.JSONDecodeError:
                tmpl = {}
        elif isinstance(cfg, dict):
            tmpl = dict(cfg)
        else:
            tmpl = {}
        tmpl["id"] = row["id"]
        tmpl["name"] = row["name"]
        tmpl["description"] = row.get("description", "")
        tmpl["is_active"] = row.get("is_active", True)
        result.append(tmpl)
    return result


def _load_templates_from_env_file() -> Optional[list]:
    """Legacy .env parser — kept as fallback when the DB is unreachable."""
    env_path = Path(os.getenv("ENV_FILE", "/app/.env"))
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("EXPERT_TEMPLATES="):
                raw = line[len("EXPERT_TEMPLATES="):].strip()
                if raw.startswith('"') and raw.endswith('"'):
                    raw = raw[1:-1].replace('\\\\', '\\').replace('\\"', '"')
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else None
    except Exception:
        return None
    return None


def _read_cc_profiles() -> list:
    """Reads CLAUDE_CODE_PROFILES dynamically from /app/.env (not os.getenv).
    Cached for 60 seconds to minimize disk I/O. This makes profiles
    created in the Admin UI visible without a container restart."""
    import time as _time
    now = _time.monotonic()
    cache = _read_cc_profiles._cache
    if now - cache["ts"] < 60 and cache["data"] is not None:
        return cache["data"]
    env_path = Path(os.getenv("ENV_FILE", "/app/.env"))
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("CLAUDE_CODE_PROFILES="):
                raw = line[len("CLAUDE_CODE_PROFILES="):].strip()
                if raw.startswith('"') and raw.endswith('"'):
                    raw = raw[1:-1].replace('\\\\', '\\').replace('\\"', '"')
                data = json.loads(raw)
                cache["ts"] = now
                cache["data"] = data
                return data
    except Exception:
        pass
    fallback = json.loads(os.getenv("CLAUDE_CODE_PROFILES", "[]"))
    cache["ts"] = now
    cache["data"] = fallback
    return fallback
_read_cc_profiles._cache: dict = {"ts": 0.0, "data": None}

NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://neo4j-knowledge:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS")

REDIS_URL = REDIS_URL  # keep original reference below

# INFERENCE_SERVERS: JSON array [{"name":..,"url":..,"gpu_count":..}]
# Configured via the Admin UI — no hardcoded defaults.
_INF_SERVERS_RAW = os.getenv("INFERENCE_SERVERS", "")
if _INF_SERVERS_RAW.strip():
    try:
        INFERENCE_SERVERS_LIST = json.loads(_INF_SERVERS_RAW)
    except json.JSONDecodeError:
        logger.warning("INFERENCE_SERVERS JSON is invalid — no inference servers loaded")
        INFERENCE_SERVERS_LIST = []
else:
    INFERENCE_SERVERS_LIST = []

# Filter out disabled servers
INFERENCE_SERVERS_LIST = [s for s in INFERENCE_SERVERS_LIST if s.get("enabled", True)]
URL_MAP      = {s["name"]: s["url"]                for s in INFERENCE_SERVERS_LIST if s.get("url")}
TOKEN_MAP    = {s["name"]: s.get("token", "ollama") for s in INFERENCE_SERVERS_LIST}
API_TYPE_MAP = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}
JUDGE_ENDPOINT_NAME = os.getenv("JUDGE_ENDPOINT", "")
JUDGE_URL   = URL_MAP.get(JUDGE_ENDPOINT_NAME) if JUDGE_ENDPOINT_NAME else None
JUDGE_TOKEN = TOKEN_MAP.get(JUDGE_ENDPOINT_NAME, "ollama") if JUDGE_ENDPOINT_NAME else "ollama"
# Graph ingest LLM — used exclusively by the background Kafka ingest consumer.
# Falls back to the judge LLM when not explicitly configured.
GRAPH_INGEST_MODEL    = os.getenv("GRAPH_INGEST_MODEL", "")
_GRAPH_INGEST_EP_NAME = os.getenv("GRAPH_INGEST_ENDPOINT", "")
GRAPH_INGEST_URL      = URL_MAP.get(_GRAPH_INGEST_EP_NAME) if _GRAPH_INGEST_EP_NAME else None
GRAPH_INGEST_TOKEN    = TOKEN_MAP.get(_GRAPH_INGEST_EP_NAME, "ollama") if _GRAPH_INGEST_EP_NAME else "ollama"
# Planner model — separate from the Judge/Merger/Critic LLM.
# phi4:14b is compact (9 GB), very reliable for structured JSON output
# and occupies no VRAM for parallel expert calls after unloading.
PLANNER_MODEL    = os.getenv("PLANNER_MODEL", "phi4:14b")
PLANNER_ENDPOINT = os.getenv("PLANNER_ENDPOINT", os.getenv("JUDGE_ENDPOINT", ""))
PLANNER_URL      = URL_MAP.get(PLANNER_ENDPOINT) if PLANNER_ENDPOINT else None
PLANNER_TOKEN    = TOKEN_MAP.get(PLANNER_ENDPOINT, "ollama") if PLANNER_ENDPOINT else "ollama"
# Native Ollama API base URLs (without /v1) for keep_alive=0 unload calls
_JUDGE_BASE    = (JUDGE_URL   or "").rstrip("/").removesuffix("/v1")
_PLANNER_BASE  = (PLANNER_URL or "").rstrip("/").removesuffix("/v1")
EXPERTS = json.loads(os.getenv("EXPERT_MODELS", "{}"))
MCP_URL = os.getenv("MCP_URL", "http://mcp-precision:8003")
# When True: graph_rag_node calls graph_query via MCP server instead of direct Neo4j
GRAPH_VIA_MCP = os.getenv("GRAPH_VIA_MCP", "false").lower() in ("1", "true", "yes")
# Maximum characters injected from the knowledge graph into planner/merger prompts.
# Prevents context-window saturation on nodes with small LLMs (e.g. phi4:14b @ 8192 tokens).
# Set to 0 to disable truncation.  Default: 6000 (~1500 tokens at 4 chars/token).
MAX_GRAPH_CONTEXT_CHARS: int = int(os.getenv("MAX_GRAPH_CONTEXT_CHARS", "6000"))
# Unified API Gateway: When set, all expert calls are routed through LiteLLM.
# LiteLLM handles endpoint selection, retries (circuit breaker) and fallback chains.
# Empty = direct access to Ollama nodes via _select_node() (fallback, backwards compatible).
LITELLM_URL = os.getenv("LITELLM_URL", "").rstrip("/")
# Shadow-Mode: sample production traffic against a candidate template for quality comparison.
# Every BENCHMARK_SHADOW_RATE-th request is sent to BENCHMARK_SHADOW_TEMPLATE in parallel.
# Responses are never shown to users — only stored for quality gate analysis.
BENCHMARK_SHADOW_TEMPLATE: str = os.getenv("BENCHMARK_SHADOW_TEMPLATE", "")
BENCHMARK_SHADOW_RATE: int     = int(os.getenv("BENCHMARK_SHADOW_RATE", "20"))
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


def _resolve_user_experts(permissions_json: str, override_tmpl_id: Optional[str] = None,
                           user_templates_json: str = "{}", admin_override: bool = False,
                           user_connections_json: str = "{}") -> Optional[dict]:
    """Returns an EXPERTS-compatible dict from the assigned expert template.
    New template format: {cat: {system_prompt, models:[{model,endpoint,required}]}}
    Legacy format (migrated): {cat: {model, endpoint}} is also supported.
    Returns None if no template is assigned → global EXPERTS are used.
    override_tmpl_id: If set, this template is loaded directly (model-ID routing).
    admin_override: If True, override_tmpl_id is loaded without permission check.
    user_templates_json: Inline JSON map {tmpl_id: config} for user-owned templates (from Valkey).
    """
    try:
        perms = json.loads(permissions_json or "{}")
        tmpl_ids = perms.get("expert_template", [])
        templates = _read_expert_templates()
        user_templates: dict = json.loads(user_templates_json or "{}")
        # Merge: user templates are looked up by ID prefix 'user:'
        def _find_tmpl(tid: str):
            if tid in user_templates:
                return user_templates[tid]
            return next((t for t in templates if t.get("id") == tid), None)
        if admin_override and override_tmpl_id:
            tmpl = _find_tmpl(override_tmpl_id)
        elif not tmpl_ids:
            return None
        elif override_tmpl_id and override_tmpl_id in tmpl_ids:
            tmpl = _find_tmpl(override_tmpl_id)
            if tmpl is None:
                logger.warning("Ghost template in _resolve_user_experts: %s in perms but not in DB/cache", override_tmpl_id)
        else:
            tmpl = next((_find_tmpl(tid) for tid in tmpl_ids if _find_tmpl(tid)), None)
            if tmpl is None:
                logger.warning("Ghost templates: all permitted templates missing from DB: %s", tmpl_ids)
        if not tmpl:
            return None
        result: dict = {}
        for cat, cat_cfg in tmpl.get("experts", {}).items():
            if isinstance(cat_cfg, dict) and "models" in cat_cfg:
                # New format
                models_list = []
                for m in cat_cfg.get("models", []):
                    role = m.get("role")  # new format: "primary"|"fallback"|"always"
                    if role is None:      # backward compat for old required=bool format
                        role = "always" if m.get("required", True) else "primary"
                    if role == "always":
                        forced, model_tier = True, None
                    elif role == "fallback":
                        forced, model_tier = False, 2
                    else:  # "primary"
                        forced, model_tier = False, 1
                    entry = {
                        "model":          m.get("model", ""),
                        "endpoint":       m.get("endpoint", ""),
                        "forced":         forced,
                        "enabled":        True,
                        "_system_prompt": cat_cfg.get("system_prompt", ""),
                    }
                    if not forced:
                        entry["_tier"] = model_tier
                    models_list.append(entry)
                result[cat] = models_list
            elif isinstance(cat_cfg, dict) and "model" in cat_cfg:
                # Legacy format (fallback)
                result[cat] = [{
                    "model":    cat_cfg.get("model", ""),
                    "endpoint": cat_cfg.get("endpoint", ""),
                    "forced":   True,
                    "enabled":  True,
                    "_system_prompt": "",
                }]
        # Inject URL/token for experts referencing user-owned connections
        user_conns = json.loads(user_connections_json or "{}")
        if user_conns:
            for _models_list in result.values():
                for _entry in _models_list:
                    _ep = _entry.get("endpoint", "")
                    if _ep and _ep not in URL_MAP and _ep in user_conns:
                        _conn = user_conns[_ep]
                        _entry["_user_conn_url"]      = _conn["url"]
                        _entry["_user_conn_token"]    = _conn.get("api_key") or "ollama"
                        _entry["_user_conn_api_type"] = _conn.get("api_type", "openai")
        return result if result else None
    except Exception:
        return None


def _resolve_template_prompts(permissions_json: str, override_tmpl_id: Optional[str] = None,
                               user_templates_json: str = "{}", admin_override: bool = False,
                               user_connections_json: str = "{}") -> dict:
    """Returns planner_prompt, judge_prompt and optional model overrides from the user's Expert Template.
    Model fields are stored as 'model@endpoint' strings; URL/token are resolved from INFERENCE_SERVERS.
    admin_override: If True, override_tmpl_id is loaded without permission check.
    user_templates_json: Inline JSON map {tmpl_id: config} for user-owned templates (from Valkey).
    """
    empty = {"planner_prompt": "", "judge_prompt": "",
             "judge_model_override": "", "judge_url_override": "", "judge_token_override": "",
             "planner_model_override": "", "planner_url_override": "", "planner_token_override": "",
             "enable_cache": True, "enable_graphrag": True, "enable_web_research": True,
             "graphrag_max_chars": 0,
             "history_max_turns": 0, "history_max_chars": 0,
             "max_agentic_rounds": 0}
    try:
        perms    = json.loads(permissions_json or "{}")
        tmpl_ids = perms.get("expert_template", [])
        templates = _read_expert_templates()
        user_templates: dict = json.loads(user_templates_json or "{}")
        def _find_tmpl(tid: str):
            if tid in user_templates:
                return user_templates[tid]
            return next((t for t in templates if t.get("id") == tid), None)
        if admin_override and override_tmpl_id:
            tmpl = _find_tmpl(override_tmpl_id)
        elif not tmpl_ids:
            return empty
        elif override_tmpl_id and override_tmpl_id in tmpl_ids:
            tmpl = _find_tmpl(override_tmpl_id)
        else:
            tmpl = next((_find_tmpl(tid) for tid in tmpl_ids if _find_tmpl(tid)), None)
        if not tmpl:
            return empty

        def _split_model_ep(val: str) -> tuple:
            """'model@endpoint' → (model, endpoint)"""
            if val and "@" in val:
                at = val.rindex("@")
                return val[:at], val[at + 1:]
            return val or "", ""

        judge_m, judge_ep   = _split_model_ep(tmpl.get("judge_model", ""))
        planner_m, planner_ep = _split_model_ep(tmpl.get("planner_model", ""))

        def _resolve_ep_url(ep: str) -> tuple:
            """Resolve endpoint name to (url, token): global URL_MAP first, user connections second."""
            if ep in URL_MAP:
                return URL_MAP[ep], TOKEN_MAP.get(ep, "ollama")
            _uc = json.loads(user_connections_json or "{}")
            if ep in _uc:
                return _uc[ep]["url"], _uc[ep].get("api_key") or "ollama"
            return "", "ollama"

        judge_url,   judge_tok   = _resolve_ep_url(judge_ep)   if judge_ep   else ("", "ollama")
        planner_url, planner_tok = _resolve_ep_url(planner_ep) if planner_ep else ("", "ollama")
        return {
            "planner_prompt":        tmpl.get("planner_prompt", ""),
            "judge_prompt":          tmpl.get("judge_prompt", ""),
            "judge_model_override":  judge_m,
            "judge_url_override":    judge_url,
            "judge_token_override":  judge_tok,
            "planner_model_override": planner_m,
            "planner_url_override":  planner_url,
            "planner_token_override": planner_tok,
            # Service toggles: allow templates to disable pipeline components
            "enable_cache":        tmpl.get("enable_cache", True),
            "enable_graphrag":     tmpl.get("enable_graphrag", True),
            "enable_web_research": tmpl.get("enable_web_research", True),
            # Context window budget: 0 = auto-compute from judge model's known context window.
            # Set a positive integer to pin the char limit; -1 = model-aware auto (same as 0).
            "graphrag_max_chars":  int(tmpl.get("graphrag_max_chars", 0)),
            # Per-template history compression: 0 = global default, -1 = unlimited (no compression).
            "history_max_turns":   int(tmpl.get("history_max_turns", 0)),
            "history_max_chars":   int(tmpl.get("history_max_chars", 0)),
            # When true: activate thinking_node (chain-of-thought) before routing, equivalent
            # to mode="agent_orchestrated" — set in template config_json as force_think: true.
            "force_think":         bool(tmpl.get("force_think", False)),
            # Agentic re-planning loop: 0 = disabled (single-pass), N = max re-plan iterations.
            "max_agentic_rounds":  int(tmpl.get("max_agentic_rounds", 0)),
        }
    except Exception:
        return empty


# ─── Server-side Skill Resolution ──────────────────────────────────────────
# Skills are loaded from /app/skills (mounted from ~/.claude/commands/).
# When a client sends /skill-name [args], the server resolves the skill,
# before the pipeline sees it — works for ANY client (Claude Code,
# Continue.dev, Open Code, Open WebUI etc.) without permanently
# zu belasten.
_SKILLS_DIR = Path("/app/skills")
_COMMUNITY_SKILLS_DIR = _SKILLS_DIR / "community"

_SKILL_FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?(.*)", re.DOTALL)

def _load_skill_body(name: str) -> Optional[str]:
    """Loads a skill by name from built-in or community directory.
    Built-in skills take priority over community skills."""
    search_dirs = [_SKILLS_DIR, _COMMUNITY_SKILLS_DIR]
    for d in search_dirs:
        for candidate in (d / f"{name}.md", d / f"{name}.md.disabled"):
            if candidate.exists() and candidate.suffix == ".md":
                try:
                    raw = candidate.read_text(encoding="utf-8")
                    m = _SKILL_FM_RE.match(raw)
                    return m.group(1).strip() if m else raw.strip()
                except OSError:
                    pass
    return None

def _build_skill_catalog() -> str:
    """Builds a compact skill catalog for the planner prompt.
    Lists available output skills so the planner can suggest output formats."""
    _DESC_RE = re.compile(r"description:\s*(.+?)(?:\n|$)")
    catalog = []
    # Scan both built-in and community skill directories
    for skill_dir in [_SKILLS_DIR, _COMMUNITY_SKILLS_DIR]:
        if not skill_dir.exists():
            continue
        for f in sorted(skill_dir.iterdir()):
            if f.suffix == ".md" and ".disabled" not in f.name and f.stem not in ("_SPEC", "README"):
                try:
                    header = f.read_text(encoding="utf-8")[:500]
                    m = _DESC_RE.search(header)
                    if m:
                        desc = m.group(1).strip()[:100]
                        name = f.stem
                        # Avoid duplicates (built-in takes priority)
                        if not any(name in entry for entry in catalog):
                            catalog.append(f"  /{name}: {desc}")
                except OSError:
                    pass
    if not catalog:
        return ""
    return (
        "\n\nAVAILABLE OUTPUT SKILLS (for non-plaintext deliverables):\n"
        "If the user's request would benefit from a specific output format (PDF, DOCX, HTML, slides, etc.),\n"
        "add an optional field \"output_skill\" to one of your tasks.\n"
        "Only suggest a skill when the request clearly implies a document or visual output.\n"
        + "\n".join(catalog)
        + '\nExample: {"task": "Create visual report", "category": "research", "output_skill": "web-artifacts-builder"}\n'
    )


def _resolve_skill_invocation(text: str, allowed_skills: Optional[list] = None) -> str:
    """Resolves /skill-name [args] server-side (client-independent).
    allowed_skills: None = all allowed (backwards compatible).
                    List = only listed skills or '*' for all allowed.
    Returns unchanged text if no matching skill found or not allowed.

    NOTE: Do not call this directly from async request handlers.
    Use _resolve_skill_secure() which enforces the ADMIN_APPROVED hard-lock.
    """
    if not text or not text.startswith("/"):
        return text
    m = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)(?:[ \t]+(.*))?$", text, re.DOTALL)
    if not m:
        return text
    skill_name, args = m.group(1), (m.group(2) or "").strip()
    # Permission check: if allowed_skills is set and no wildcard, only permitted skills are allowed
    if allowed_skills is not None and "*" not in allowed_skills and skill_name not in allowed_skills:
        logging.getLogger(__name__).info(f"⛔ Skill /{skill_name} not allowed for this user")
        return text
    body = _load_skill_body(skill_name)
    if body is None:
        return text
    resolved = body.replace("$ARGUMENTS", args)
    logging.getLogger(__name__).info(
        f"🎯 Skill resolved: /{skill_name} → {len(resolved)} chars"
    )
    return resolved


# ─── Skill Registry: Hard-Lock (ADMIN_APPROVED gate) ─────────────────────────

async def _ensure_skill_registry_schema() -> None:
    """Creates skill_registry and skill_audit_log tables if they do not exist."""
    if _userdb_pool is None:
        return
    ddl = """
        CREATE TABLE IF NOT EXISTS skill_registry (
            skill_name      TEXT PRIMARY KEY,
            admin_approved  BOOLEAN NOT NULL DEFAULT FALSE,
            approved_by     TEXT,
            approved_at     TIMESTAMPTZ,
            audit_verdict   TEXT CHECK (audit_verdict IN ('safe', 'warning', 'blocked')),
            audit_file      TEXT,
            is_builtin      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS skill_audit_log (
            id              BIGSERIAL PRIMARY KEY,
            skill_name      TEXT NOT NULL,
            user_id         TEXT NOT NULL,
            session_id      TEXT,
            args_hash       TEXT,
            executed_at     TIMESTAMPTZ DEFAULT NOW(),
            outcome         TEXT CHECK (outcome IN ('executed', 'blocked', 'error'))
        );
        CREATE INDEX IF NOT EXISTS idx_skill_audit_log_skill  ON skill_audit_log (skill_name);
        CREATE INDEX IF NOT EXISTS idx_skill_audit_log_user   ON skill_audit_log (user_id);
        CREATE INDEX IF NOT EXISTS idx_skill_audit_log_ts     ON skill_audit_log (executed_at);
    """
    try:
        async with _userdb_pool.connection() as conn:
            await conn.execute(ddl)
        logger.info("✅ Skill registry schema ensured")
    except Exception as e:
        logger.warning(f"⚠️ Skill registry schema setup failed: {e}")


async def _bootstrap_skill_registry() -> None:
    """Populates skill_registry from filesystem on startup (idempotent).

    Built-in skills get admin_approved=TRUE automatically.
    Community skills are approved if their audit.json verdict is 'safe'.
    """
    if _userdb_pool is None:
        return
    import json as _json_mod
    rows: list[tuple] = []
    # Built-in skills — trusted by default
    if _SKILLS_DIR.exists():
        for f in _SKILLS_DIR.iterdir():
            if f.suffix == ".md" and ".disabled" not in f.name and f.stem not in ("_SPEC", "README"):
                rows.append((f.stem, True, None, True))
    # Community skills — approved only if LLM audit passed with 'safe' verdict
    if _COMMUNITY_SKILLS_DIR.exists():
        for f in _COMMUNITY_SKILLS_DIR.iterdir():
            if f.suffix == ".md" and ".disabled" not in f.name:
                audit_path = _COMMUNITY_SKILLS_DIR / f"{f.stem}.audit.json"
                approved = False
                verdict = None
                if audit_path.exists():
                    try:
                        audit = _json_mod.loads(audit_path.read_text())
                        verdict = audit.get("verdict")
                        approved = verdict == "safe"
                    except Exception:
                        pass
                rows.append((f.stem, approved, verdict, False))
    if not rows:
        return
    upsert_sql = """
        INSERT INTO skill_registry (skill_name, admin_approved, audit_verdict, is_builtin)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (skill_name) DO NOTHING
    """
    try:
        async with _userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(upsert_sql, rows)
            await conn.commit()
        approved_count = sum(1 for r in rows if r[1])
        logger.info(f"✅ Skill registry bootstrapped: {len(rows)} skills, {approved_count} approved")
    except Exception as e:
        logger.warning(f"⚠️ Skill registry bootstrap failed: {e}")


async def _check_skill_approved(skill_name: str) -> bool:
    """Returns True if skill_name has admin_approved=TRUE in the registry.

    Fail-secure: if the DB is unavailable, returns False (deny by default).
    """
    if _userdb_pool is None:
        logger.warning(f"⛔ Skill /{skill_name} blocked — DB unavailable (fail-secure)")
        return False
    try:
        async with _userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT admin_approved FROM skill_registry WHERE skill_name = %s",
                    (skill_name,),
                )
                row = await cur.fetchone()
        if row is None:
            logger.warning(f"⛔ Skill /{skill_name} not in registry — blocked")
            return False
        return bool(row[0])
    except Exception as e:
        logger.warning(f"⛔ Skill /{skill_name} approval check failed ({e}) — fail-secure deny")
        return False


async def _log_skill_execution(
    skill_name: str, user_id: str, session_id: Optional[str], args: str, outcome: str
) -> None:
    """Appends a structured audit record for each skill invocation attempt."""
    if _userdb_pool is None:
        return
    args_hash = hashlib.sha256(args.encode()).hexdigest()[:16] if args else None
    try:
        async with _userdb_pool.connection() as conn:
            await conn.execute(
                """INSERT INTO skill_audit_log (skill_name, user_id, session_id, args_hash, outcome)
                   VALUES (%s, %s, %s, %s, %s)""",
                (skill_name, user_id, session_id, args_hash, outcome),
            )
    except Exception as e:
        logger.debug(f"Skill audit log write failed: {e}")


async def _resolve_skill_secure(
    text: str,
    allowed_skills: Optional[list],
    user_id: str = "anon",
    session_id: Optional[str] = None,
) -> str:
    """Secure async wrapper around _resolve_skill_invocation.

    Enforces the ADMIN_APPROVED hard-lock: a skill is only resolved if it has
    an explicit admin_approved=TRUE entry in skill_registry. All invocation
    attempts are written to skill_audit_log for compliance auditing.
    """
    if not text or not text.startswith("/"):
        return text
    m = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)(?:[ \t]+(.*))?$", text, re.DOTALL)
    if not m:
        return text
    skill_name = m.group(1)
    args = (m.group(2) or "").strip()

    approved = await _check_skill_approved(skill_name)
    if not approved:
        await _log_skill_execution(skill_name, user_id, session_id, args, "blocked")
        logger.warning(f"⛔ Skill /{skill_name} hard-locked — ADMIN_APPROVED missing (user={user_id})")
        return text

    resolved = _resolve_skill_invocation(text, allowed_skills=allowed_skills)
    outcome = "executed" if resolved != text else "error"
    await _log_skill_execution(skill_name, user_id, session_id, args, outcome)
    return resolved


# ─── Automatic file skill detection ──────────────────────────────────────
_FILE_SKILL_MAP: Dict[str, str] = {
    ".pdf":  "pdf",
    ".docx": "docx",
    ".doc":  "docx",
    ".xlsx": "xlsx",
    ".xls":  "xlsx",
    ".csv":  "xlsx",
    ".tsv":  "xlsx",
    ".pptx": "pptx",
    ".ppt":  "pptx",
}

_MIME_SKILL_MAP: Dict[str, str] = {
    "application/pdf":                                                                    "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":           "docx",
    "application/msword":                                                                 "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":                 "xlsx",
    "application/vnd.ms-excel":                                                           "xlsx",
    "text/csv":                                                                           "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":         "pptx",
    "application/vnd.ms-powerpoint":                                                      "pptx",
}


def _skill_for_file(name: str = "", mime: str = "") -> Optional[str]:
    """Returns the skill name for a filename or MIME type (or None)."""
    if name:
        ext = Path(name).suffix.lower()
        if ext in _FILE_SKILL_MAP:
            return _FILE_SKILL_MAP[ext]
    if mime:
        clean = mime.split(";")[0].strip().lower()
        if clean in _MIME_SKILL_MAP:
            return _MIME_SKILL_MAP[clean]
    return None


def _detect_file_skill(files: Optional[List], user_input: str,
                       allowed_skills: Optional[list] = None) -> Optional[str]:
    """Detects file type attachments and returns the matching skill name.
    Checks the OpenWebUI files parameter first, then filename patterns in the text.
    Returns None if no matching skill found or not allowed.
    """
    def _allowed(skill: str) -> bool:
        if allowed_skills is None:
            return True
        return "*" in allowed_skills or skill in allowed_skills

    # 1. OpenWebUI files parameter (contains filename / MIME type)
    for f in (files or []):
        if not isinstance(f, dict):
            continue
        name = f.get("name") or f.get("filename") or ""
        mime = f.get("type") or f.get("mime_type") or f.get("content_type") or ""
        # OpenWebUI sometimes nests files as: {"type": "file", "file": {"name": ...}}
        if not name and not mime and isinstance(f.get("file"), dict):
            inner = f["file"]
            name = inner.get("name") or inner.get("filename") or ""
            mime = inner.get("type") or inner.get("mime_type") or ""
        skill = _skill_for_file(name=name, mime=mime)
        if skill and _allowed(skill):
            logger.debug(f"📎 File skill detected via files parameter: /{skill} (name={name!r}, mime={mime!r})")
            return skill

    # 2. Filename pattern in message text (OpenWebUI often injects [filename.ext])
    _ext_pattern = re.compile(r'\b[\w.\-]+\.(pdf|docx?|xlsx?|csv|tsv|pptx?)\b', re.IGNORECASE)
    for m in _ext_pattern.finditer(user_input):
        ext = "." + m.group(1).lower()
        skill = _FILE_SKILL_MAP.get(ext)
        if skill and _allowed(skill):
            logger.debug(f"📎 File skill detected via text pattern: /{skill} (found: {m.group(0)!r})")
            return skill

    return None


# Claude Code / Anthropic-API-compatible model IDs that are accepted and routed internally.
# Default set of Claude model ID prefixes recognized as Claude Code requests.
# Configurable via CLAUDE_CODE_MODELS env var (comma-separated list).
_DEFAULT_CLAUDE_CODE_MODELS = (
    "claude-opus-4-6,claude-sonnet-4-6,claude-haiku-4-5-20251001,"
    "claude-opus-4-5,claude-sonnet-4-5,claude-3-5-sonnet-20241022,"
    "claude-3-5-haiku-20241022,claude-3-7-sonnet-20250219"
)

# ─── Claude Code Integration Profiles ────────────────────────────────────────
# Load all profiles for per-user routing at request time.
# Global CC defaults come exclusively from environment variables (set via Admin UI).
# CC profiles are loaded dynamically via _read_cc_profiles() (60s cache from .env file)

# Global CC defaults — configured in Admin UI and stored as env vars.
CLAUDE_CODE_MODELS = {
    m.strip()
    for m in os.getenv("CLAUDE_CODE_MODELS", _DEFAULT_CLAUDE_CODE_MODELS).split(",")
    if m.strip()
}
CLAUDE_CODE_TOOL_MODEL    = os.getenv("CLAUDE_CODE_TOOL_MODEL", "").strip().rstrip("*")
CLAUDE_CODE_TOOL_ENDPOINT = os.getenv("CLAUDE_CODE_TOOL_ENDPOINT", os.getenv("JUDGE_ENDPOINT", ""))
CLAUDE_CODE_MODE          = os.getenv("CLAUDE_CODE_MODE", "moe_orchestrated")
CLAUDE_CODE_REASONING_MODEL    = os.getenv("CLAUDE_CODE_REASONING_MODEL", "")
CLAUDE_CODE_REASONING_ENDPOINT = os.getenv("CLAUDE_CODE_REASONING_ENDPOINT", "")
_CC_SYSTEM_PREFIX = ""
_CC_STREAM_THINK  = False
CLAUDE_CODE_TOOL_CHOICE = os.getenv("CLAUDE_CODE_TOOL_CHOICE", "auto")

_CLAUDE_CODE_TOOL_URL   = URL_MAP.get(CLAUDE_CODE_TOOL_ENDPOINT) or JUDGE_URL
_CLAUDE_CODE_TOOL_TOKEN = TOKEN_MAP.get(CLAUDE_CODE_TOOL_ENDPOINT, "ollama")
_CLAUDE_CODE_REASONING_URL = (
    URL_MAP.get(CLAUDE_CODE_REASONING_ENDPOINT) if CLAUDE_CODE_REASONING_ENDPOINT else None
)

# Per-endpoint rate limit state (populated from response headers of AIHUB/external provider calls)
# Format: {endpoint_name: {"remaining_tokens": int, "limit_tokens": int, "reset_time": float|None, "exhausted": bool, "updated_at": float}}
_provider_rate_limits: dict = {}


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
# MCP tool schemas for pre-call arg validation: {tool_name: {required: [...], args: {...}}}
MCP_TOOL_SCHEMAS: dict = {}

# Code-navigation tools — not included in MCP_TOOLS_DESCRIPTION,
# only injected into the planner prompt when agentic_coder is active.
_AGENTIC_TOOL_NAMES = {"repo_map", "read_file_chunked", "lsp_query"}
AGENTIC_CODE_TOOLS_DESCRIPTION = ""

# Categories handled by specialized nodes (not by expert LLMs)
NON_EXPERT_CATEGORIES = {"precision_tools", "research"}

# Max chars per expert output towards merger (approx. 600 tokens)
MAX_EXPERT_OUTPUT_CHARS = int(os.getenv("MAX_EXPERT_OUTPUT_CHARS", "2400"))

# Threshold for cache-hit short-circuit (ChromaDB cosine distance)
CACHE_HIT_THRESHOLD   = float(os.getenv("CACHE_HIT_THRESHOLD",   "0.15"))
SOFT_CACHE_THRESHOLD  = float(os.getenv("SOFT_CACHE_THRESHOLD",  "0.50"))
SOFT_CACHE_MAX_EXAMPLES = int(os.getenv("SOFT_CACHE_MAX_EXAMPLES", "2"))

# Semantic pre-routing: maximum distance for direct expert routing (without planner)
ROUTE_THRESHOLD = float(os.getenv("ROUTE_THRESHOLD", "0.18"))
# Minimum gap between top-1 and top-2 match scores — prevents misrouting on ambiguous requests
ROUTE_GAP       = float(os.getenv("ROUTE_GAP",       "0.10"))

# Minimum response length to be cached in ChromaDB
CACHE_MIN_RESPONSE_LEN = int(os.getenv("CACHE_MIN_RESPONSE_LEN", "150"))

# Expert-Routing-Parameter
EXPERT_TIER_BOUNDARY_B  = float(os.getenv("EXPERT_TIER_BOUNDARY_B", "20"))
EXPERT_MIN_SCORE        = float(os.getenv("EXPERT_MIN_SCORE",        "0.3"))
EXPERT_MIN_DATAPOINTS   = int(os.getenv("EXPERT_MIN_DATAPOINTS",     "5"))

# Conversation history limits
HISTORY_MAX_TURNS = int(os.getenv("HISTORY_MAX_TURNS", "4"))
HISTORY_MAX_CHARS = int(os.getenv("HISTORY_MAX_CHARS", "3000"))

# Monitoring history limit
HISTORY_MAX_ENTRIES = int(os.getenv("HISTORY_MAX_ENTRIES", "5000"))

# Timeouts (seconds)
JUDGE_TIMEOUT   = int(os.getenv("JUDGE_TIMEOUT",   "900"))
EXPERT_TIMEOUT  = int(os.getenv("EXPERT_TIMEOUT",  "900"))
PLANNER_TIMEOUT = int(os.getenv("PLANNER_TIMEOUT", "300"))

# Judge Refinement Loop
JUDGE_REFINE_MAX_ROUNDS      = int(os.getenv("JUDGE_REFINE_MAX_ROUNDS",      "2"))
JUDGE_REFINE_MIN_IMPROVEMENT = float(os.getenv("JUDGE_REFINE_MIN_IMPROVEMENT", "0.15"))

# Token limits for the Anthropic tool handler — configured via Admin UI
TOOL_MAX_TOKENS      = int(os.getenv("TOOL_MAX_TOKENS",      "8192"))
REASONING_MAX_TOKENS = int(os.getenv("REASONING_MAX_TOKENS", "16384"))

# Planner configuration
PLANNER_RETRIES  = int(os.getenv("PLANNER_RETRIES",  "2"))
PLANNER_MAX_TASKS = int(os.getenv("PLANNER_MAX_TASKS", "4"))

# Streaming chunk size (chars per SSE partial packet)
SSE_CHUNK_SIZE = int(os.getenv("SSE_CHUNK_SIZE", "50"))

# Feedback and self-evaluation thresholds
EVAL_CACHE_FLAG_THRESHOLD  = int(os.getenv("EVAL_CACHE_FLAG_THRESHOLD",  "2"))
FEEDBACK_POSITIVE_THRESHOLD = int(os.getenv("FEEDBACK_POSITIVE_THRESHOLD", "4"))
FEEDBACK_NEGATIVE_THRESHOLD = int(os.getenv("FEEDBACK_NEGATIVE_THRESHOLD", "2"))

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

# Custom prompts set via Admin UI (CUSTOM_EXPERT_PROMPTS env-var, JSON)
_CUSTOM_EXPERT_PROMPTS: Dict[str, str] = {}
try:
    _CUSTOM_EXPERT_PROMPTS = json.loads(os.getenv("CUSTOM_EXPERT_PROMPTS", "{}"))
except Exception:
    pass

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

# --- PROMETHEUS METRICS ---
PROM_TOKENS          = Counter('moe_tokens_total',             'Total tokens processed',      ['model', 'token_type', 'node', 'user_id'])
PROM_REQUESTS        = Counter('moe_requests_total',           'Total requests',              ['mode', 'cache_hit', 'user_id'])
PROM_BUDGET_EXCEEDED = Counter('moe_budget_exceeded_total',    'Budget limit exceeded',       ['user_id', 'limit_type'])
PROM_EXPERT_CALLS    = Counter('moe_expert_calls_total',       'Expert model calls',          ['model', 'category', 'node'])
PROM_CONFIDENCE      = Counter('moe_expert_confidence_total',  'Expert confidence level',     ['level', 'category'])
PROM_CACHE_HITS      = Counter('moe_cache_hits_total',         'Cache hits')
PROM_CACHE_MISSES    = Counter('moe_cache_misses_total',       'Cache misses')
PROM_RESPONSE_TIME   = Histogram('moe_response_duration_seconds', 'Response duration',         ['mode'],
                                 buckets=[1, 2, 5, 10, 20, 30, 60, 120])
PROM_SELF_EVAL       = Histogram('moe_self_eval_score',        'Self-evaluation score',       buckets=[1,2,3,4,5,6])
PROM_FEEDBACK        = Histogram('moe_feedback_score',         'User feedback score',         buckets=[1,2,3,4,5,6])
PROM_CHROMA_DOCS     = Gauge('moe_chroma_documents_total',     'Documents in ChromaDB cache')
PROM_GRAPH_ENTITIES  = Gauge('moe_graph_entities_total',       'Entities in Neo4j')
PROM_GRAPH_RELATIONS = Gauge('moe_graph_relations_total',      'Relations in Neo4j')
PROM_PLANNER_PATS    = Gauge('moe_planner_patterns_total',     'Successful planner patterns')
PROM_ONTOLOGY_GAPS   = Gauge('moe_ontology_gaps_total',        'Ontology gap terms')
PROM_ONTOLOGY_ENTS   = Gauge('moe_ontology_entities_total',    'Ontology entities (static)')
PROM_TOOL_CALL_DURATION = Histogram(
    'moe_tool_call_duration_seconds', 'Duration of individual tool model calls',
    ['node', 'model', 'phase'],
    buckets=[1, 2, 5, 10, 20, 30, 60, 120, 300, 600]
)
PROM_TOOL_TIMEOUTS      = Counter('moe_tool_call_timeout_total',       'Timeout errors in tool model calls',         ['node', 'model'])
PROM_TOOL_FORMAT_ERRORS = Counter('moe_tool_call_format_errors_total', 'Malformed tool call responses',              ['node', 'model', 'format'])
PROM_TOOL_CALL_SUCCESS  = Counter('moe_tool_call_success_total',       'Successful tool call responses',             ['node', 'model'])
PROM_COMPLEXITY         = Counter('moe_complexity_routing_total',       'Request complexity routing',                 ['level'])
# --- LIVE STATUS METRICS ---
PROM_ACTIVE_REQUESTS    = Gauge('moe_active_requests',                 'Active requests in progress')
PROM_SERVER_UP          = Gauge('moe_inference_server_up',             'Inference server reachable (1=up, 0=down)',   ['server'])
PROM_SERVER_MODELS      = Gauge('moe_available_models_total',          'Available models per inference server',       ['server'])
# Ollama runtime metrics scraped via /api/ps (no native /metrics endpoint upstream).
PROM_SERVER_LOADED_MODELS    = Gauge('moe_inference_server_loaded_models', 'Currently loaded models in VRAM (Ollama /api/ps)', ['server'])
PROM_SERVER_VRAM_BYTES       = Gauge('moe_inference_server_vram_bytes',    'Total VRAM bytes used by loaded models',           ['server'])
PROM_SERVER_MODEL_VRAM_BYTES = Gauge('moe_inference_server_model_vram_bytes', 'VRAM bytes used by a specific loaded model',    ['server', 'model'])
PROM_SYNTHESIS_NODES    = Gauge('moe_graph_synthesis_nodes_total',     'Synthesis-Nodes in Neo4j (:Synthesis)')
PROM_FLAGGED_RELS       = Gauge('moe_graph_flagged_relations_total',   'Flagged relations in Neo4j (r.flagged=true)')
PROM_LINTING_RUNS       = Counter('moe_linting_runs_total',            'Total graph linting runs')
PROM_LINTING_ORPHANS    = Counter('moe_linting_orphans_deleted_total', 'Orphaned nodes deleted by linting')
PROM_LINTING_CONFLICTS  = Counter('moe_linting_conflicts_resolved_total', 'Conflicts resolved by linting')
PROM_LINTING_DECAY      = Counter('moe_linting_decay_deleted_total', 'Relations deleted by confidence decay')
PROM_QUARANTINE_ADDED   = Counter('moe_quarantine_added_total', 'Triples quarantined by blast-radius check')
PROM_SYNTHESIS_CREATED  = Counter('moe_synthesis_persisted_total',     'Synthesis nodes persisted',               ['domain', 'insight_type'])
# --- RL FLYWHEEL & CONTEXT WINDOW METRICS ---
PROM_HISTORY_COMPRESSED = Counter('moe_history_compressed_total',      'History compression events (turns truncated)')
PROM_HISTORY_UNLIMITED  = Counter('moe_history_unlimited_total',       'Requests with compression disabled (per-template)')
PROM_CORRECTIONS_STORED = Counter('moe_corrections_stored_total',      'Correction memory entries written to Neo4j',   ['source'])
PROM_CORRECTIONS_INJECTED = Counter('moe_corrections_injected_total',  'Correction memory entries injected into expert prompts', ['category'])
PROM_JUDGE_REFINED      = Counter('moe_judge_refinement_total',        'Judge refinement rounds executed',             ['outcome'])
PROM_EXPERT_FAILURES    = Counter('moe_expert_failures_total',         'Expert invocation failures (VRAM, timeout, error)', ['model', 'reason'])
PROM_GRAPH_DENSITY      = Gauge('moe_graph_density_ratio',             'Relations per entity (graph connectivity measure)')

# --- USER AUTH & USAGE TRACKING ---

async def _db_fallback_key_lookup(key_hash: str) -> Optional[dict]:
    """Fallback: validate API key directly from Postgres (on Valkey cache miss) and sync."""
    try:
        if _userdb_pool is None:
            return None
        async with _userdb_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT ak.*, u.is_active AS user_active, u.id AS uid "
                    "FROM api_keys ak JOIN users u ON ak.user_id = u.id "
                    "WHERE ak.key_hash=%s AND ak.is_active=TRUE AND u.is_active=TRUE",
                    (key_hash,),
                )
                row = await cur.fetchone()
        if not row:
            return None
        user_id = row["user_id"]
        # Re-sync in Valkey so next requests are fast. Relies on admin_ui.database's
        # own pool — initialised in lifespan(); logs loudly if that failed.
        try:
            from admin_ui.database import sync_user_to_redis as _sync
            await _sync(user_id)
        except Exception as _sync_err:
            logger.warning("sync_user_to_redis failed in fallback for user %s: %s",
                           user_id, _sync_err)
        if redis_client:
            data = await redis_client.hgetall(f"user:apikey:{key_hash}")
            if data and data.get("is_active") == "1":
                return data
        return None
    except Exception as e:
        logger.warning(f"DB fallback auth error: {e}")
        return None


async def _fetch_jwks() -> Optional[dict]:
    """Fetch and cache JWKS from Authentik (10 min TTL)."""
    global _jwks_cache
    with _cache_lock:
        keys, fetched_at = _jwks_cache
    if keys and (time.time() - fetched_at) < 600:
        return keys
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(OIDC_JWKS_URL)
            if r.status_code == 200:
                with _cache_lock:
                    _jwks_cache = (r.json(), time.time())
                return r.json()
    except Exception as e:
        logger.warning("JWKS fetch failed: %s", e)
    return keys  # return stale cache if available


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


async def _validate_api_key(raw_key: str) -> Optional[dict]:
    """Validate API key or OIDC JWT.
    - Tokens starting with 'moe-sk-': legacy API key path
    - Other Bearer tokens: OIDC JWT path (if OIDC enabled)
    Returns user-dict on success, {"error": "..."} on failure."""
    if not raw_key:
        return {"error": "invalid_key"}
    # OIDC JWT path (tokens not starting with moe-sk-)
    if OIDC_ENABLED and not raw_key.startswith("moe-sk-"):
        oidc_ctx = await _validate_oidc_token(raw_key)
        if oidc_ctx:
            return oidc_ctx
        return {"error": "invalid_key"}
    # Legacy API key path
    if not raw_key.startswith("moe-sk-"):
        return {"error": "invalid_key"}
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    if redis_client is None:
        return {"error": "invalid_key"}
    try:
        data = await redis_client.hgetall(f"user:apikey:{key_hash}")
        if not data or data.get("is_active") != "1":
            # Cache miss: check directly in DB and re-sync if needed
            data = await _db_fallback_key_lookup(key_hash)
            if not data:
                return {"error": "invalid_key"}
        # Budget-Check
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        month = date.today().strftime("%Y-%m")
        uid   = data.get("user_id", "")
        if data.get("budget_daily"):
            used = int(await redis_client.get(f"user:{uid}:tokens:daily:{today}") or 0)
            if used >= int(data["budget_daily"]):
                PROM_BUDGET_EXCEEDED.labels(user_id=uid, limit_type="daily").inc()
                return {"error": "budget_exceeded", "limit_type": "daily"}
        if data.get("budget_monthly"):
            used = int(await redis_client.get(f"user:{uid}:tokens:monthly:{month}") or 0)
            if used >= int(data["budget_monthly"]):
                PROM_BUDGET_EXCEEDED.labels(user_id=uid, limit_type="monthly").inc()
                return {"error": "budget_exceeded", "limit_type": "monthly"}
        if data.get("budget_total"):
            used = int(await redis_client.get(f"user:{uid}:tokens:total") or 0)
            if used >= int(data["budget_total"]):
                PROM_BUDGET_EXCEEDED.labels(user_id=uid, limit_type="total").inc()
                return {"error": "budget_exceeded", "limit_type": "total"}
        # Team budget check — pre-emptive, uses estimated 0 tokens to gate access
        if uid and _userdb_pool is not None:
            try:
                from admin_ui.database import get_user_teams as _get_user_teams
                from admin_ui.database import check_team_budget as _check_team_budget
                for _team_id in await _get_user_teams(uid):
                    _ok, _reason = await _check_team_budget(_team_id, 0)
                    if not _ok:
                        PROM_BUDGET_EXCEEDED.labels(user_id=uid, limit_type="team").inc()
                        return {"error": "budget_exceeded", "limit_type": "team",
                                "team_id": _team_id, "message": _reason}
            except Exception as _be:
                logger.debug(f"Team budget check skipped: {_be}")
        return data
    except Exception as e:
        logger.warning(f"Auth error: {e}")
        return {"error": "invalid_key"}


def _extract_api_key(request: Request) -> Optional[str]:
    """Extract API key from Authorization header or x-api-key."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip() or None


def _extract_session_id(request: Request) -> Optional[str]:
    """Extract session ID from known client headers.

    Supported clients and their headers:
    - Claude Code / Anthropic SDK:  x-stainless-session-id
    - Continue.dev:                 x-request-id (consistent per conversation)
    - OpenCode / OpenAI SDK:        x-stainless-session-id (Stainless framework)
    - Cline / Claw-Code:            x-stainless-session-id or x-session-id
    - Generic:                      x-session-id, x-conversation-id
    """
    h = request.headers
    return (
        h.get("x-stainless-session-id") or
        h.get("x-session-id") or
        h.get("x-conversation-id") or
        h.get("x-request-id") or
        None
    )


async def _log_usage_to_db(user_id: str, api_key_id: str, request_id: str,
                            model: str, moe_mode: str,
                            prompt_tokens: int, completion_tokens: int,
                            status: str = "ok", session_id: str = None) -> None:
    """Fire-and-forget Postgres usage log. Never raises exceptions."""
    try:
        if _userdb_pool is None:
            return
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        async with _userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO usage_log "
                    "(id,user_id,api_key_id,request_id,session_id,model,moe_mode,prompt_tokens,"
                    "completion_tokens,total_tokens,status,requested_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (uuid.uuid4().hex, user_id, api_key_id or None, request_id,
                     session_id or None, model, moe_mode, prompt_tokens, completion_tokens,
                     prompt_tokens + completion_tokens, status, now_iso),
                )
                await cur.execute(
                    "UPDATE api_keys SET last_used_at=%s WHERE user_id=%s AND is_active=TRUE",
                    (now_iso, user_id),
                )
    except Exception as e:
        logger.warning(f"Usage log failed: {e}")


async def _register_active_request(chat_id: str, user_id: str, model: str,
                                    moe_mode: str, req_type: str,
                                    template_name: str = "", client_ip: str = "",
                                    backend_model: str = "", backend_host: str = "",
                                    api_key_id: str = "") -> None:
    """Registers a running request in Valkey for live monitoring."""
    if redis_client is None:
        return
    try:
        # Look up key label and key prefix from DB for live monitoring
        _key_label = ""
        _key_prefix = ""
        if api_key_id and _userdb_pool is not None:
            try:
                async with _userdb_pool.connection() as _conn:
                    async with _conn.cursor(row_factory=dict_row) as _cur:
                        await _cur.execute(
                            "SELECT label, key_prefix FROM api_keys WHERE id=%s",
                            (api_key_id,),
                        )
                        _row = await _cur.fetchone()
                        if _row:
                            _key_label  = _row["label"] or ""
                            _key_prefix = _row["key_prefix"] or ""
            except Exception:
                pass
        meta = {
            "chat_id":       chat_id,
            "user_id":       user_id,
            "model":         model,
            "moe_mode":      moe_mode,
            "type":          req_type,
            "template_name": template_name,
            "client_ip":     client_ip,
            "backend_model": backend_model,
            "backend_host":  backend_host,
            "api_key_id":    api_key_id,
            "key_label":     _key_label,
            "key_prefix":    _key_prefix,
            "started_at":    datetime.utcnow().isoformat() + "Z",
        }
        await redis_client.set(f"moe:active:{chat_id}", json.dumps(meta), ex=7200)
    except Exception as e:
        logger.debug(f"Active request registration failed: {e}")


async def _deregister_active_request(chat_id: str) -> None:
    """Remove a completed request from Valkey live monitoring and write it to the history."""
    if redis_client is None:
        return
    try:
        key = f"moe:active:{chat_id}"
        raw = await redis_client.get(key)
        if raw:
            try:
                meta = json.loads(raw)
                meta["status"] = "completed"
                meta["ended_at"] = datetime.now(timezone.utc).isoformat()
                score = datetime.now(timezone.utc).timestamp()
                await redis_client.zadd("moe:admin:completed", {json.dumps(meta, default=str): score})
                await redis_client.zremrangebyrank("moe:admin:completed", 0, -(HISTORY_MAX_ENTRIES + 1))
            except Exception as _he:
                logger.warning("History entry failed: %s", _he)
        await redis_client.delete(key)
    except Exception as e:
        logger.debug(f"Active request deregister failed: {e}")


# ---------------------------------------------------------------------------
# Model availability check with short cache (avoids spam against the nodes)
# ---------------------------------------------------------------------------
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


async def _increment_user_budget(user_id: str, tokens: int,
                                  prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """Increments the Valkey budget counter for a user (with cost factor of the assigned template).
    Tracks total, input, and output tokens separately."""
    if not user_id or user_id == "anon" or redis_client is None:
        return
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    month = date.today().strftime("%Y-%m")
    try:
        # Apply template cost factor (default 1.0 = neutral)
        cost_factor = 1.0
        cf_raw = await redis_client.get(f"user:{user_id}:cost_factor")
        if cf_raw:
            cost_factor = float(cf_raw)
        effective_tokens = max(1, round(tokens * cost_factor))
        eff_prompt     = round(prompt_tokens * cost_factor)
        eff_completion = round(completion_tokens * cost_factor)
        pipe = redis_client.pipeline()
        # Total counter (for budget limits)
        pipe.incrby(f"user:{user_id}:tokens:daily:{today}", effective_tokens)
        pipe.expire(f"user:{user_id}:tokens:daily:{today}", 48 * 3600)
        pipe.incrby(f"user:{user_id}:tokens:monthly:{month}", effective_tokens)
        pipe.expire(f"user:{user_id}:tokens:monthly:{month}", 35 * 86400)
        pipe.incrby(f"user:{user_id}:tokens:total", effective_tokens)
        # Input-Token-Counter
        if eff_prompt > 0:
            pipe.incrby(f"user:{user_id}:tokens:daily:{today}:input", eff_prompt)
            pipe.expire(f"user:{user_id}:tokens:daily:{today}:input", 48 * 3600)
            pipe.incrby(f"user:{user_id}:tokens:monthly:{month}:input", eff_prompt)
            pipe.expire(f"user:{user_id}:tokens:monthly:{month}:input", 35 * 86400)
            pipe.incrby(f"user:{user_id}:tokens:total:input", eff_prompt)
        # Output-Token-Counter
        if eff_completion > 0:
            pipe.incrby(f"user:{user_id}:tokens:daily:{today}:output", eff_completion)
            pipe.expire(f"user:{user_id}:tokens:daily:{today}:output", 48 * 3600)
            pipe.incrby(f"user:{user_id}:tokens:monthly:{month}:output", eff_completion)
            pipe.expire(f"user:{user_id}:tokens:monthly:{month}:output", 35 * 86400)
            pipe.incrby(f"user:{user_id}:tokens:total:output", eff_completion)
        await pipe.execute()
    except Exception as e:
        logger.warning(f"Budget counter failed: {e}")
    # Deduct from all team budgets the user belongs to (fire-and-forget)
    if _userdb_pool is not None:
        try:
            from admin_ui.database import get_user_teams as _get_user_teams
            from admin_ui.database import deduct_team_budget as _deduct_team_budget
            for _team_id in await _get_user_teams(user_id):
                await _deduct_team_budget(_team_id, effective_tokens)
        except Exception as _tbe:
            logger.debug(f"Team budget deduct failed: {_tbe}")

# --- CONCURRENCY CONTROL (per endpoint, dynamic from INFERENCE_SERVERS_LIST) ---
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
chroma_client = chromadb.HttpClient(host=os.getenv("CHROMA_HOST", "chromadb-vector"), port=8000)
default_ef = embedding_functions.DefaultEmbeddingFunction()
cache_collection = chroma_client.get_or_create_collection(name="moe_fact_cache", embedding_function=default_ef)
# Second collection for semantic pre-routing: prototypical task queries per category
route_collection = chroma_client.get_or_create_collection(name="task_type_prototypes", embedding_function=default_ef)

# --- MODES ---
# Control output format and behavior of the entire pipeline.
# Open WebUI displays each entry as a separate model.
MODES: Dict[str, Dict] = {
    "default": {
        "model_id":       "moe-orchestrator",
        "description":    "Complete answers with explanations (default)",
        "expert_suffix":  "",
        "merger_prefix":  (
            "Synthesize the following information into a clear, complete answer.\n"
            "Priority: MCP calculations > knowledge graph > experts (high/medium) > web > experts (low) > cache.\n"
            "Ignore contradictory expert statements when MCP or graph data are available.\n"
            "Act as cross-domain validator: compare all numerical values in the expert answers "
            "with the original request. In case of discrepancies, the original value has absolute priority. "
            "If an expert ignored a subtask, name the gap explicitly instead of filling it with "
            "hallucinations.\n"
            "CRITICAL: If the original question has multiple parts (a), (b), (c), you MUST address "
            "EVERY part in your synthesis. Each expert may have answered a different part — combine "
            "them all. When an MCP tool provided a calculation result, copy the result VERBATIM.\n"
            "LANGUAGE: Always answer in the same language as the original question."
        ),
    },
    "code": {
        "model_id":       "moe-orchestrator-code",
        "description":    "Source code only — no explanations, no prose",
        "expert_suffix":  (
            "\n\nIMPORTANT: Answer EXCLUSIVELY with runnable source code. "
            "No introduction, no explanations, no conclusion. "
            "Inline comments in code are allowed. "
            "Add as first line: # CONFIDENCE: high | medium | low"
        ),
        "merger_prefix":  (
            "Return ONLY the finished, complete, runnable source code. "
            "Absolutely no prose, no introduction, no explanations, no conclusion. "
            "Combine and deduplicate the expert code suggestions into the best result. "
            "Code only."
        ),
    },
    "concise": {
        "model_id":       "moe-orchestrator-concise",
        "description":    "Short, precise answers without rambling",
        "expert_suffix":  (
            "\n\nAnswer very briefly and precisely. Maximum 4 sentences. "
            "No repetitions, no introduction, no conclusion."
        ),
        "merger_prefix":  (
            "Synthesize into a short, precise answer. "
            "Maximum 120 words. No introduction, no conclusion, no repetitions. "
            "Priority: MCP > knowledge graph > experts > web."
        ),
        "skip_think": False,
    },
    "agent": {
        "model_id":        "moe-orchestrator-agent",
        "description":     "Coding agent mode — for OpenCode, Continue.dev and AI coding tools (OpenAI-compatible)",
        "expert_suffix":   (
            "\n\nYou are a precise coding agent. Answer technically exact and directly actionable. "
            "Use markdown code blocks for all code. No unnecessary explanations unless explicitly asked."
        ),
        "merger_prefix":   (
            "Synthesize the expert code analyses into one precise, actionable response. "
            "Use markdown code blocks for all code. Be concise and direct. "
            "Do not add preamble or boilerplate. Start with the solution immediately. "
            "Priority: code_reviewer > technical_support."
        ),
        "skip_think":      True,     # No <think> block — OpenCode/Continue.dev render it as raw text
        "force_categories": ["code_reviewer", "technical_support"],
    },
    "agent_orchestrated": {
        "model_id":        "moe-orchestrator-agent-orchestrated",
        "description":     "Claude Code — full planner, all experts, force_think (maximum quality)",
        "expert_suffix":   (
            "\n\nYou are a precise coding agent. Answer technically exact and directly actionable. "
            "Use markdown code blocks for all code. No unnecessary explanations unless explicitly asked."
        ),
        "merger_prefix":   (
            "Synthesize the expert analyses into one precise, actionable response. "
            "Use markdown code blocks for all code. Be concise and direct. "
            "Do not add preamble or boilerplate. Start with the solution immediately. "
            "Priority: code_reviewer > technical_support > general."
        ),
        "skip_think":      True,     # no <think> SSE wrapper (Claude Code renders it as raw text)
        "force_think":     True,     # thinking_node active in pipeline for better synthesis
        # no force_categories → planner decides freely, load distributes across all nodes
    },
    "research": {
        "model_id":    "moe-orchestrator-research",
        "description": "Deep research — multiple parallel web searches, structured research report",
        "expert_suffix": (
            "\n\nYou support a structured deep research. "
            "Analyze the topic from your domain perspective. "
            "Explicitly name: established knowledge, open questions and research gaps. "
            "Cite sources when known."
        ),
        "merger_prefix": (
            "Create a structured research report from the following sources.\n"
            "Use exactly this outline:\n\n"
            "## Summary\n[2-3 sentence key statement]\n\n"
            "## Main Findings\n[Most important insights as bullet points]\n\n"
            "## Details\n[In-depth analysis with all relevant information]\n\n"
            "## Sources\n[All cited web sources with [N] numbers]\n\n"
            "Priority: web research > knowledge graph > experts (high/medium) > experts (low).\n"
            "Ignore contradictory expert statements when web research data is available."
        ),
        "force_think": True,
    },
    "report": {
        "model_id":    "moe-orchestrator-report",
        "description": "Professional report — full pipeline, structured markdown report",
        "expert_suffix": (
            "\n\nYou deliver inputs for a professional technical report. "
            "Write objectively, precisely and in report style. "
            "Clearly separate facts from evaluations. "
            "Structure your contribution with short paragraphs."
        ),
        "merger_prefix": (
            "Create a professional technical report in Markdown format.\n"
            "Use exactly this structure:\n\n"
            "# [Meaningful title]\n\n"
            "## Executive Summary\n[3-5 sentences — key message for decision makers]\n\n"
            "## Background\n[Context, relevance, initial situation]\n\n"
            "## Main Findings\n[Most important insights, backed by experts and research]\n\n"
            "## Analysis\n[In-depth evaluation, connections, implications]\n\n"
            "## Conclusion\n[Conclusions and, if applicable, recommendations for action]\n\n"
            "## Sources\n[All cited sources]\n\n"
            "Tone: objective, professional, suitable for authorities or companies.\n"
            "Priority: MCP calculations > knowledge graph > experts (high/medium) > web > experts (low)."
        ),
        "force_think": True,
    },
    "plan": {
        "model_id":    "moe-orchestrator-plan",
        "description": "Plan & Execute — shows execution plan, runs all tasks in parallel",
        "expert_suffix": (
            "\n\nYou deliver inputs for comprehensive planning. "
            "Be complete and precise. Explicitly name what you know and what is uncertain."
        ),
        "merger_prefix": (
            "Synthesize all expert inputs and research into a complete, "
            "structured answer.\n"
            "Consider all available sources. Prioritize accuracy over brevity.\n"
            "Priority: MCP calculations > knowledge graph > experts (high/medium) > web > experts (low).\n"
            "Ignore contradictory expert statements when MCP or graph data are available."
        ),
        "force_think": True,
    },
}
# Model ID → mode key lookup
_MODEL_ID_TO_MODE: Dict[str, str] = {v["model_id"]: k for k, v in MODES.items()}

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

class FeedbackRequest(BaseModel):
    response_id: str           # chat_id from the response ("chatcmpl-...")
    rating: int                # 1-5  (1-2=negativ, 3=neutral, 4-5=positiv)
    correction: Optional[str] = None  # optional corrected response

# AgentState is defined in pipeline/state.py and imported at the top of this file.
# See that module for full field documentation grouped by purpose.

# Zentrale Komponenten
JUDGE_MODEL   = os.getenv("JUDGE_MODEL", "magistral:24b")
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
_SEARXNG_URL = os.getenv("SEARXNG_URL", "").strip()
search: Optional[SearxSearchWrapper] = (
    SearxSearchWrapper(searx_host=_SEARXNG_URL) if _SEARXNG_URL else None
)
if search is None:
    logger.info("SEARXNG_URL not set — web search disabled")
graph_manager:  Optional[GraphRAGManager]  = None
redis_client:   Optional[aioredis.Redis]   = None
kafka_producer: Optional[AIOKafkaProducer] = None

# ─── LEARNING HELPERS ────────────────────────────────────────────────────────

def _perf_key(model: str, category: str) -> str:
    """Valkey key for expert performance: moe:perf:{model}:{category}"""
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", model)
    return f"moe:perf:{safe}:{category}"

async def _ollama_unload(model: str, base_url: str) -> None:
    """Unloads a model immediately from Ollama VRAM via native API (keep_alive=0).
    Fire-and-forget — errors are ignored, pipeline continues."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                f"{base_url}/api/generate",
                json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
            )
        logger.debug(f"🗑️ VRAM: {model} unloaded")
    except Exception as e:
        logger.debug(f"⚠️ VRAM-Unload {model}: {e}")


def _server_info(endpoint_name: str) -> dict:
    """Returns the full server configuration for an endpoint name."""
    return next((s for s in INFERENCE_SERVERS_LIST if s["name"] == endpoint_name), {})


# ─── AIHUB Fallback (configurable via environment) ───────────────────────────
# When AIHUB (or any remote LLM endpoint) is unstable, the orchestrator can
# transparently fall back to a local inference node. All three variables must
# be set for the fallback to activate — an empty string disables the fallback.
# The node name must match an entry in INFERENCE_SERVERS (configured via admin UI).

_AIHUB_FALLBACK_NODE         = os.getenv("AIHUB_FALLBACK_NODE", "")          # e.g. "N04-RTX"
_AIHUB_FALLBACK_MODEL        = os.getenv("AIHUB_FALLBACK_MODEL", "")         # e.g. "qwen3.6:35b"
_AIHUB_FALLBACK_MODEL_SECOND = os.getenv("AIHUB_FALLBACK_MODEL_SECOND", "")  # e.g. "gemma4:31b"

# Keep legacy aliases so existing code references resolve without a sweep
_N04_FALLBACK_NODE          = _AIHUB_FALLBACK_NODE
_N04_FALLBACK_MODEL         = _AIHUB_FALLBACK_MODEL
_N04_FALLBACK_MODEL_SECOND  = _AIHUB_FALLBACK_MODEL_SECOND

_FALLBACK_ENABLED = bool(_AIHUB_FALLBACK_NODE and _AIHUB_FALLBACK_MODEL)

# Per-endpoint degraded state: {url → monotonic timestamp of last failure}
_aihub_degraded: dict[str, float] = {}
_AIHUB_DEGRADED_TTL = 300  # 5 min blackout window after auth/quota failure

# AIHUB retry config: try N times with short delay BEFORE triggering the fallback.
# The AIHUB cluster is sometimes transiently unstable (not quota-limited), so a
# quick retry succeeds without burning the N04-RTX fallback unnecessarily.
_AIHUB_RETRY_COUNT = 3    # retries before declaring endpoint degraded
_AIHUB_RETRY_DELAY = 2.0  # seconds between retries (short — transient instability)


def _is_aihub_error(exc: Exception) -> bool:
    """Return True when the exception signals AIHUB is unavailable (auth/quota)."""
    s = str(exc).lower()
    return any(k in s for k in ("401", "unauthorized", "403", "forbidden",
                                 "429", "rate limit", "quota exceeded",
                                 "authentication", "x-api-key"))


def _mark_endpoint_degraded(url: str) -> None:
    _aihub_degraded[url] = time.monotonic()
    logger.warning("⚠️ Endpoint marked degraded (5 min): %s", url)


def _endpoint_is_degraded(url: str) -> bool:
    ts = _aihub_degraded.get(url)
    if ts is None:
        return False
    if time.monotonic() - ts > _AIHUB_DEGRADED_TTL:
        _aihub_degraded.pop(url, None)
        return False
    return True


async def _get_n04_fallback_llm(timeout: float = 120.0, model: str = "") -> "ChatOpenAI":
    """Return a ChatOpenAI pointing to the configured local fallback node.

    model: override which fallback model to use. Defaults to AIHUB_FALLBACK_MODEL.
           Raises RuntimeError when fallback is not configured (AIHUB_FALLBACK_NODE empty).
    """
    if not _FALLBACK_ENABLED:
        raise RuntimeError(
            "No local fallback configured. Set AIHUB_FALLBACK_NODE and "
            "AIHUB_FALLBACK_MODEL environment variables to enable."
        )
    url = URL_MAP.get(_AIHUB_FALLBACK_NODE)
    if not url:
        raise RuntimeError(
            f"Fallback node '{_AIHUB_FALLBACK_NODE}' is not in the configured "
            "inference servers (INFERENCE_SERVERS env var)."
        )
    token = TOKEN_MAP.get(_AIHUB_FALLBACK_NODE, "ollama")
    return ChatOpenAI(
        model=model or _AIHUB_FALLBACK_MODEL,
        base_url=url,
        api_key=token,
        timeout=timeout,
    )


def _is_aihub_url(url: str) -> bool:
    """Return True when the URL points to an AIHUB endpoint (not a local Ollama node)."""
    u = url.lower()
    return "adesso-ai-hub" in u or ("aihub" in u and "ollama" not in u)


async def _invoke_llm_with_aihub_fallback(
    primary_llm: "ChatOpenAI",
    primary_url: str,
    prompt,
    timeout: float = 120.0,
    label: str = "LLM",
) -> tuple:
    """Invoke primary_llm; on AIHUB auth/quota error OR empty response, retry with N04-RTX.

    Handles two failure modes:
    1. Exception (401, 429, connection error) — caught in except block.
    2. Silent empty body (HTTP 200 with no content) — detected after ainvoke returns.

    Returns (result, used_fallback: bool).
    """
    _on_aihub = _is_aihub_url(primary_url)

    async def _try_n04(reason: str, model: str = "") -> tuple:
        """Try the given N04-RTX model. Returns (res, True) on success."""
        _mark_endpoint_degraded(primary_url)
        _fb_model = model or _N04_FALLBACK_MODEL
        logger.warning("🔄 %s: %s — falling back to %s@%s",
                       label, reason, _fb_model, _N04_FALLBACK_NODE)
        fb_llm = await _get_n04_fallback_llm(timeout, model=_fb_model)
        fb_res = await fb_llm.ainvoke(prompt)
        return fb_res, True

    async def _try_fallback_chain(reason: str) -> tuple:
        """Try primary fallback model, then second-tier fallback model.

        Does nothing (re-raises) when fallback is not configured via env vars.
        """
        if not _FALLBACK_ENABLED:
            logger.warning("⚠️ %s: %s — no local fallback configured (AIHUB_FALLBACK_NODE/MODEL not set)",
                           label, reason)
            raise RuntimeError(f"{label} failed and no fallback configured: {reason}")

        try:
            res, used = await _try_n04(reason, _AIHUB_FALLBACK_MODEL)
            logger.info("✅ %s: Fallback (%s@%s) succeeded", label, _AIHUB_FALLBACK_MODEL, _AIHUB_FALLBACK_NODE)
            return res, used
        except Exception as fe1:
            if _AIHUB_FALLBACK_MODEL_SECOND:
                logger.warning("⚠️ %s: Primary fallback (%s) failed: %s — trying %s",
                               label, _AIHUB_FALLBACK_MODEL, str(fe1)[:60], _AIHUB_FALLBACK_MODEL_SECOND)
                try:
                    res2, _ = await _try_n04(reason + " (2nd fallback)", _AIHUB_FALLBACK_MODEL_SECOND)
                    logger.info("✅ %s: Second fallback (%s) succeeded", label, _AIHUB_FALLBACK_MODEL_SECOND)
                    return res2, True
                except Exception as fe2:
                    logger.error("❌ %s: Both fallbacks failed. Last: %s", label, fe2)
                    raise fe2
            logger.error("❌ %s: Fallback (%s) failed, no second fallback configured: %s",
                         label, _AIHUB_FALLBACK_MODEL, fe1)
            raise fe1

    # ── Primary call: AIHUB with short retry before declaring it unstable ───
    if _endpoint_is_degraded(primary_url):
        return await _try_fallback_chain(f"endpoint {primary_url} is in degraded state")

    _last_exc: Exception | None = None
    for _attempt in range(_AIHUB_RETRY_COUNT if _on_aihub else 1):
        try:
            res = await primary_llm.ainvoke(prompt)
            # Silent failure: AIHUB sometimes returns HTTP 200 with empty body
            if _on_aihub and (not res or not getattr(res, "content", None) or not res.content.strip()):
                _last_exc = RuntimeError("Empty response")
                if _attempt < _AIHUB_RETRY_COUNT - 1:
                    logger.debug("⏳ %s: Empty response, retry %d/%d in %.0fs",
                                 label, _attempt + 1, _AIHUB_RETRY_COUNT, _AIHUB_RETRY_DELAY)
                    await asyncio.sleep(_AIHUB_RETRY_DELAY)
                    continue
                # Exhausted retries → fallback
                return await _try_fallback_chain("AIHUB returned empty response after retries")
            return res, False
        except Exception as e:
            _last_exc = e
            if _is_aihub_error(e) or (_on_aihub and "empty" in str(e).lower()):
                if _attempt < _AIHUB_RETRY_COUNT - 1:
                    logger.debug("⏳ %s: AIHUB error, retry %d/%d in %.0fs: %s",
                                 label, _attempt + 1, _AIHUB_RETRY_COUNT, _AIHUB_RETRY_DELAY, str(e)[:60])
                    await asyncio.sleep(_AIHUB_RETRY_DELAY)
                    continue
                # Exhausted retries → fallback
                return await _try_fallback_chain(f"AIHUB error after {_AIHUB_RETRY_COUNT} retries: {str(e)[:60]}")
            raise  # non-AIHUB error — propagate immediately

    # Should not reach here but handle defensively
    if _on_aihub and _last_exc:
        return await _try_fallback_chain(f"AIHUB exhausted: {str(_last_exc)[:60]}")
    raise _last_exc


async def _invoke_judge_with_retry(
    state: "AgentState", prompt: str, max_retries: int = 3, temperature: float | None = None
):
    """Invoke the judge LLM with retry logic for empty/failed responses.
    On failure: waits 5s (model reload time), re-discovers the node, retries.
    When AIHUB returns 401/429, immediately falls back to qwen3.6:35b@N04-RTX
    without burning retry budget on unavailable endpoints.

    temperature: when set, overrides the default judge sampling temperature.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            llm = await _get_judge_llm(state)
            if temperature is not None:
                llm = llm.bind(temperature=temperature)
            # Determine primary URL for degradation tracking
            _j_url = (state.get("judge_url_override") or JUDGE_URL or "").rstrip("/")
            res, used_fb = await _invoke_llm_with_aihub_fallback(
                llm, _j_url, prompt, timeout=JUDGE_TIMEOUT, label="Judge"
            )
            # Check for empty/useless response
            if res and hasattr(res, 'content') and res.content and len(res.content.strip()) > 0:
                if attempt > 0:
                    logger.info(f"✅ Judge retry {attempt+1}/{max_retries} succeeded")
                return res
            logger.warning(f"⚠️ Judge returned empty/short response (attempt {attempt+1}/{max_retries})")
            last_error = "Empty response"
        except Exception as e:
            logger.warning(f"⚠️ Judge invoke failed (attempt {attempt+1}/{max_retries}): {e}")
            last_error = str(e)

        if attempt < max_retries - 1:
            wait = 5 * (attempt + 1)  # 5s, 10s, 15s
            logger.info(f"🔄 Judge retry in {wait}s (warming up model)...")
            await asyncio.sleep(wait)
            # Clear PS cache to force fresh node discovery
            with _cache_lock:
                _ps_cache.clear()

    # All retries failed — return a minimal response
    logger.error(f"❌ Judge failed after {max_retries} attempts: {last_error}")
    from types import SimpleNamespace
    return SimpleNamespace(content=f"[Judge unavailable after {max_retries} retries: {last_error}]")


async def _get_judge_llm(state: "AgentState") -> "ChatOpenAI":
    """Returns per-template judge LLM, or global judge_llm as fallback.
    Supports floating mode: if model is set but URL is empty, discovers the best node.
    When the configured endpoint is in degraded state, returns N04-RTX fallback directly."""
    m = (state.get("judge_model_override") or "").strip()
    u = (state.get("judge_url_override")   or "").strip()
    t = (state.get("judge_token_override") or "ollama").strip()
    if m and u:
        if _endpoint_is_degraded(u.rstrip("/")) and _FALLBACK_ENABLED:
            logger.info("⚡ Judge endpoint degraded — returning fallback LLM directly")
            return await _get_n04_fallback_llm(JUDGE_TIMEOUT)
        return ChatOpenAI(model=m, base_url=u, api_key=t, timeout=JUDGE_TIMEOUT)
    if m and not u:
        # Floating judge: discover the best node for this model
        all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
        node = await _select_node(m, all_eps, user_id=state.get("user_id", ""))
        _url = node.get("url") or URL_MAP.get(node["name"], "")
        _tok = node.get("token", "ollama")
        logger.info(f"🌐 Floating judge: {m} → {node['name']}")
        return ChatOpenAI(model=m, base_url=_url, api_key=_tok, timeout=JUDGE_TIMEOUT)
    return judge_llm


async def _get_planner_llm(state: "AgentState") -> "ChatOpenAI":
    """Returns per-template planner LLM, or global planner_llm as fallback.
    Supports floating mode: if model is set but URL is empty, discovers the best node.
    When the configured endpoint is in degraded state, returns N04-RTX fallback directly."""
    m = (state.get("planner_model_override") or "").strip()
    u = (state.get("planner_url_override")   or "").strip()
    t = (state.get("planner_token_override") or "ollama").strip()
    if m and u:
        if _endpoint_is_degraded(u.rstrip("/")) and _FALLBACK_ENABLED:
            logger.info("⚡ Planner endpoint degraded — returning fallback LLM directly")
            return await _get_n04_fallback_llm(PLANNER_TIMEOUT)
        return ChatOpenAI(model=m, base_url=u, api_key=t, timeout=PLANNER_TIMEOUT)
    if m and not u:
        # Floating planner: discover the best node for this model
        all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
        node = await _select_node(m, all_eps, user_id=state.get("user_id", ""))
        _url = node.get("url") or URL_MAP.get(node["name"], "")
        _tok = node.get("token", "ollama")
        logger.info(f"🌐 Floating planner: {m} → {node['name']}")
        return ChatOpenAI(model=m, base_url=_url, api_key=_tok, timeout=PLANNER_TIMEOUT)
    return planner_llm


# _improvement_ratio — see parsing.py


async def _refine_expert_response(cat: str, gap_feedback: str, state: "AgentState") -> Optional[str]:
    """Re-calls the score-best expert for `cat`, enriched with judge gap feedback."""
    experts_for_cat = EXPERTS.get(cat, [])
    if not experts_for_cat:
        return None
    scored = [(await _get_expert_score(e["model"], cat), e) for e in experts_for_cat]
    scored.sort(key=lambda x: -x[0])
    best_expert = scored[0][1]
    _refine_ep = best_expert.get("endpoints") or [best_expert.get("endpoint", "")]
    if not _refine_ep or _refine_ep == [""]:
        _refine_ep = [s["name"] for s in INFERENCE_SERVERS_LIST]
    node = await _select_node(best_expert["model"], _refine_ep)
    url      = node.get("url") or URL_MAP.get(node["name"])
    token    = node.get("token", "ollama")
    _timeout = float(node.get("timeout", EXPERT_TIMEOUT))
    sys_prompt = _get_expert_prompt(cat, state.get("user_experts"))
    task_text  = state["input"]
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user",   "content": (
            f"{task_text}\n\n"
            f"--- FEEDBACK DES JUDGES (Bitte gezielt verbessern) ---\n{gap_feedback}"
        )},
    ]
    llm = ChatOpenAI(model=best_expert["model"], base_url=url, api_key=token, timeout=_timeout)
    try:
        res = await llm.ainvoke(messages)
        return res.content[:MAX_EXPERT_OUTPUT_CHARS] if res.content else None
    except Exception as e:
        logger.warning(f"⚠️ Refinement Expert [{cat}]: {e}")
        return None


# PS-Cache: {server_name: (timestamp, running_models_list)}
_ps_cache: Dict[str, tuple] = {}
_PS_CACHE_TTL = 5.0  # seconds — Ollama state does not change faster


def _estimate_model_vram_gb(model_name: str) -> float:
    """Estimate VRAM requirement in GB from model name.

    Parses parameter count from name (e.g. 'llama3.3:70b' → 70) and estimates
    VRAM based on common quantization: Q4 ≈ 0.55 * params + 1.5 GB overhead.
    Returns 0 if parameter count cannot be parsed (disables VRAM filtering).
    """
    import re as _re
    # Extract parameter count: "phi4:14b", "llama3.3:70b", "gemma4:31b"
    m = _re.search(r"[:\-](\d+(?:\.\d+)?)b", model_name.lower())
    if not m:
        # Try GGUF names: "Q4_0" → no param info, return 0
        return 0.0
    params_b = float(m.group(1))
    # Q4_K_M estimate: ~0.55 bytes/param + 1.5 GB context/overhead
    return params_b * 0.55 + 1.5


async def _select_node(model_name: str, allowed_endpoints: List[str],
                       user_id: str = "", priority: str = "normal") -> dict:
    """Selects the optimal node for model_name from the allowed endpoints.

    Strategy (4 phases):
    0. VRAM filter: exclude nodes where vram_gb < estimated model requirement
    1. Sticky session: if user recently used this model on a node, prefer it
    2. Check Ollama /api/ps (with 5s cache) for warm/cold models
    3. Within warm/cold: lowest load score (running/gpu_count) wins
    Priority: 'high' = pinned templates, 'normal' = standard, 'low' = floating/batch
    OpenAI nodes: always cold, neutral load.
    """
    # Phase 0: Sticky session check (warm model affinity for same user)
    if redis_client and user_id:
        try:
            sticky_key = f"moe:sticky:{user_id}:{model_name.split(':')[0]}"
            sticky_node = await redis_client.get(sticky_key)
            if sticky_node:
                sticky_name = sticky_node if isinstance(sticky_node, str) else sticky_node.decode()
                if sticky_name in allowed_endpoints:
                    srv = _server_info(sticky_name)
                    if srv:
                        # VRAM guard: verify sticky node can actually fit the model
                        est = _estimate_model_vram_gb(model_name)
                        node_vram = float(srv.get("vram_gb", 0))
                        if est > 0 and node_vram > 0 and node_vram < est:
                            await redis_client.delete(sticky_key)
                            logger.warning(f"🔒 Sticky override: {sticky_name} has {node_vram}GB but {model_name} needs ~{est:.1f}GB — re-routing")
                        else:
                            logger.debug(f"📌 Sticky session: {sticky_name} for {model_name}")
                            return srv
        except Exception:
            pass
    # Dynamic server exclusions stored in Redis (survive container restarts without rebuild)
    _blocked: set = set()
    _float_disabled: set = set()
    if redis_client is not None:
        try:
            _blocked       = {(v if isinstance(v, str) else v.decode()) for v in await redis_client.smembers("moe:blocked_servers")}
            _float_disabled = {(v if isinstance(v, str) else v.decode()) for v in await redis_client.smembers("moe:floating_disabled_servers")}
        except Exception:
            pass
    # Hard block: remove from every pool regardless of pinning
    _effective = [ep for ep in allowed_endpoints if ep not in _blocked]
    # Floating-disable: only applies when multiple endpoints are in the pool
    # (single-endpoint = explicit @node pin — always honoured)
    if len(_effective) > 1:
        _effective = [ep for ep in _effective if ep not in _float_disabled]
    candidates = [s for s in INFERENCE_SERVERS_LIST if s["name"] in _effective]
    if not candidates:
        # Fall back to the first non-blocked endpoint (preserves liveness)
        fallback_name = _effective[0] if _effective else (allowed_endpoints[0] if allowed_endpoints else "")
        return _server_info(fallback_name) or {"name": fallback_name, "url": URL_MAP.get(fallback_name, ""), "token": "ollama", "api_type": "ollama"}

    # Phase 0: VRAM filter — exclude nodes that cannot fit this model
    est_vram = _estimate_model_vram_gb(model_name)
    if est_vram > 0:
        vram_ok = [s for s in candidates if float(s.get("vram_gb", 0)) >= est_vram]
        if vram_ok:
            if len(vram_ok) < len(candidates):
                excluded = [s["name"] for s in candidates if s not in vram_ok]
                logger.info(f"🔒 VRAM filter: {model_name} needs ~{est_vram:.1f}GB — excluded {excluded}")
            candidates = vram_ok
        else:
            # Hard filter: only keep nodes WITHOUT a vram_gb limit (cloud/external)
            no_limit = [s for s in candidates if not s.get("vram_gb")]
            if no_limit:
                logger.warning(f"⚠️ No local node has enough VRAM for {model_name} (~{est_vram:.1f}GB) — using cloud/external nodes only")
                candidates = no_limit
            else:
                logger.error(f"🚫 VRAM hard block: {model_name} (~{est_vram:.1f}GB) exceeds ALL nodes — routing to largest available")
                candidates = sorted(candidates, key=lambda s: float(s.get("vram_gb", 0)), reverse=True)[:1]

    if len(candidates) == 1:
        return candidates[0]

    async def _get_ps(srv: dict) -> tuple:
        """Returns (srv, running_models_list, model_is_warm). Uses 5s cache."""
        if srv.get("api_type", "ollama") != "ollama":
            return srv, [], False
        now = time.monotonic()
        with _cache_lock:
            cached = _ps_cache.get(srv["name"])
        if cached and (now - cached[0]) < _PS_CACHE_TTL:
            running = cached[1]
        else:
            base = srv["url"].rstrip("/").removesuffix("/v1")
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    r = await client.get(f"{base}/api/ps")
                    running = r.json().get("models", []) if r.status_code == 200 else []
            except Exception:
                running = []
            with _cache_lock:
                _ps_cache[srv["name"]] = (now, running)
        is_warm = any(
            m.get("name", "").split(":")[0] == model_name.split(":")[0]
            and (not model_name.count(":") or m.get("name") == model_name)
            for m in running
        )
        return srv, running, is_warm

    def load_score(srv: dict, running: list) -> float:
        """Lower score = better candidate. Factors in GPU count AND cost_factor.
        cost_factor acts as a speed/priority weight: higher = faster/preferred.
        RTX (1.0) is preferred over Tesla M10 (0.8) at equal load."""
        raw_load = len(running) / max(int(srv.get("gpu_count", 1)), 1)
        speed = float(srv.get("cost_factor", 1.0))  # higher = faster GPU
        return raw_load / max(speed, 0.1)  # divide by speed: fast nodes get lower scores

    # Select best candidate: warm preferred, then idle, then lowest load
    ps_results = await asyncio.gather(*[_get_ps(s) for s in candidates])
    warm = [(srv, running) for srv, running, is_warm in ps_results if is_warm]
    cold = [(srv, running) for srv, running, is_warm in ps_results if not is_warm]

    # Priority order: 1) warm + idle, 2) warm + busy, 3) cold + idle, 4) cold + busy
    warm_idle = [(s, r) for s, r in warm if load_score(s, r) < 0.5]
    cold_idle = [(s, r) for s, r in cold if load_score(s, r) < 0.5]

    pool = warm_idle or warm or cold_idle or cold
    best = min(pool, key=lambda x: load_score(x[0], x[1]))

    status = "🔥 warm" if warm else "❄️ cold"
    busy = "idle" if load_score(best[0], best[1]) < 0.5 else "busy"
    logger.debug(f"{status}/{busy} Node-Select: {best[0]['name']} for {model_name}")

    # Set sticky session for future requests from this user
    if redis_client and user_id:
        try:
            sticky_key = f"moe:sticky:{user_id}:{model_name.split(':')[0]}"
            asyncio.create_task(
                redis_client.setex(sticky_key, 300, best[0]["name"])  # 5 min TTL
            )
        except Exception:
            pass

    return best[0]


THOMPSON_SAMPLING_ENABLED = os.getenv("THOMPSON_SAMPLING_ENABLED", "true").lower() in ("1", "true", "yes")

PROM_THOMPSON = Histogram("moe_thompson_sample", "Thompson-sampled expert score",
                          buckets=[.1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0])


async def _get_expert_score(model: str, category: str) -> float:
    """Performance score 0-1 for a model in a category.

    When ``THOMPSON_SAMPLING_ENABLED`` is true, draws from Beta(α, β) instead
    of the deterministic Laplace point estimate.  This provides natural
    exploration: experts with fewer observations have wider variance and
    occasionally score higher than their point estimate, giving them a chance
    to prove themselves.
    """
    if redis_client is None:
        return 0.5
    try:
        key = _perf_key(model, category)
        data = await redis_client.hgetall(key)
        total = int(data.get("total", 0))
        if total < EXPERT_MIN_DATAPOINTS:
            return 0.5
        positive = int(data.get("positive", 0))
        if THOMPSON_SAMPLING_ENABLED:
            import random
            alpha = positive + 1
            beta = (total - positive) + 1
            score = random.betavariate(alpha, beta)
            PROM_THOMPSON.observe(score)
            return score
        return (positive + 1) / (total + 2)  # Laplace fallback
    except Exception:
        return 0.5

async def _record_expert_outcome(model: str, category: str, positive: bool) -> None:
    """Increments total and optionally positive counter for a model/category pair."""
    if redis_client is None:
        return
    try:
        key = _perf_key(model, category)
        pipe = redis_client.pipeline()
        pipe.hincrby(key, "total", 1)
        if positive:
            pipe.hincrby(key, "positive", 1)
        else:
            pipe.hincrby(key, "negative", 1)
        await pipe.execute()
    except Exception as e:
        logger.warning(f"Expert score update failed: {e}")

# _extract_usage, _extract_json, _parse_expert_confidence,
# _parse_expert_gaps, _expert_category — see parsing.py

def _infer_tier(model_name: str) -> int:
    """Derives expert tier from model size in the name. T1 ≤20B, T2 >20B.
    Kept for backward compatibility with raw EXPERTS env var entries that lack
    an explicit '_tier' field. New code uses explicit role→_tier mapping in templates."""
    m = re.search(r':(\d+(?:\.\d+)?)b', model_name, re.I)
    if not m:
        return 1
    return 1 if float(m.group(1)) <= EXPERT_TIER_BOUNDARY_B else 2

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

# _DOMAIN_SCORES, _domain_score, _reliability_label — see web_search.py

async def _web_search_with_citations(query: str) -> str:
    """Wrapper: forwards to web_search._web_search_with_citations_impl with app-level search singleton."""
    return await _web_search_with_citations_impl(query, search)

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

async def _kafka_publish(topic: str, payload: dict) -> None:
    """Sends a JSON message to a Kafka topic. Fails silently if Kafka is not available."""
    if kafka_producer is None:
        return
    try:
        data = json.dumps(payload).encode()
        await kafka_producer.send_and_wait(topic, data)
    except Exception as e:
        logger.warning(f"Kafka publish [{topic}] failed: {e}")


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
    if not GRAPH_VIA_MCP and graph_manager is None:
        return {"graph_context": ""}
    plan = state.get("plan", [])
    categories = [t.get("category", "") for t in plan if isinstance(t, dict)]

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
    valid_cats = set(EXPERTS.keys()) | NON_EXPERT_CATEGORIES | {"agentic_coder"}
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
    # Skip cache entirely during agentic re-planning — each iteration needs a fresh plan.
    import hashlib as _hashlib
    _plan_cache_key = f"moe:plan:{_hashlib.sha256(state['input'][:300].encode()).hexdigest()[:16]}"
    if redis_client is not None and not _is_agentic_replan:
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
    _routing    = complexity_routing_hint(_complexity)
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
    _query_temp = _detect_query_temperature(state["input"])
    logger.info(f"🌡️ Adaptive temperature: {_query_temp} (input analysis)")
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
{MCP_TOOLS_DESCRIPTION}
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
        res, _planner_fb = await _invoke_llm_with_aihub_fallback(
            _planner_llm_inst, _planner_url, prompt,
            timeout=PLANNER_TIMEOUT, label="Planner",
        )
        if _planner_fb:
            await _report("⚠️ Planner: used N04-RTX fallback (AIHUB degraded)")
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
            # Embed conversation context before the current task
            messages: List[Dict] = [{"role": "system", "content": sys_prompt}]
            if chat_history:
                messages.extend(chat_history)

            # Attach images as multimodal content when present (OpenAI image_url format).
            # Applies to all categories — even if the planner chooses "general" instead of "vision"
            # a multimodal-capable model (e.g. gemma4) can still process the image.
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

            logger.info(f"🚀 Expert {t_idx}.{e_idx} GPU#{gpu} [{model_name} / {cat}]")
            await _report(
                f"🚀 Expert [{model_name} / {cat}] GPU#{gpu}\n"
                f"  Task: {task_text}"
            )
            await _report(
                f"📤 Expert [{model_name} / {cat}] System-Prompt:\n{sys_prompt}"
            )
            expert_base_url = url.rstrip("/").removesuffix("/v1")
            _expert_node_timeout = float(
                selected.get("timeout", EXPERT_TIMEOUT) if not LITELLM_URL else EXPERT_TIMEOUT
            )
            llm = ChatOpenAI(model=model_name, base_url=url, api_key=token, timeout=_expert_node_timeout)
            try:
                _primary_url = url.rstrip("/")
                res, _used_fallback = await _invoke_llm_with_aihub_fallback(
                    llm, _primary_url, messages,
                    timeout=_expert_node_timeout,
                    label=f"Expert[{cat}]",
                )
                if _used_fallback:
                    await _report(f"⚠️ Expert [{cat}]: used N04-RTX fallback (AIHUB degraded)")
                usage = _extract_usage(res)
                content = res.content[:MAX_EXPERT_OUTPUT_CHARS]
                if len(res.content) > MAX_EXPERT_OUTPUT_CHARS:
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
                return {"res": f"[{res_prefix} / {cat}]: {content}", "model_cat": f"{model_name}::{cat}", **usage}
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

    groups = await asyncio.gather(*[run_task(i, task) for i, task in expert_tasks])
    all_results: List[dict] = [r for group in groups for r in group]

    used = [r["model_cat"] for r in all_results if r.get("model_cat")]
    return {
        "expert_results":     [r["res"] for r in all_results if "res" in r],
        "expert_models_used": used,
        "prompt_tokens":      sum(r.get("prompt_tokens",     0) for r in all_results),
        "completion_tokens":  sum(r.get("completion_tokens", 0) for r in all_results),
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

    _SEARCH_CACHE_TTL = 86400  # 24 h — results reproducible within a day

    async def _fetch_one(task: dict, idx: int, total: int) -> tuple[str, str, str]:
        """Execute a single search with Redis caching for reproducibility.

        Returns (result_text, query, quality).
        Cache key: sha256(query) — collisions are astronomically unlikely for search strings.
        """
        query = task.get("search_query", state["input"])
        # Redis cache lookup (never blocks if Redis is down)
        _cache_key = f"moe:search_cache:{hashlib.sha256(query.encode()).hexdigest()[:32]}"
        if redis_client is not None:
            try:
                cached = await redis_client.get(_cache_key)
                if cached:
                    logger.debug("🗄️ Search cache hit: %s", query[:60])
                    return cached.decode("utf-8", errors="replace"), query, "ok-cached"
            except Exception:
                pass

        await _report(f"🌐 Web search [{idx+1}/{total}]: '{query[:60]}'...")
        raw = await _web_search_with_citations(query)
        quality = "ok" if raw and len(raw) > 200 else "empty"

        if raw:
            await _report(f"🌐 Search [{idx+1}] result ({len(raw)} chars)")
            # Store in Redis cache
            if redis_client is not None:
                try:
                    await redis_client.set(_cache_key, raw.encode("utf-8"), ex=_SEARCH_CACHE_TTL)
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

# Env-configurable: compression is triggered when raw context exceeds budget × this factor.
_GRAPH_COMPRESS_THRESHOLD_FACTOR = float(os.getenv("GRAPH_COMPRESS_THRESHOLD_FACTOR", "2.0"))
_GRAPH_COMPRESS_LLM_MODEL        = os.getenv("GRAPH_COMPRESS_LLM", "")  # empty = skip LLM compress
_GRAPH_COMPRESS_LLM_TIMEOUT      = float(os.getenv("GRAPH_COMPRESS_LLM_TIMEOUT", "3.0"))

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
        _effective_limit = graphrag_budget_chars(
            model=_judge_model,
            query_chars=len(state.get("input", "")),
            override_chars=_tpl_limit,
        )
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
        # Truncate overly long expert outputs before merging to reduce
        # merger token consumption.  The first ~2000 chars typically contain
        # the core finding; the rest is elaboration that inflates the prompt
        # without proportional quality gain.  (Measured: merger was 45% of
        # total token budget before this cap.)
        MAX_EXPERT_CHARS = 2000
        trimmed = []
        for er in expert_results:
            if len(er) > MAX_EXPERT_CHARS:
                trimmed.append(er[:MAX_EXPERT_CHARS] + "\n[...truncated for merger efficiency]")
            else:
                trimmed.append(er)
        sections.append("EXPERT RESPONSES:\n" + "\n\n".join(trimmed))
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
        # Structured web-research compression: keep only the top 3 search
        # result blocks and cap each at 600 chars.  This replaces the old
        # blunt character truncate (which cut mid-sentence in the 3rd
        # result) with a relevance-preserving filter.
        web_blocks = [b.strip() for b in re.split(r'\n\[(?:Research|Recherche|\d+)', web) if b.strip()]
        MAX_WEB_BLOCKS = 5 if mode in ("research", "plan") else 3
        MAX_BLOCK_CHARS = 800 if mode in ("research", "plan") else 600
        compressed_web = "\n\n".join(
            block[:MAX_BLOCK_CHARS] + ("…" if len(block) > MAX_BLOCK_CHARS else "")
            for block in web_blocks[:MAX_WEB_BLOCKS]
        )
        if not compressed_web:
            compressed_web = web[:2000]  # fallback if split produced nothing
        sections.append(f"WEB RESEARCH (current, with sources):\n{compressed_web}")
    if cached:
        sections.append(f"PRIOR KNOWLEDGE (Cache):\n{cached[:1000]}")
    soft_examples = state.get("soft_cache_examples") or ""
    if soft_examples:
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

    # Inject output skill formatting instructions if planner suggested one
    _skill_body = state.get("output_skill_body", "")
    if _skill_body:
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
        if len(fast_resp) > CACHE_MIN_RESPONSE_LEN:
            # Deterministic ID (SHA-256 of content) prevents duplicate entries under
            # concurrent writes — upsert is idempotent if same response races twice.
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

    if len(res_content_clean) > CACHE_MIN_RESPONSE_LEN:
        # Deterministic ID (SHA-256 of content) prevents duplicate entries under
        # concurrent writes — upsert is idempotent if same response races twice.
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
            if _used_tokens < 80_000:
                _gap_prompt = (
                    "You are a completion assessor. Based on the original question and the current answer, "
                    "determine if the answer is complete and what specific data is still missing.\n\n"
                    f"ORIGINAL QUESTION:\n{state['input'][:600]}\n\n"
                    f"CURRENT ANSWER:\n{res_content_clean[:800]}\n\n"
                    "Reply ONLY in this exact format (no extra text):\n"
                    "COMPLETION_STATUS: COMPLETE | NEEDS_MORE_INFO\n"
                    "GAP: <specific fact/calculation/document still missing, or 'none'>\n"
                    "SEARCH_STRATEGY: <concrete next search approach — e.g. 'search site:github.com numpy polynomial issues', "
                    "'use youtube_transcript for video', 'use pubchem_compound_search', 'search exact paper title in quotes'>"
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

        # Record this iteration's findings for planner context in next round
        _agentic_history.append({
            "iteration": _agentic_iter,
            "findings": res_content_clean[:1200],
            "gap": _agentic_gap,
        })

        # Working Memory: LLM-based fact extraction when gap still open and budget allows
        _wm_merged: dict = dict(state.get("working_memory") or {})
        if _agentic_gap != "COMPLETE" and state.get("prompt_tokens", 0) < 90_000:
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

        _agentic_extra = {
            "agentic_gap": _agentic_gap,
            "agentic_history": _agentic_history,
            "working_memory": _wm_merged,
            "search_strategy_hint": _strategy_hint,
        }

    return {
        "final_response": res_content_clean,
        "provenance_sources": _provenance_sources,
        **merger_usage,
        **_agentic_extra,
    }


async def research_fallback_node(state: AgentState):
    """
    Runs after all parallel nodes — analyzes expert outputs for knowledge gaps.
    For each CONFIDENCE:low result a targeted web search is started.
    Results are aggregated in web_research before the merger synthesizes.
    """
    if state.get("cache_hit"):
        return {"web_research": state.get("web_research", "")}

    expert_results = state.get("expert_results") or []
    plan           = state.get("plan") or []
    existing_web   = state.get("web_research") or ""

    # Expert tasks from the plan (LLM-handled only, no precision_tools/research)
    expert_tasks = [
        t for t in plan
        if isinstance(t, dict) and t.get("category") not in NON_EXPERT_CATEGORIES
    ]

    # Pair low-confidence results with matching plan tasks
    searches: List[Dict] = []
    seen: set = set()
    for result in expert_results:
        if _parse_expert_confidence(result) != "low":
            continue
        cat = _expert_category(result)
        matching = [t for t in expert_tasks if t.get("category") == cat]
        for task in matching:
            key = task.get("task", "")[:100]
            if key in seen:
                continue
            seen.add(key)
            searches.append({"query": task.get("task", state["input"]), "category": cat})

    if not searches:
        return {"web_research": existing_web}

    logger.info(f"--- [NODE] RESEARCH FALLBACK ({len(searches)} knowledge gaps) ---")
    await _report(f"🔍 {len(searches)} knowledge gap(s) detected — starting research agent...")

    async def _search_one(item: dict) -> str:
        query = item["query"][:180]
        cat   = item["category"]
        await _report(f"🌐 Agent researching [{cat}]: '{query[:60]}'...")
        raw = await _web_search_with_citations(query)
        if raw:
            await _report(f"🌐 [{cat}]: {len(raw)} chars research result")
            logger.info(f"🌐 Research Fallback [{cat}]: {len(raw)} chars")
            return f"[Research / {cat}]:\n{raw[:2000]}"
        await _report(f"⚠️ Research [{cat}] failed")
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
    is_complex   = len(plan) > 1

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
    # Increment iteration counter via state mutation (read by planner_node)
    state["agentic_iteration"] = _iter + 1  # type: ignore[index]
    logger.info(f"🔄 Agentic router: iteration {_iter + 1}/{_max}, gap='{_gap[:60]}'")
    return "planner"


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
builder.add_node("merger",             merger_node)
builder.add_node("critic",             critic_node)

builder.set_entry_point("cache")
builder.add_conditional_edges("cache", _route_cache, {"semantic_router": "semantic_router", "merger": "merger"})
builder.add_edge("semantic_router", "planner")
builder.add_edge("planner", "workers")
builder.add_edge("planner", "research")
builder.add_edge("planner", "math")
builder.add_edge("planner", "mcp")
builder.add_edge("planner", "graph_rag")
builder.add_edge(["workers", "research", "math", "mcp", "graph_rag"], "research_fallback")
builder.add_edge("research_fallback", "thinking")
builder.add_edge("thinking", "merger")
builder.add_conditional_edges(
    "merger",
    _should_replan,
    {"planner": "planner", "critic": "critic"},
)
builder.add_edge("critic", END)

# --- SERVER ---
app_graph = None

async def _init_graph_rag() -> None:
    """Initialize the Neo4j GraphRAG Manager with retry logic."""
    global graph_manager
    for attempt in range(6):
        try:
            mgr = GraphRAGManager(NEO4J_URI, NEO4J_USER, NEO4J_PASS)
            await mgr.setup()
            graph_manager = mgr
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
    global MCP_TOOLS_DESCRIPTION, AGENTIC_CODE_TOOLS_DESCRIPTION, MCP_TOOL_SCHEMAS
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
            logger.info(f"✅ Kafka Producer connected ({KAFKA_BOOTSTRAP})")
            return
        except Exception as e:
            wait = 5 * (attempt + 1)
            logger.warning(f"⚠️ Kafka unreachable (attempt {attempt+1}/12): {e} — retry in {wait}s")
            await asyncio.sleep(wait)
    logger.error("❌ Kafka unreachable after 12 attempts — Kafka disabled")


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
                                _r = await _hc.get(f"{_surl}/models")
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
    global app_graph, redis_client, _userdb_pool
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
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
    )
    # Kafka Consumer as persistent background task
    consumer_task  = asyncio.create_task(_kafka_consumer_loop())
    gauge_task     = asyncio.create_task(_gauge_updater_loop())
    asyncio.create_task(_auto_resume_dedicated_healer())
    asyncio.create_task(_watchdog_dedicated_healer())
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

# CORS for Open WebUI direct connections (browser-side)
from fastapi.middleware.cors import CORSMiddleware
_cors_all     = os.getenv("CORS_ALL_ORIGINS", "0") == "1"
_cors_raw     = os.getenv("CORS_ORIGINS", "")
_cors_origins = ["*"] if _cors_all else [o.strip() for o in _cors_raw.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=not _cors_all,  # credentials + wildcard is forbidden per CORS spec
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "x-api-key", "Content-Type"],
    )

@app.get("/health")
async def health_check():
    """Liveness probe for Docker HEALTHCHECK and load balancers."""
    return {"status": "ok"}


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint — returns all moe_* metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/provider-status")
async def provider_status():
    """Rate-Limit-Status aller gecachten Provider-Endpunkte (Claude Code Integration)."""
    import time as _time
    return {ep: {**data, "now": _time.time()} for ep, data in _provider_rate_limits.items()}


@app.get("/v1/models")
async def list_models(raw_request: Request):
    raw_key = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "missing_key"}
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key", "type": "invalid_request_error", "code": "invalid_api_key"
        }})
    user_perms        = json.loads(user_ctx.get("permissions_json", "{}"))
    allowed_modes     = user_perms.get("moe_mode")        # None = all allowed (backward-compat)
    allowed_templates = user_perms.get("expert_template") # list of template IDs or None
    _user_cc_json_m   = user_ctx.get("user_cc_profiles_json", "")
    has_cc            = bool(user_perms.get("cc_profile")) or bool(_user_cc_json_m and _user_cc_json_m not in ("{}", ""))  # CC aliases when permission granted or user has own profiles

    if allowed_templates:
        # User has templates assigned → only show templates (no generic modes)
        all_templates = _read_expert_templates()
        main_models = [
            {
                "id":          t.get("name", t["id"]),   # Template name as model ID
                "object":      "model",
                "description": t.get("description") or t.get("name", t["id"]),
            }
            for t in all_templates if t.get("id") in allowed_templates
        ]
    else:
        # No templates → show generic modes (filtered by moe_mode permission)
        # If the user has other explicit permissions (model_endpoint, cc_profile) but no moe_mode,
        # do not show MoE modes (only legacy users without any permissions see all modes)
        _has_other_perms = bool(user_perms.get("model_endpoint") or user_perms.get("cc_profile"))
        if allowed_modes is not None or not _has_other_perms:
            main_models = [
                {"id": cfg["model_id"], "object": "model", "description": cfg["description"]}
                for cfg in MODES.values()
                if allowed_modes is None or cfg["model_id"] in allowed_modes
            ]
        else:
            main_models = []

    existing_ids = {m["id"] for m in main_models}
    # Claude Code compatible model IDs — only for users with cc_profile permission
    claude_models = [
        {"id": mid, "object": "model", "description": f"Claude Code compatible → MoE ({CLAUDE_CODE_TOOL_MODEL} for tools)"}
        for mid in sorted(CLAUDE_CODE_MODELS) if mid not in existing_ids
    ] if has_cc else []
    # Native LLMs — direct inference endpoints from model_endpoint permission (model@node)
    # Model ID as "model@node" for unique host assignment in OpenWebUI
    native_models = []
    _api_type_map = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}
    allowed_endpoints = user_perms.get("model_endpoint")
    if allowed_endpoints:
        seen: set = set()
        for entry in allowed_endpoints:
            model_n, _, node = entry.partition("@")
            if not model_n:
                continue
            if model_n == "*":
                # Node wildcard: fetch all currently available models from the node live
                if node not in URL_MAP:
                    continue
                try:
                    _api_type = _api_type_map.get(node, "ollama")
                    _wc_url = URL_MAP[node].rstrip("/")
                    _wc_token = TOKEN_MAP.get(node, "ollama")
                    async with httpx.AsyncClient(timeout=5) as _wc_client:
                        if _api_type == "ollama":
                            _wc_r = await _wc_client.get(
                                f"{_wc_url}/api/tags",
                                headers={"Authorization": f"Bearer {_wc_token}"},
                            )
                            _wc_models = [m["name"] for m in (_wc_r.json().get("models") or [])] if _wc_r.status_code == 200 else []
                        else:
                            _wc_r = await _wc_client.get(
                                f"{_wc_url}/v1/models",
                                headers={"Authorization": f"Bearer {_wc_token}"},
                            )
                            _wc_models = [m["id"] for m in (_wc_r.json().get("data") or [])] if _wc_r.status_code == 200 else []
                    for _wc_m in _wc_models:
                        mid = f"{_wc_m}@{node}"
                        if mid not in seen and mid not in existing_ids:
                            seen.add(mid)
                            native_models.append({"id": mid, "object": "model",
                                                  "description": f"Direkt via {node}"})
                except Exception:
                    pass
                continue
            model_id = f"{model_n}@{node}" if node else model_n
            if model_id not in seen and model_id not in existing_ids:
                seen.add(model_id)
                native_models.append({
                    "id":          model_id,
                    "object":      "model",
                    "description": f"Direkt via {node}" if node else "Direktzugriff",
                })
    return {"object": "list", "data": main_models + claude_models + native_models}

@app.get("/graph/stats")
async def graph_stats():
    if graph_manager is None:
        return {"status": "unavailable"}
    stats = await graph_manager.get_stats()
    return {"status": "ok", **stats}

@app.get("/graph/search")
async def graph_search(q: str, limit: int = 10):
    if graph_manager is None:
        return {"status": "unavailable", "results": []}
    results = await graph_manager.search_entities(q, limit)
    return {"status": "ok", "query": q, "results": results}

@app.get("/graph/knowledge/export")
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


@app.post("/graph/knowledge/import")
async def graph_knowledge_import(raw_request: Request):
    """Import a knowledge bundle into the graph."""
    if graph_manager is None:
        return JSONResponse(status_code=503, content={"error": "GraphRAG unavailable"})
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    bundle = body.get("bundle", body)
    dry_run = body.get("dry_run", False)
    source_tag = body.get("source_tag", "community_import")
    trust_floor = float(body.get("trust_floor", 0.5))
    if "@context" not in bundle and "entities" not in bundle:
        return JSONResponse(status_code=400, content={"error": "Not a valid knowledge bundle"})
    stats = await graph_manager.import_knowledge_bundle(
        bundle=bundle,
        source_tag=source_tag,
        trust_floor=trust_floor,
        dry_run=dry_run,
        kafka_publish_fn=_kafka_publish,
    )
    return {"status": "ok", "dry_run": dry_run, **stats}


@app.post("/graph/knowledge/import/validate")
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
    if request.max_tokens:  payload["max_tokens"]  = request.max_tokens
    if request.temperature: payload["temperature"] = request.temperature

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
                yield _chunk({"content": plan_text[i:i + 50]})
                await asyncio.sleep(0.01)

    _t_first_token: Optional[float] = None
    if "error" in result_box:
        yield _chunk({"content": f"Error: {result_box['error']}"})
    else:
        content = result_box.get("data", {}).get("final_response") or ""
        for i in range(0, len(content), SSE_CHUNK_SIZE):
            if _t_first_token is None:
                _t_first_token = time.monotonic()
            yield _chunk({"content": content[i:i + 50]})
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
            ))
            asyncio.create_task(_increment_user_budget(_uid, p_tok + c_tok, prompt_tokens=p_tok, completion_tokens=c_tok))
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

@app.post("/v1/feedback")
async def submit_feedback(req: FeedbackRequest):
    if not 1 <= req.rating <= 5:
        return {"status": "error", "message": "Rating must be between 1 and 5"}
    if redis_client is None:
        return {"status": "error", "message": "Valkey not available"}

    meta = await redis_client.hgetall(f"moe:response:{req.response_id}")
    if not meta:
        return {"status": "error", "message": "Response ID not found or expired"}

    positive = req.rating >= FEEDBACK_POSITIVE_THRESHOLD
    negative = req.rating <= FEEDBACK_NEGATIVE_THRESHOLD

    PROM_FEEDBACK.observe(req.rating)
    # Update expert performance counter
    for model_cat in json.loads(meta.get("expert_models_used", "[]")):
        if "::" in model_cat:
            model, cat = model_cat.split("::", 1)
            await _record_expert_outcome(model, cat, positive)

    # Flag ChromaDB entry on negative feedback
    chroma_doc_id = meta.get("chroma_doc_id", "")
    if negative and chroma_doc_id:
        try:
            await asyncio.to_thread(cache_collection.update, ids=[chroma_doc_id], metadatas=[{"flagged": True}])
            logger.info(f"🚫 Cache entry {chroma_doc_id} flagged")
        except Exception as e:
            logger.warning(f"ChromaDB update failed: {e}")

    # Flag corresponding Neo4j triples
    if graph_manager is not None:
        user_input = meta.get("input", "")
        if negative and user_input:
            n = await graph_manager.mark_triples_unverified(user_input)
            if n:
                logger.info(f"⚠️ {n} GraphRAG-Tripel als flagged markiert")
        elif positive and user_input:
            n = await graph_manager.verify_triples(user_input)
            if n:
                logger.info(f"✅ {n} GraphRAG-Tripel als verified markiert")

    asyncio.create_task(_telemetry.record_user_feedback(_userdb_pool, req.response_id, req.rating))
    asyncio.create_task(_kafka_publish(KAFKA_TOPIC_FEEDBACK, {
        "response_id": req.response_id,
        "rating":      req.rating,
        "positive":    positive,
        "ts":          datetime.now().isoformat(),
    }))
    logger.info(f"📬 Feedback {req.response_id}: rating={req.rating} positive={positive}")
    return {"status": "ok", "response_id": req.response_id, "rating": req.rating, "positive": positive}


class MemoryIngestRequest(BaseModel):
    """Request body for the /v1/memory/ingest endpoint."""
    session_summary: str
    key_decisions: List[str] = []
    domain: str = "session"
    source_model: str = "claude-code-hook"
    confidence: float = 0.8


@app.post("/v1/memory/ingest")
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
            yield _chunk({"content": content[i:i + 50]})
            await asyncio.sleep(0.005)
        yield _chunk({}, finish_reason="stop", u=usage)
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.post("/v1/chat/completions")
async def chat_completions(raw_request: Request, request: ChatCompletionRequest):
    chat_id    = f"chatcmpl-{uuid.uuid4()}"
    session_id = _extract_session_id(raw_request)

    # Auth
    raw_key  = _extract_api_key(raw_request)
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
    user_id      = user_ctx.get("user_id", "anon")
    api_key_id   = user_ctx.get("key_id", "")
    user_perms   = json.loads(user_ctx.get("permissions_json", "{}"))

    # Template names and MoE mode IDs take precedence over native endpoints.
    # Wildcard permissions (*@node) would otherwise intercept template names and
    # route them as direct Ollama calls (model does not exist → empty response).
    _req_raw = request.model
    _req_at = _req_raw.rindex("@") if "@" in _req_raw else -1
    _req_model_base = _req_raw[:_req_at] if _req_at >= 0 else _req_raw
    _req_node_hint  = _req_raw[_req_at + 1:] if _req_at >= 0 else None
    _all_tmpls = _read_expert_templates()
    _matched_tmpl = next((t for t in _all_tmpls if t.get("name") == request.model), None)
    _tmpl_override: Optional[str] = _matched_tmpl["id"] if _matched_tmpl else None
    mode = _MODEL_ID_TO_MODE.get(request.model, "default")

    # User-owned templates (stored in Valkey, not in admin DB) are invisible to the
    # admin template lookup above. When a user selects their own template, _tmpl_override
    # stays None and the native-endpoint block below incorrectly intercepts the request,
    # routing it as a direct LLM call (native mode) instead of through the MoE pipeline.
    # Fix: detect user templates early and set _tmpl_override so the pipeline is used.
    if not _tmpl_override:
        _early_user_tmpls: dict = {}
        try:
            _early_user_tmpls = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
        except Exception:
            pass
        if _early_user_tmpls:
            # Match by template name (display name) or by template ID.
            # Also try _req_model_base (the name without any "@node" suffix) because
            # Open WebUI appends "@connectionname" to model IDs when a user selects a
            # template — e.g. "nff-test@AIHUB_NFF" → base name is "nff-test".
            for _ut_id, _ut_cfg in _early_user_tmpls.items():
                _ut_name = _ut_cfg.get("name", "")
                if (
                    _ut_name == request.model          # exact match with full model string
                    or _ut_name == _req_model_base     # match without @node suffix
                    or _ut_id  == request.model        # match by template ID
                    or _ut_id  == _req_model_base
                ):
                    _tmpl_override = _ut_id
                    logger.debug("User template detected: '%s' (req='%s') → override=%s",
                                 _ut_name or _ut_id, request.model, _ut_id)
                    break
            # Also check templates referenced in the user's expert_template permissions
            if not _tmpl_override:
                _early_perms = json.loads(user_ctx.get("permissions_json", "{}") or "{}")
                for _perm_tid in _early_perms.get("expert_template", []):
                    if _perm_tid in _early_user_tmpls:
                        _ut_cfg = _early_user_tmpls[_perm_tid]
                        _ut_name = _ut_cfg.get("name", "")
                        if (
                            _ut_name == request.model
                            or _ut_name == _req_model_base
                            or _perm_tid == request.model
                            or _perm_tid == _req_model_base
                        ):
                            _tmpl_override = _perm_tid
                            logger.debug("User template detected via permissions: %s", _perm_tid)
                            break

    # Benchmark node reservation: reject non-benchmark requests when nodes are locked.
    # Fails open if Redis is unavailable so a Redis outage never blocks all traffic.
    if redis_client is not None:
        try:
            _bench_reserved = await redis_client.smembers("moe:benchmark_reserved")
            if _bench_reserved:
                _bench_meta = await redis_client.hgetall("moe:benchmark_lock_meta") or {}
                _bench_tmpl = _bench_meta.get("template", "")
                if request.model != _bench_tmpl:
                    # Allow templates whose experts run exclusively on non-reserved nodes.
                    # Collect the endpoint names used by this template's experts.
                    _tmpl_nodes: set = set()
                    if _matched_tmpl:
                        for _exp in (_matched_tmpl.get("experts") or {}).values():
                            for _em in (_exp.get("models") or []):
                                _ep = _em.get("endpoint", "")
                                if _ep:
                                    _tmpl_nodes.add(_ep)
                    # Block only if the template uses reserved nodes, or has no node info
                    # (unknown template — block by default for safety).
                    if not _tmpl_nodes or _tmpl_nodes.intersection(_bench_reserved):
                        return JSONResponse(status_code=503, content={"error": {
                            "message": (
                                f"Service temporarily unavailable: inference nodes are reserved "
                                f"for benchmark run (template: {_bench_tmpl}). "
                                "Please retry after the benchmark completes."
                            ),
                            "type": "service_unavailable",
                            "code": "benchmark_lock_active",
                            "benchmark_template": _bench_tmpl,
                        }})
        except Exception:
            pass  # Fail open — never block traffic due to Redis errors

    # If the matched template sets force_think=true and no explicit mode was requested,
    # upgrade to agent_orchestrated so the thinking_node activates before routing.
    # (Resolved after _tmpl_prompts is populated below; pre-read here to avoid circular dep.)
    if _tmpl_override:
        _early_tmpl = next((t for t in _all_tmpls if t.get("id") == _tmpl_override), None)
        if _early_tmpl and _early_tmpl.get("force_think") and mode == "default":
            mode = "agent_orchestrated"

    # Native LLM? check model_endpoint permission — only if no template and no MoE mode
    # Supports "model_name" (legacy) and "model_name@node" (new, OpenWebUI format)
    _native_endpoint: Optional[dict] = None
    _user_conns_map: dict = {}
    try:
        _user_conns_map = json.loads(user_ctx.get("user_connections_json", "{}") or "{}")
    except Exception:
        pass
    if not _tmpl_override and request.model not in _MODEL_ID_TO_MODE:
        for _ep_entry in user_perms.get("model_endpoint", []):
            _ep_model, _, _ep_node = _ep_entry.partition("@")
            if _ep_node not in URL_MAP:
                continue
            if (_ep_model == _req_model_base or _ep_model == "*") and \
               (_req_node_hint is None or _req_node_hint == _ep_node):
                _native_endpoint = {
                    "url":   URL_MAP[_ep_node],
                    "token": TOKEN_MAP.get(_ep_node, "ollama"),
                    "model": _req_model_base,  # Model name only to backend, no @host suffix
                    "node":  _ep_node,
                }
                break
        # Fallback: user-owned private connections (lower priority than global URL_MAP)
        if not _native_endpoint and _user_conns_map:
            if _req_node_hint and _req_node_hint in _user_conns_map:
                _uc = _user_conns_map[_req_node_hint]
                _native_endpoint = {
                    "url":        _uc["url"],
                    "token":      _uc.get("api_key") or "ollama",
                    "model":      _req_model_base,
                    "node":       _req_node_hint,
                    "api_type":   _uc.get("api_type", "openai"),
                    "_user_conn": True,
                }
            elif not _req_node_hint:
                # Bare model name: check models_cache of each user connection
                for _cname, _uc in _user_conns_map.items():
                    if _req_model_base in _uc.get("models_cache", []):
                        _native_endpoint = {
                            "url":        _uc["url"],
                            "token":      _uc.get("api_key") or "ollama",
                            "model":      _req_model_base,
                            "node":       _cname,
                            "api_type":   _uc.get("api_type", "openai"),
                            "_user_conn": True,
                        }
                        break
    _user_tmpls_json = user_ctx.get("user_templates_json", "{}")
    # Template name match is NOT authorization — check expert_template permissions explicitly.
    if _tmpl_override:
        _allowed_tmpls = user_perms.get("expert_template", [])
        if _tmpl_override not in _allowed_tmpls:
            return JSONResponse(status_code=403, content={"error": {
                "message": f"Template '{request.model}' is not authorized for this API key",
                "type": "permission_denied",
                "code": "template_not_authorized",
            }})
    _user_conns_json = user_ctx.get("user_connections_json", "{}")
    user_experts  = _resolve_user_experts(user_ctx.get("permissions_json", ""), override_tmpl_id=_tmpl_override,
                                           user_templates_json=_user_tmpls_json,
                                           admin_override=False,
                                           user_connections_json=_user_conns_json) or {}
    _tmpl_prompts = _resolve_template_prompts(user_ctx.get("permissions_json", ""), override_tmpl_id=_tmpl_override,
                                               user_templates_json=_user_tmpls_json,
                                               admin_override=False,
                                               user_connections_json=_user_conns_json)
    # Ghost-template detection: template in permissions but absent from DB
    if _tmpl_override and not user_experts and not user_perms.get("expert_template") == []:
        _ghost_check = next((t for t in _all_tmpls if t.get("id") == _tmpl_override), None)
        if _ghost_check is None:
            logger.error("Ghost template detected: %s in permissions but not in DB", _tmpl_override)
            return JSONResponse(status_code=422, content={"error": {
                "message": f"Template '{_tmpl_override}' is configured but no longer exists in the database",
                "type": "template_not_found",
                "code": "ghost_template",
            }})

    # Extract system message (coding agents send file/codebase context here)
    system_msgs   = [m for m in request.messages if m.role == "system"]
    system_prompt = _oai_content_to_str(system_msgs[0].content) if system_msgs else ""

    # Last user message as the actual query
    user_msgs  = [m for m in request.messages if m.role in ("user", "assistant")]
    last_user  = next((m for m in reversed(request.messages) if m.role == "user"), None)
    _user_images = _extract_oai_images(last_user.content) if last_user else []
    allowed_skills = user_perms.get("skill")  # None = all allowed (backwards compatible)
    _raw_user_input = _oai_content_to_str(last_user.content) if last_user else ""
    user_input = await _resolve_skill_secure(_raw_user_input, allowed_skills, user_id=user_id, session_id=session_id)
    # Shadow-Mode: sample every BENCHMARK_SHADOW_RATE-th request to the candidate template.
    # Fire-and-forget — never blocks the production response.
    global _shadow_request_counter
    if BENCHMARK_SHADOW_TEMPLATE:
        with _shadow_lock:
            _shadow_request_counter += 1
            _fire_shadow = (_shadow_request_counter % BENCHMARK_SHADOW_RATE == 0)
        if _fire_shadow:
            asyncio.create_task(_shadow_request(_raw_user_input, user_id, raw_key or ""))
            logger.debug(f"🔬 Shadow request enqueued (counter={_shadow_request_counter})")

    _pending_reports: List[str] = []
    if user_input != _raw_user_input:
        _sm = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)", _raw_user_input)
        _sname = _sm.group(1) if _sm else "?"
        _sargs = _raw_user_input[len(_sname)+1:].strip() if _sm else ""
        _pending_reports.append(
            f"🎯 Skill /{_sname} resolved (args: '{_sargs}', {len(user_input)} chars):\n{user_input}"
        )
    else:
        # No manual skill command → auto-detect whether a known file is attached.
        # Skip file-skill detection when the message already contains an explicit routing
        # directive (e.g. from the GAIA benchmark runner or structured data injection).
        # The directive "[ROUTE TO: reasoning OR general" signals that the caller has
        # already classified the content and no automatic skill dispatch should occur.
        _routing_bypass = (
            "[ROUTE TO: reasoning OR general" in _raw_user_input
            or "[ROUTING: Use reasoning or general expert" in _raw_user_input
        )
        _auto_skill = None if _routing_bypass else _detect_file_skill(
            request.files, _raw_user_input, allowed_skills
        )
        if _auto_skill:
            _auto_input = f"/{_auto_skill} {_raw_user_input}"
            _resolved = await _resolve_skill_secure(_auto_input, allowed_skills, user_id=user_id, session_id=session_id)
            if _resolved != _auto_input:  # skill exists and was resolved
                user_input = _resolved
                _pending_reports.append(f"📎 File skill /{_auto_skill} triggered automatically")

    # Open WebUI internal requests (follow-ups, title, autocomplete) directly without pipeline
    # Skip in agent mode — coding tools do not send OpenWebUI-internal markers
    if mode != "agent" and _is_openwebui_internal(request.messages):
        return await _handle_internal_direct(request.messages, chat_id, request.stream)

    # Model availability check: does the requested model actually exist on the target node?
    # Prevents hanging requests when a model is requested via wildcard permission,
    # but is not present on the host (e.g. gemma4:31b@AIHUB even though it is not installed).
    # Skipped for user-owned connections — their models_cache already served that check.
    if _native_endpoint and not _native_endpoint.get("_user_conn"):
        _avail_models = await _get_available_models(_native_endpoint["node"])
        if _avail_models is not None and _native_endpoint["model"] not in _avail_models:
            _avail_list = sorted(_avail_models)
            logger.warning(
                "Model-Not-Found: '%s' not available on %s. Available: %s",
                _native_endpoint["model"], _native_endpoint["node"], _avail_list,
            )
            return JSONResponse(
                status_code=404,
                content={"error": {
                    "message": (
                        f"Model '{_native_endpoint['model']}' is not available on "
                        f"{_native_endpoint['node']}. "
                        f"Available models: {_avail_list}"
                    ),
                    "type":  "invalid_request_error",
                    "code":  "model_not_found",
                }},
            )

    # Live monitoring: register request as active
    _tmpl_name = request.model if _tmpl_override else ""
    _req_type  = "streaming" if request.stream else "batch"
    _req_type  = "native"    if _native_endpoint else _req_type
    _client_ip = raw_request.client.host if raw_request.client else ""
    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=user_id, model=_req_model_base,
        moe_mode=("native" if _native_endpoint else mode),
        req_type=_req_type, template_name=_tmpl_name, client_ip=_client_ip,
        backend_model=_native_endpoint["model"] if _native_endpoint else "",
        backend_host=_native_endpoint["node"]  if _native_endpoint else "",
        api_key_id=api_key_id,
    ))

    # Conversation history: only user/assistant messages (no system messages)
    # Multimodal content is extracted as text (history for MoE pipeline is text-only)
    raw_history = [
        {"role": m.role, "content": _oai_content_to_str(m.content)}
        for m in request.messages
        if m.role in ("user", "assistant") and m != last_user
    ]
    _hist_turns = _tmpl_prompts.get("history_max_turns", 0) or None
    _hist_chars = _tmpl_prompts.get("history_max_chars", 0) or None
    history = _truncate_history(raw_history, max_turns=_hist_turns, max_chars=_hist_chars)

    # Native LLM: forward directly to endpoint, no MoE pipeline
    if _native_endpoint:
        if request.stream:
            return StreamingResponse(
                _stream_native_llm(request, chat_id, _native_endpoint, user_id, request.model, session_id=session_id),
                media_type="text/event-stream",
            )
        # Non-streaming native: blockierender httpx-Call
        async with httpx.AsyncClient(timeout=300) as _hc:
            _nr = await _hc.post(
                _native_endpoint["url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {_native_endpoint['token']}", "Content-Type": "application/json"},
                json={"model": _native_endpoint["model"],
                      "messages": [{"role": m.role, "content": m.content if m.content is not None else ""} for m in request.messages],
                      "stream": False,
                      **({"max_tokens": request.max_tokens} if request.max_tokens else {}),
                      **({"temperature": request.temperature} if request.temperature else {})},
            )
        _nr.raise_for_status()
        _nj = _nr.json()
        _nu = _nj.get("usage", {})
        if user_id != "anon":
            asyncio.create_task(_log_usage_to_db(user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                model=request.model, moe_mode="native",
                prompt_tokens=_nu.get("prompt_tokens", 0), completion_tokens=_nu.get("completion_tokens", 0),
                session_id=session_id))
            asyncio.create_task(_increment_user_budget(user_id, _nu.get("total_tokens", 0), prompt_tokens=_nu.get("prompt_tokens", 0), completion_tokens=_nu.get("completion_tokens", 0)))
        asyncio.create_task(_deregister_active_request(chat_id))
        _nj["id"] = chat_id
        return _nj

    _moe_resp_headers = {}
    if _tmpl_override:
        _moe_resp_headers["X-MoE-Template-Id"]   = _tmpl_override
        _moe_resp_headers["X-MoE-Template-Name"]  = _tmpl_name or request.model

    # Expose user token budget so callers can track their quota without a separate API call.
    if user_id and user_id != "anon" and redis_client is not None:
        try:
            from datetime import date as _date
            _today = _date.today().strftime("%Y-%m-%d")
            _used_tok = int(await redis_client.get(f"user:{user_id}:tokens:daily:{_today}") or 0)
            _moe_resp_headers["X-MoE-Budget-Daily-Used"] = str(_used_tok)
            _daily_limit = user_ctx.get("budget_daily")
            if _daily_limit:
                _moe_resp_headers["X-MoE-Budget-Daily-Limit"] = str(_daily_limit)
        except Exception:
            pass

    if request.stream:
        return StreamingResponse(
            stream_response(user_input, chat_id, mode, chat_history=history,
                            system_prompt=system_prompt, user_id=user_id,
                            user_permissions=user_perms, user_experts=user_experts,
                            planner_prompt=_tmpl_prompts["planner_prompt"],
                            judge_prompt=_tmpl_prompts["judge_prompt"],
                            judge_model_override=_tmpl_prompts["judge_model_override"],
                            judge_url_override=_tmpl_prompts["judge_url_override"],
                            judge_token_override=_tmpl_prompts["judge_token_override"],
                            planner_model_override=_tmpl_prompts["planner_model_override"],
                            planner_url_override=_tmpl_prompts["planner_url_override"],
                            planner_token_override=_tmpl_prompts["planner_token_override"],
                            model_name=request.model,
                            pending_reports=_pending_reports,
                            images=_user_images,
                            session_id=session_id,
                            max_agentic_rounds=_tmpl_prompts.get("max_agentic_rounds", 0),
                            no_cache=request.no_cache),
            media_type="text/event-stream",
            headers=_moe_resp_headers or None,
        )
    result = await app_graph.ainvoke(
        {"input": user_input, "response_id": chat_id, "mode": mode,
         "user_id": user_id, "api_key_id": api_key_id,
         "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
         "chat_history": history, "reasoning_trace": "", "system_prompt": system_prompt,
         "images": _user_images,
         "user_permissions": user_perms, "user_experts": user_experts,
         # Prepend personal namespace so user-created knowledge is private by default.
         # graph_tenant permissions add team/tenant namespaces on top.
         "tenant_ids": ([f"user:{user_id}"] if user_id and user_id != "anon" else [])
                       + [t for t in user_perms.get("graph_tenant", [])
                          if t != f"user:{user_id}"],
         "provenance_sources": [],
         "output_skill_body": "",
         "enable_cache": _tmpl_prompts.get("enable_cache", True),
         "enable_graphrag": _tmpl_prompts.get("enable_graphrag", True),
         "enable_web_research": _tmpl_prompts.get("enable_web_research", True),
         "graphrag_max_chars": _tmpl_prompts.get("graphrag_max_chars", 0),
         "history_max_turns": _tmpl_prompts.get("history_max_turns", 0),
         "history_max_chars": _tmpl_prompts.get("history_max_chars", 0),
         "planner_prompt": _tmpl_prompts["planner_prompt"],
         "judge_prompt":   _tmpl_prompts["judge_prompt"],
         "judge_model_override":   _tmpl_prompts["judge_model_override"],
         "judge_url_override":     _tmpl_prompts["judge_url_override"],
         "judge_token_override":   _tmpl_prompts["judge_token_override"],
         "planner_model_override": _tmpl_prompts["planner_model_override"],
         "planner_url_override":   _tmpl_prompts["planner_url_override"],
         "planner_token_override": _tmpl_prompts["planner_token_override"],
         "template_name":  _tmpl_name,
         "pending_reports": _pending_reports,
         "max_agentic_rounds": _tmpl_prompts.get("max_agentic_rounds", 0),
         "agentic_iteration": 0,
         "agentic_history": [],
         "agentic_gap": "",
         "attempted_queries": [],
         "search_strategy_hint": "",
         "no_cache": request.no_cache},
        {"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    p_tok = result.get("prompt_tokens",     0)
    c_tok = result.get("completion_tokens", 0)
    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=MODES.get(mode, MODES["default"])["model_id"],
            moe_mode=mode, prompt_tokens=p_tok, completion_tokens=c_tok,
            session_id=session_id,
        ))
        asyncio.create_task(_increment_user_budget(user_id, p_tok + c_tok, prompt_tokens=p_tok, completion_tokens=c_tok))
    asyncio.create_task(_deregister_active_request(chat_id))
    resp = {
        "id":      chat_id,
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   MODES.get(mode, MODES["default"])["model_id"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result["final_response"]}, "finish_reason": "stop"}],
        "usage":   {
            "prompt_tokens":     p_tok,
            "completion_tokens": c_tok,
            "total_tokens":      p_tok + c_tok,
        },
    }
    # Add provenance metadata if available (non-standard, backward-compatible)
    _prov = result.get("provenance_sources")
    if _prov:
        resp["metadata"] = {"sources": _prov}
    if _moe_resp_headers:
        return JSONResponse(content=resp, headers=_moe_resp_headers)
    return resp

# ============================================================
#  ANTHROPIC MESSAGES API  (/v1/messages)
#  Enables Claude Code CLI and other Anthropic API clients
#  to use the MoE Orchestrator.
#
#  Routing:
#    - Requests WITH tools / tool_results → judge_llm (magistral:24b via Ollama)
#      with OpenAI→Anthropic format conversion
#    - Pure text requests                 → MoE Agent-Pipeline (mode="agent")
# ============================================================

# _anthropic_content_to_text, _extract_images, _extract_oai_images,
# _anthropic_to_openai_messages, _anthropic_tools_to_openai — see parsing.py


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _anthropic_content_blocks_to_sse(
    content_blocks: list, chat_id: str, model_id: str,
    input_tokens: int, output_tokens: int, stop_reason: str
):
    """Emit finished content blocks as Anthropic SSE stream."""
    yield _sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [], "model": model_id,
            "stop_reason": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1}
        }
    })
    for idx, block in enumerate(content_blocks):
        if block["type"] == "text":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "text", "text": ""}
            })
            if idx == 0:
                yield _sse_event("ping", {"type": "ping"})
            text = block.get("text", "")
            for i in range(0, max(len(text), 1), SSE_CHUNK_SIZE):
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "text_delta", "text": text[i:i+50]}
                })
                await asyncio.sleep(0.005)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block["type"] == "thinking":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "thinking", "thinking": ""}
            })
            if idx == 0:
                yield _sse_event("ping", {"type": "ping"})
            thinking_text = block.get("thinking", "")
            for i in range(0, max(len(thinking_text), 1), SSE_CHUNK_SIZE):
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "thinking_delta", "thinking": thinking_text[i:i+50]}
                })
                await asyncio.sleep(0.005)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block["type"] == "tool_use":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {
                    "type": "tool_use", "id": block["id"],
                    "name": block["name"], "input": {}
                }
            })
            args_json = json.dumps(block.get("input", {}))
            yield _sse_event("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": args_json}
            })
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
    yield _sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens}
    })
    yield _sse_event("message_stop", {"type": "message_stop"})


async def _anthropic_tool_handler(body: dict, chat_id: str, tool_model: Optional[str] = None, tool_url: Optional[str] = None, tool_token: Optional[str] = None, tool_timeout: Optional[int] = None, tool_node: Optional[str] = None, user_id: str = "anon", api_key_id: str = "", session_id: str = None):
    """Forwards tool-capable requests to an inference server and converts formats.

    tool_model/tool_url/tool_token: override default judge if specified (e.g. for Claude Code sessions).
    tool_timeout: per-node timeout in seconds (fallback: JUDGE_TIMEOUT).
    tool_node: node name for Prometheus labels (e.g. "N04-RTX").
    """
    model_id   = body.get("model", "moe-orchestrator-agent")
    messages   = body.get("messages", [])
    system     = body.get("system") or ""
    # Prepend system-prompt prefix from the active Claude Code profile
    if _CC_SYSTEM_PREFIX and system:
        system = f"{_CC_SYSTEM_PREFIX}\n\n{system}"
    elif _CC_SYSTEM_PREFIX:
        system = _CC_SYSTEM_PREFIX
    tools      = body.get("tools", [])
    do_stream  = body.get("stream", False)
    max_tokens = body.get("max_tokens", TOOL_MAX_TOKENS)

    # Eval timing + input classification
    _eval_t0 = time.monotonic()
    _last_msg = messages[-1] if messages else {}
    _last_content = _last_msg.get("content", "")
    _last_msg_type = (
        "tool_result" if (
            isinstance(_last_content, list)
            and any(b.get("type") == "tool_result" for b in _last_content)
        )
        else "tool_use_request" if tools
        else "text"
    )

    oai_messages = _anthropic_to_openai_messages(messages, system)
    oai_tools    = _anthropic_tools_to_openai(tools) if tools else None

    effective_model = tool_model or JUDGE_MODEL
    effective_url   = tool_url   or JUDGE_URL
    effective_token = tool_token or JUDGE_TOKEN
    effective_node  = tool_node or "unknown"
    _node_timeout   = float(tool_timeout if tool_timeout is not None else JUDGE_TIMEOUT)

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
                                  session_id: str = None):
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

    invoke_state = {
        "input": user_input, "response_id": chat_id,
        "mode": "agent_orchestrated" if CLAUDE_CODE_MODE == "moe_orchestrated" else "agent",
        "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
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
                    asyncio.create_task(_increment_user_budget(user_id, in_tok + out_tok, prompt_tokens=in_tok, completion_tokens=out_tok))

                for i in range(0, max(len(content), 1), SSE_CHUNK_SIZE):
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": content[i:i+50]}
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
        asyncio.create_task(_increment_user_budget(user_id, _p_tok + _c_tok, prompt_tokens=_p_tok, completion_tokens=_c_tok))
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


@app.post("/v1/messages")
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
            _effective_tool_url      = URL_MAP.get(_effective_tool_endpoint) or _CLAUDE_CODE_TOOL_URL
            _effective_tool_token    = TOKEN_MAP.get(_effective_tool_endpoint, "ollama")
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
                                             session_id=session_id)
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


@app.post("/v1/admin/ontology/trigger")
async def trigger_ontology_healer(body: dict = None):
    """Kick off one gap-healer iteration in the background."""
    import uuid as _uuid
    body = body or {}
    if redis_client is not None:
        try:
            cur = await redis_client.hgetall(_ONTOLOGY_RUN_KEY)
            if cur and cur.get("status") == "running":
                return {"ok": False, "reason": "already_running", "status": cur}
        except Exception:
            pass
    concurrency = max(1, min(32, int(body.get("concurrency") or 4)))
    batch_size = max(1, min(200, int(body.get("batch_size") or 20)))
    run_id = _uuid.uuid4().hex[:12]
    asyncio.create_task(_run_healer_task(concurrency, batch_size, run_id))
    return {"ok": True, "run_id": run_id}


@app.delete("/v1/admin/ontology/status")
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
_dedicated_healer_proc: "asyncio.subprocess.Process | None" = None
# Mutex prevents concurrent auto-restart tasks (stream task + watchdog can both trigger).
_dedicated_healer_restart_lock = asyncio.Lock()


async def _auto_resume_dedicated_healer() -> None:
    """Resume the dedicated healer after a container restart if Redis shows it was running.

    Called as a background task during startup. A short sleep lets the ASGI
    server finish binding before the healer subprocess sends its first request
    to the local API.
    """
    global _dedicated_healer_proc
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
        _dedicated_healer_proc = proc
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
                _dedicated_healer_proc = None
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


async def _stream_dedicated_healer(
    proc: "asyncio.subprocess.Process",
    *,
    first_line: "bytes | None" = None,
) -> None:
    """Read stdout from the dedicated healer loop and update Redis counters.

    first_line: optional pre-read line from the early-exit detection in the
    start endpoint — passed in to avoid dropping the first output token.
    Writes a history entry to moe:maintenance:ontology:runs on completion.
    """
    import time as _t
    import uuid as _uuid
    stats: dict = {"processed": 0, "written": 0, "failed": 0}
    assert proc.stdout is not None
    start_ts = _t.time()

    async def _handle(text: str) -> None:
        if "✓" in text and "→" in text:
            stats["written"] += 1
        elif "?" in text and "→" in text:
            stats["processed"] += 1
        elif "✗" in text:
            stats["failed"] += 1
        await _set_healer_status_dedicated(last_activity_ts=str(_t.time()), **stats)

    try:
        if first_line:
            await _handle(first_line.decode(errors="replace"))
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            await _handle(line.decode(errors="replace"))
        rc = await proc.wait()
    except Exception:
        rc = -1
    if redis_client is not None:
        try:
            cur = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "stopped", "exit_code": str(rc),
            })
            # Persist run to the shared history list (same key as one-shot healer).
            template = (cur or {}).get("template", "")
            entry = json.dumps({
                "run_id": str(_uuid.uuid4()),
                "type": "dedicated",
                "template": template,
                "started_at": float((cur or {}).get("started_at", start_ts)),
                "finished_at": _t.time(),
                "exit_code": rc,
                **stats,
            })
            await redis_client.lpush(_ONTOLOGY_RUNS_HISTORY_KEY, entry)
            await redis_client.ltrim(_ONTOLOGY_RUNS_HISTORY_KEY, 0, 99)
        except Exception:
            pass

    # Auto-restart: if the flag is set the user wants a permanent daemon loop.
    # Re-spawn after a short pause so transient failures don't tight-loop.
    await _dedicated_healer_auto_restart_if_needed(template_hint=(cur or {}).get("template", "") if redis_client else "")


_HEALER_STALL_SECONDS = 300  # 5 minutes without output → stalled
_HEALER_RESTART_DELAY_S = 30  # pause between auto-restarts


async def _dedicated_healer_auto_restart_if_needed(template_hint: str = "") -> None:
    """Re-spawn the dedicated healer if auto_restart=1 is set in Redis.

    Called after a subprocess exits (clean or error). Waits HEALER_RESTART_DELAY_S
    before spawning so rapid crash-loops don't thrash the system.
    The restart lock ensures only one concurrent restart attempt runs at a time —
    both the stream task and the watchdog can call this; without the lock they
    would produce a restart storm.
    """
    import time as _t
    global _dedicated_healer_proc

    # Non-blocking: if another restart is already in flight, skip.
    if _dedicated_healer_restart_lock.locked():
        return
    async with _dedicated_healer_restart_lock:
        if redis_client is None:
            return
        try:
            cur = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
        except Exception:
            return
        if (cur or {}).get("auto_restart") != "1":
            return

        template = (cur or {}).get("template", "") or template_hint
        if not template:
            return

        logger.info("🔄 Dedicated healer exited — auto-restart in %ds (template=%s)", _HEALER_RESTART_DELAY_S, template)
        await asyncio.sleep(_HEALER_RESTART_DELAY_S)

        # Bail if user stopped the healer while we were sleeping.
        try:
            cur2 = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
            if (cur2 or {}).get("auto_restart") != "1":
                return
        except Exception:
            return

        env = os.environ.copy()
        env["TEMPLATE_POOL"] = template
        env.setdefault("REQUEST_TIMEOUT", "900")
        env.setdefault("MOE_API_BASE", "http://localhost:8000")
        sys_key = (os.environ.get("SYSTEM_API_KEY", "") or os.environ.get("MOE_API_KEY", "")).strip()
        if sys_key:
            env["MOE_API_KEY"] = sys_key
        try:
            new_proc = await asyncio.create_subprocess_exec(
                "python3", "/app/scripts/gap_healer_templates.py",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            _dedicated_healer_proc = new_proc
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "running",
                "stalled": "0",
                "pid": str(new_proc.pid),
                "started_at": str(_t.time()),
                "last_activity_ts": str(_t.time()),
                "processed": "0",
                "written": "0",
                "failed": "0",
                "auto_restart": "1",
            })
            asyncio.create_task(_stream_dedicated_healer(new_proc))
            logger.info("✅ Dedicated healer auto-restarted — PID %s", new_proc.pid)
        except Exception as exc:
            logger.error("❌ Dedicated healer auto-restart failed: %s", exc)


async def _watchdog_dedicated_healer() -> None:
    """Periodic watchdog: escalate or auto-restart a stalled dedicated healer.

    Runs every 60 s. If the healer reports 'running' in Redis but has not
    produced any stdout output for HEALER_STALL_SECONDS, it is marked 'stalled'
    and the subprocess is restarted automatically.
    """
    import time as _t
    global _dedicated_healer_proc

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
        proc = _dedicated_healer_proc
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
            _dedicated_healer_proc = new_proc
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


async def _set_healer_status_dedicated(**fields) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={k: str(v) for k, v in fields.items()})
    except Exception:
        pass


@app.post("/v1/admin/ontology/dedicated/start")
async def start_dedicated_healer(body: dict = None):
    """Start a permanent gap-healer loop pinned to a single curator template.

    Waits up to 3 s for the subprocess to emit its first output line. If the
    process exits within those 3 s it is considered a start failure and the
    endpoint returns ok=False with the exit code so the UI can show an error
    instead of silently marking status as 'running'.
    """
    global _dedicated_healer_proc
    import time as _t
    body = body or {}
    template = (body.get("template") or "").strip()
    if not template:
        return {"ok": False, "reason": "template_required"}

    # Reject if an in-memory process is still alive.
    if _dedicated_healer_proc is not None and _dedicated_healer_proc.returncode is None:
        return {"ok": False, "reason": "already_running"}

    # Also check Redis for a live process from a previous container run.
    if redis_client is not None:
        try:
            cur = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
            if cur and cur.get("status") in ("running", "starting"):
                pid = int(cur.get("pid", 0))
                if pid:
                    try:
                        os.kill(pid, 0)
                        return {"ok": False, "reason": "already_running", "pid": pid}
                    except OSError:
                        pass  # stale — fall through to clean start
        except Exception:
            pass

    # Always wipe stale Redis state so the UI starts from a clean slate.
    if redis_client is not None:
        try:
            await redis_client.delete(_DEDICATED_HEALER_KEY)
        except Exception:
            pass

    env = os.environ.copy()
    env["TEMPLATE_POOL"] = template
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    # Prefer SYSTEM_API_KEY, fall back to MOE_API_KEY — either way the subprocess needs one.
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
    except Exception as exc:
        return {"ok": False, "reason": "spawn_failed", "error": str(exc)[:200]}

    _dedicated_healer_proc = proc

    # Write "starting" state immediately so the UI can show a spinner.
    if redis_client is not None:
        try:
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "starting",
                "template": template,
                "pid": str(proc.pid),
                "started_at": str(_t.time()),
                "processed": "0",
                "written": "0",
                "failed": "0",
                "stalled": "0",
                "auto_restart": "1",
            })
        except Exception:
            pass

    # Early-exit detection: race between first stdout line and process exit.
    first_line: "bytes | None" = None
    try:
        first_line_task = asyncio.create_task(proc.stdout.readline())
        wait_task = asyncio.create_task(proc.wait())
        done, pending = await asyncio.wait(
            {first_line_task, wait_task},
            timeout=3.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if wait_task in done:
            # Process died before producing any output — start failed.
            rc = wait_task.result()
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            _dedicated_healer_proc = None
            if redis_client is not None:
                try:
                    await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                        "status": "stopped", "exit_code": str(rc),
                    })
                except Exception:
                    pass
            return {"ok": False, "reason": "process_exited_immediately", "exit_code": rc}

        # Process is still alive — cancel wait_task, preserve first line if available.
        wait_task.cancel()
        try:
            await wait_task
        except asyncio.CancelledError:
            pass
        if first_line_task in done:
            first_line = first_line_task.result() or None
        else:
            first_line_task.cancel()
            try:
                await first_line_task
            except asyncio.CancelledError:
                pass
    except Exception:
        pass  # Timeout or unexpected — continue anyway, process may be alive

    # Confirmed running — update Redis and hand off to the streaming task.
    if redis_client is not None:
        try:
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "running"})
        except Exception:
            pass

    asyncio.create_task(_stream_dedicated_healer(proc, first_line=first_line))
    return {"ok": True, "pid": proc.pid, "template": template}


@app.post("/v1/admin/ontology/dedicated/stop")
async def stop_dedicated_healer():
    """Stop the running dedicated healer loop."""
    global _dedicated_healer_proc
    import signal as _sig

    stopped = False
    # Try in-memory handle first
    if _dedicated_healer_proc is not None and _dedicated_healer_proc.returncode is None:
        try:
            _dedicated_healer_proc.terminate()
            await asyncio.wait_for(_dedicated_healer_proc.wait(), timeout=5.0)
        except Exception:
            try:
                _dedicated_healer_proc.kill()
            except Exception:
                pass
        _dedicated_healer_proc = None
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


@app.get("/v1/admin/ontology/dedicated/status")
async def get_dedicated_healer_status():
    """Return the current state of the dedicated healer loop."""
    if redis_client is None:
        # Fallback to in-memory check
        if _dedicated_healer_proc is not None and _dedicated_healer_proc.returncode is None:
            return {"status": "running", "pid": _dedicated_healer_proc.pid}
        return {"status": "stopped"}
    try:
        data = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
        if not data:
            return {"status": "stopped"}

        # Verify the process is still alive
        import time as _t
        if data.get("status") in ("running", "stalled"):
            pid = int(data.get("pid", 0))
            if pid:
                try:
                    os.kill(pid, 0)
                except OSError:
                    # Process gone — mark as stopped
                    await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "stopped"})
                    data["status"] = "stopped"

        # Compute staleness for the caller.
        last_ts = float(data.get("last_activity_ts") or 0)
        age = round(_t.time() - last_ts) if last_ts else None
        data["activity_age_seconds"] = str(age) if age is not None else ""
        data["stalled"] = data.get("stalled", "0")
        return dict(data)
    except Exception as e:
        return {"status": "unknown", "error": str(e)[:100]}


@app.get("/v1/admin/ontology/dedicated/verify")
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
    if not pid_alive and _dedicated_healer_proc is not None and _dedicated_healer_proc.returncode is None:
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


@app.post("/v1/admin/benchmark/lock")
async def benchmark_lock(body: dict = None):
    """Reserve all nodes used by a template exclusively for benchmark runs.

    While the lock is active, any request whose model/template differs from the
    reserved template receives HTTP 503. Fails open if Redis is unavailable.
    """
    body = body or {}
    template_name = (body.get("template") or "").strip()
    if not template_name:
        return {"ok": False, "reason": "template_required"}

    templates = _read_expert_templates()
    tmpl = next((t for t in templates if t.get("name") == template_name or t.get("id") == template_name), None)
    if not tmpl:
        return {"ok": False, "reason": "template_not_found", "template": template_name}

    # _read_expert_templates() returns the parsed config_json merged into tmpl,
    # so tmpl itself is the config (keys: experts, planner_endpoint, name, id …).
    cfg = tmpl

    nodes: set = set()
    for cat_cfg in cfg.get("experts", {}).values():
        for m in cat_cfg.get("models", []):
            ep = (m.get("endpoint") or "").strip()
            if ep:
                nodes.add(ep)
    # Include top-level planner/judge endpoint overrides when present
    for key in ("planner_endpoint", "judge_endpoint"):
        ep = (cfg.get(key) or "").strip()
        if ep:
            nodes.add(ep)

    if not nodes:
        return {"ok": False, "reason": "no_nodes_found_in_template"}
    if redis_client is None:
        return {"ok": False, "reason": "redis_unavailable"}

    import time as _t
    await redis_client.delete(_BENCHMARK_RESERVED_KEY)
    await redis_client.sadd(_BENCHMARK_RESERVED_KEY, *nodes)
    await redis_client.hset(_BENCHMARK_LOCK_META_KEY, mapping={
        "template":  template_name,
        "nodes":     json.dumps(sorted(nodes)),
        "locked_at": str(_t.time()),
    })
    logger.info(f"🔒 Benchmark lock acquired: template={template_name}, nodes={sorted(nodes)}")
    return {"ok": True, "template": template_name, "reserved": sorted(nodes)}


@app.delete("/v1/admin/benchmark/lock")
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


@app.get("/v1/admin/benchmark/lock")
async def benchmark_lock_status():
    """Return the current benchmark node reservation status."""
    if redis_client is None:
        return {"active": False, "reason": "redis_unavailable"}
    try:
        raw = await redis_client.smembers(_BENCHMARK_RESERVED_KEY)
        reserved = sorted(v if isinstance(v, str) else v.decode() for v in raw)
        meta = await redis_client.hgetall(_BENCHMARK_LOCK_META_KEY) or {}
        if not reserved:
            return {"active": False}
        return {
            "active":     True,
            "template":   meta.get("template", ""),
            "nodes":      reserved,
            "locked_at":  meta.get("locked_at", ""),
        }
    except Exception as e:
        return {"active": False, "error": str(e)[:100]}


@app.get("/v1/admin/knowledge-stats")
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


@app.get("/v1/admin/ontology-gaps")
async def get_ontology_gaps(limit: int = 30):
    """Shows most frequent terms from answers not in the ontology."""
    if redis_client is None:
        return {"error": "Valkey not available"}
    try:
        gaps = await redis_client.zrevrange("moe:ontology_gaps", 0, limit - 1, withscores=True)
        return {"gaps": [{"term": g, "count": int(s)} for g, s in gaps]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/v1/admin/planner-patterns")
async def get_planner_patterns(limit: int = 20):
    """Shows proven planner patterns based on positive user feedback."""
    if redis_client is None:
        return {"error": "Valkey not available"}
    try:
        patterns = await redis_client.zrevrange("moe:planner_success", 0, limit - 1, withscores=True)
        return {"patterns": [{"signature": sig, "count": int(score)} for sig, score in patterns]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/v1/admin/tool-eval")
async def get_tool_eval_log(limit: int = 50):
    """Returns the last N records from tool_eval.jsonl as parsed JSON objects."""
    path = "/app/logs/tool_eval.jsonl"
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        records = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
            if len(records) >= limit:
                break
        return {"records": records, "total_lines": len(lines)}
    except FileNotFoundError:
        return {"records": [], "total_lines": 0}


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


async def _ollama_resolve_template(model_name: str, user_perms: dict):
    """Return (tmpl_id, tmpl_dict | None, error_response | None) for an Ollama model name."""
    all_tmpls   = _read_expert_templates()
    matched     = next((t for t in all_tmpls if t.get("name") == model_name or t.get("id") == model_name), None)
    if not matched:
        return None, None, None  # No template match — will fall through to default MoE mode
    tmpl_id      = matched["id"]
    allowed      = user_perms.get("expert_template", [])
    if tmpl_id not in allowed:
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

    tmpl_id, matched_tmpl, err = await _ollama_resolve_template(model_name, user_perms)
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


@app.get("/api/version")
async def ollama_version():
    """Ollama version stub — clients use this to detect Ollama compatibility."""
    return {"version": "0.6.0"}


@app.get("/api/tags")
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

    visible = templates if allowed is None else [t for t in templates if t.get("id") in allowed]
    return {"models": [_ollama_model_entry(t, now_iso=now) for t in visible]}


@app.get("/api/ps")
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

    visible = templates if allowed is None else [t for t in templates if t.get("id") in allowed]
    models = []
    for t in visible:
        entry = _ollama_model_entry(t, now_iso=now)
        entry["expires_at"] = expires
        entry["size_vram"]  = 0
        models.append(entry)
    return {"models": models}


@app.post("/api/show")
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


@app.post("/api/chat")
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


@app.post("/api/generate")
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


@app.post("/api/pull")
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


@app.delete("/api/delete")
async def ollama_delete():
    """Model deletion is not supported — managed via Admin UI."""
    return JSONResponse(status_code=400, content={
        "error": "Model deletion is managed via Admin UI"
    })


@app.post("/api/copy")
@app.post("/api/push")
@app.post("/api/embed")
@app.post("/api/embeddings")
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

"""
config.py — Application configuration from environment variables.

All os.getenv() calls that were previously scattered through main.py live here.
Imports: only stdlib (os, json). No logging, no heavy deps — this module is
imported before logging is configured in main.py.
"""

import json
import os

# =============================================================================
# Runtime flags
# =============================================================================

CORRECTION_MEMORY_ENABLED = os.getenv("CORRECTION_MEMORY_ENABLED", "true").lower() in ("1", "true", "yes")
_EDGE_MODE = os.getenv("ENVIRONMENT") == "edge_mobile"
LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO").upper()

# =============================================================================
# Kafka
# =============================================================================

KAFKA_BOOTSTRAP      = os.getenv("KAFKA_URL", "kafka://moe-kafka:9092").replace("kafka://", "")
KAFKA_TOPIC_INGEST   = "moe.ingest"
KAFKA_TOPIC_REQUESTS = "moe.requests"
KAFKA_TOPIC_FEEDBACK = "moe.feedback"
KAFKA_TOPIC_LINTING  = "moe.linting"
KAFKA_TOPIC_AUDIT    = "moe.audit"

# =============================================================================
# Database & cache
# =============================================================================

REDIS_URL               = os.getenv("REDIS_URL", "redis://terra_cache:6379")
POSTGRES_CHECKPOINT_URL = os.getenv("POSTGRES_CHECKPOINT_URL", "")
MOE_USERDB_URL          = os.getenv(
    "MOE_USERDB_URL",
    "postgresql://moe_admin@terra_checkpoints:5432/moe_userdb",
)

# =============================================================================
# Enterprise Data Stack (optional, default OFF)
# =============================================================================

_ENTERPRISE_ENABLED = os.getenv("INSTALL_ENTERPRISE_DATA_STACK", "false").lower() == "true"
NIFI_URL            = os.getenv("NIFI_URL", "")
MARQUEZ_URL         = os.getenv("MARQUEZ_URL", "")
LAKEFS_ENDPOINT     = os.getenv("LAKEFS_ENDPOINT", "")

# =============================================================================
# OIDC / Authentik
# =============================================================================

AUTHENTIK_URL = os.getenv("AUTHENTIK_URL", "")
OIDC_JWKS_URL = os.getenv(
    "OIDC_JWKS_URL",
    f"{AUTHENTIK_URL}/application/o/moe-sovereign/jwks/" if AUTHENTIK_URL else "",
)
OIDC_ISSUER   = os.getenv(
    "OIDC_ISSUER",
    f"{AUTHENTIK_URL}/application/o/moe-sovereign/" if AUTHENTIK_URL else "",
)
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "moe-infra")
OIDC_ENABLED   = bool(AUTHENTIK_URL)

# =============================================================================
# Neo4j
# =============================================================================

NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://neo4j-knowledge:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS")

# =============================================================================
# Inference servers
# =============================================================================

_INF_SERVERS_RAW = os.getenv("INFERENCE_SERVERS", "")
try:
    INFERENCE_SERVERS_LIST: list = json.loads(_INF_SERVERS_RAW) if _INF_SERVERS_RAW.strip() else []
except json.JSONDecodeError:
    INFERENCE_SERVERS_LIST = []  # invalid JSON — logged in main.py after logger is ready

INFERENCE_SERVERS_LIST = [s for s in INFERENCE_SERVERS_LIST if s.get("enabled", True)]
URL_MAP      = {s["name"]: s["url"]                 for s in INFERENCE_SERVERS_LIST if s.get("url")}
TOKEN_MAP    = {s["name"]: s.get("token", "ollama")  for s in INFERENCE_SERVERS_LIST}
API_TYPE_MAP = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}

JUDGE_ENDPOINT_NAME = os.getenv("JUDGE_ENDPOINT", "")
JUDGE_URL           = URL_MAP.get(JUDGE_ENDPOINT_NAME) if JUDGE_ENDPOINT_NAME else None
JUDGE_TOKEN         = TOKEN_MAP.get(JUDGE_ENDPOINT_NAME, "ollama") if JUDGE_ENDPOINT_NAME else "ollama"
JUDGE_MODEL         = os.getenv("JUDGE_MODEL", "magistral:24b")

GRAPH_INGEST_MODEL    = os.getenv("GRAPH_INGEST_MODEL", "")
_GRAPH_INGEST_EP_NAME = os.getenv("GRAPH_INGEST_ENDPOINT", "")
GRAPH_INGEST_URL      = URL_MAP.get(_GRAPH_INGEST_EP_NAME) if _GRAPH_INGEST_EP_NAME else None
GRAPH_INGEST_TOKEN    = TOKEN_MAP.get(_GRAPH_INGEST_EP_NAME, "ollama") if _GRAPH_INGEST_EP_NAME else "ollama"

PLANNER_MODEL    = os.getenv("PLANNER_MODEL", "phi4:14b")
PLANNER_ENDPOINT = os.getenv("PLANNER_ENDPOINT", os.getenv("JUDGE_ENDPOINT", ""))
PLANNER_URL      = URL_MAP.get(PLANNER_ENDPOINT) if PLANNER_ENDPOINT else None
PLANNER_TOKEN    = TOKEN_MAP.get(PLANNER_ENDPOINT, "ollama") if PLANNER_ENDPOINT else "ollama"

_JUDGE_BASE   = (JUDGE_URL   or "").rstrip("/").removesuffix("/v1")
_PLANNER_BASE = (PLANNER_URL or "").rstrip("/").removesuffix("/v1")

EXPERTS             = json.loads(os.getenv("EXPERT_MODELS", "{}"))
MCP_URL             = os.getenv("MCP_URL", "http://mcp-precision:8003")
GRAPH_VIA_MCP       = os.getenv("GRAPH_VIA_MCP", "false").lower() in ("1", "true", "yes")
MAX_GRAPH_CONTEXT_CHARS: int = int(os.getenv("MAX_GRAPH_CONTEXT_CHARS", "6000"))
LITELLM_URL         = os.getenv("LITELLM_URL", "").rstrip("/")

# =============================================================================
# Shadow/benchmark mode
# =============================================================================

BENCHMARK_SHADOW_TEMPLATE: str = os.getenv("BENCHMARK_SHADOW_TEMPLATE", "")
BENCHMARK_SHADOW_RATE: int     = int(os.getenv("BENCHMARK_SHADOW_RATE", "20"))

# =============================================================================
# Claude Code integration
# =============================================================================

_DEFAULT_CLAUDE_CODE_MODELS = (
    "claude-opus-4-6,claude-sonnet-4-6,claude-haiku-4-5-20251001,"
    "claude-opus-4-5,claude-sonnet-4-5,claude-3-5-sonnet-20241022,"
    "claude-3-5-haiku-20241022,claude-3-7-sonnet-20250219"
)
CLAUDE_CODE_MODELS = {
    m.strip()
    for m in os.getenv("CLAUDE_CODE_MODELS", _DEFAULT_CLAUDE_CODE_MODELS).split(",")
    if m.strip()
}
CLAUDE_CODE_TOOL_MODEL         = os.getenv("CLAUDE_CODE_TOOL_MODEL", "").strip().rstrip("*")
CLAUDE_CODE_TOOL_ENDPOINT      = os.getenv("CLAUDE_CODE_TOOL_ENDPOINT", os.getenv("JUDGE_ENDPOINT", ""))
CLAUDE_CODE_MODE               = os.getenv("CLAUDE_CODE_MODE", "moe_orchestrated")
CLAUDE_CODE_REASONING_MODEL    = os.getenv("CLAUDE_CODE_REASONING_MODEL", "")
CLAUDE_CODE_REASONING_ENDPOINT = os.getenv("CLAUDE_CODE_REASONING_ENDPOINT", "")
CLAUDE_CODE_TOOL_CHOICE        = os.getenv("CLAUDE_CODE_TOOL_CHOICE", "auto")

_CLAUDE_CODE_TOOL_URL      = URL_MAP.get(CLAUDE_CODE_TOOL_ENDPOINT) or JUDGE_URL
_CLAUDE_CODE_TOOL_TOKEN    = TOKEN_MAP.get(CLAUDE_CODE_TOOL_ENDPOINT, "ollama")
_CLAUDE_CODE_REASONING_URL = (
    URL_MAP.get(CLAUDE_CODE_REASONING_ENDPOINT) if CLAUDE_CODE_REASONING_ENDPOINT else None
)

# =============================================================================
# Expert-routing & cache thresholds
# =============================================================================

MAX_EXPERT_OUTPUT_CHARS   = int(os.getenv("MAX_EXPERT_OUTPUT_CHARS",    "2400"))
CACHE_HIT_THRESHOLD       = float(os.getenv("CACHE_HIT_THRESHOLD",      "0.15"))
SOFT_CACHE_THRESHOLD      = float(os.getenv("SOFT_CACHE_THRESHOLD",     "0.50"))
SOFT_CACHE_MAX_EXAMPLES   = int(os.getenv("SOFT_CACHE_MAX_EXAMPLES",    "2"))
ROUTE_THRESHOLD           = float(os.getenv("ROUTE_THRESHOLD",          "0.18"))
ROUTE_GAP                 = float(os.getenv("ROUTE_GAP",                "0.10"))
CACHE_MIN_RESPONSE_LEN    = int(os.getenv("CACHE_MIN_RESPONSE_LEN",     "150"))
EXPERT_TIER_BOUNDARY_B    = float(os.getenv("EXPERT_TIER_BOUNDARY_B",   "20"))
EXPERT_MIN_SCORE          = float(os.getenv("EXPERT_MIN_SCORE",         "0.3"))
EXPERT_MIN_DATAPOINTS     = int(os.getenv("EXPERT_MIN_DATAPOINTS",      "5"))

# =============================================================================
# Conversation history limits
# =============================================================================

HISTORY_MAX_TURNS   = int(os.getenv("HISTORY_MAX_TURNS",   "4"))
HISTORY_MAX_CHARS   = int(os.getenv("HISTORY_MAX_CHARS",   "3000"))
HISTORY_MAX_ENTRIES = int(os.getenv("HISTORY_MAX_ENTRIES", "5000"))

# =============================================================================
# Timeouts & LLM call limits
# =============================================================================

JUDGE_TIMEOUT   = int(os.getenv("JUDGE_TIMEOUT",   "900"))
EXPERT_TIMEOUT  = int(os.getenv("EXPERT_TIMEOUT",  "900"))
PLANNER_TIMEOUT = int(os.getenv("PLANNER_TIMEOUT", "300"))

JUDGE_REFINE_MAX_ROUNDS      = int(os.getenv("JUDGE_REFINE_MAX_ROUNDS",       "2"))
JUDGE_REFINE_MIN_IMPROVEMENT = float(os.getenv("JUDGE_REFINE_MIN_IMPROVEMENT", "0.15"))

TOOL_MAX_TOKENS      = int(os.getenv("TOOL_MAX_TOKENS",      "8192"))
REASONING_MAX_TOKENS = int(os.getenv("REASONING_MAX_TOKENS", "16384"))

PLANNER_RETRIES   = int(os.getenv("PLANNER_RETRIES",   "2"))
PLANNER_MAX_TASKS = int(os.getenv("PLANNER_MAX_TASKS", "4"))

SSE_CHUNK_SIZE = int(os.getenv("SSE_CHUNK_SIZE", "50"))

# =============================================================================
# Feedback & self-evaluation
# =============================================================================

EVAL_CACHE_FLAG_THRESHOLD   = int(os.getenv("EVAL_CACHE_FLAG_THRESHOLD",   "2"))
FEEDBACK_POSITIVE_THRESHOLD = int(os.getenv("FEEDBACK_POSITIVE_THRESHOLD", "4"))
FEEDBACK_NEGATIVE_THRESHOLD = int(os.getenv("FEEDBACK_NEGATIVE_THRESHOLD", "2"))

# =============================================================================
# Web search
# =============================================================================

_SEARXNG_URL            = os.getenv("SEARXNG_URL", "").strip()
_WEB_SEARCH_FALLBACK_DDG: bool = (
    os.getenv("WEB_SEARCH_FALLBACK_DDG", "true").strip().lower() not in ("0", "false", "no")
)

# =============================================================================
# AIHUB fallback
# =============================================================================

_AIHUB_FALLBACK_NODE         = os.getenv("AIHUB_FALLBACK_NODE", "")
_AIHUB_FALLBACK_MODEL        = os.getenv("AIHUB_FALLBACK_MODEL", "")
_AIHUB_FALLBACK_MODEL_SECOND = os.getenv("AIHUB_FALLBACK_MODEL_SECOND", "")

# Legacy aliases kept for backward compat with existing code references
_N04_FALLBACK_NODE         = _AIHUB_FALLBACK_NODE
_N04_FALLBACK_MODEL        = _AIHUB_FALLBACK_MODEL
_N04_FALLBACK_MODEL_SECOND = _AIHUB_FALLBACK_MODEL_SECOND

_FALLBACK_ENABLED = bool(_AIHUB_FALLBACK_NODE and _AIHUB_FALLBACK_MODEL)

# =============================================================================
# Thompson sampling
# =============================================================================

THOMPSON_SAMPLING_ENABLED = os.getenv("THOMPSON_SAMPLING_ENABLED", "true").lower() in ("1", "true", "yes")

# =============================================================================
# Fuzzy logic routing & graph compression
# =============================================================================

_FUZZY_VECTOR_THRESHOLD          = float(os.getenv("FUZZY_VECTOR_THRESHOLD",          "0.30"))
_FUZZY_GRAPH_THRESHOLD           = float(os.getenv("FUZZY_GRAPH_THRESHOLD",           "0.35"))
_GRAPH_COMPRESS_THRESHOLD_FACTOR = float(os.getenv("GRAPH_COMPRESS_THRESHOLD_FACTOR", "2.0"))
_GRAPH_COMPRESS_LLM_MODEL        = os.getenv("GRAPH_COMPRESS_LLM", "")
_GRAPH_COMPRESS_LLM_TIMEOUT      = float(os.getenv("GRAPH_COMPRESS_LLM_TIMEOUT", "3.0"))

# =============================================================================
# HTTP limits & CORS (used in middleware setup)
# =============================================================================

MAX_REQUEST_BODY_MB     = int(os.getenv("MAX_REQUEST_BODY_MB", "16"))
CORS_ALL_ORIGINS        = os.getenv("CORS_ALL_ORIGINS", "0") == "1"
CORS_ORIGINS_RAW        = os.getenv("CORS_ORIGINS", "")
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "60"))

# =============================================================================
# Custom expert prompts (admin override, rarely set)
# =============================================================================

_CUSTOM_EXPERT_PROMPTS: dict = json.loads(os.getenv("CUSTOM_EXPERT_PROMPTS", "{}"))

"""
conftest.py — Shared fixtures and module stubs for the MoE test suite.

All heavy external dependencies (langchain, langgraph, chromadb, redis, etc.)
are stubbed via sys.modules BEFORE any test module triggers their import so
that module-level code in main.py and mcp_server/server.py does not attempt
live connections during collection.
"""

import json
import os
import sys
from unittest.mock import MagicMock

# ── 1. Environment variables ─────────────────────────────────────────────────
# Must be set before main.py's module-level code reads them.

_FAKE_SERVERS = [
    {
        "name": "RTX",
        "url": "http://rtx-fake:11434",
        "gpu_count": 4,
        "token": "tok",
        "api_type": "ollama",
        "enabled": True,
    },
    {
        "name": "TESLA",
        "url": "http://tesla-fake:11434",
        "gpu_count": 2,
        "token": "sk-x",
        "api_type": "openai",
        "enabled": True,
    },
]

os.environ.setdefault("INFERENCE_SERVERS", json.dumps(_FAKE_SERVERS))
os.environ.setdefault("EXPERT_TEMPLATES", "[]")
os.environ.setdefault("EXPERT_MODELS", "{}")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("LITELLM_URL", "")
# Point to /dev/null so _read_expert_templates() skips the file read.
os.environ.setdefault("ENV_FILE", "/dev/null")
os.environ.setdefault("JUDGE_ENDPOINT", "")
os.environ.setdefault("PLANNER_ENDPOINT", "")
os.environ.setdefault("GRAPH_INGEST_ENDPOINT", "")
# Redirect mcp_server's file-generation dir away from /app/generated (Docker-only path)
os.environ.setdefault("MOE_GENERATED_DIR", "/tmp/moe-test-generated")

# ── 2. Stub all external modules not installed in the test environment ────────

_STUBS = [
    # main.py infrastructure deps
    "prometheus_client",
    "aiosqlite",
    "aiokafka",
    "aiokafka.errors",
    "redis",
    "redis.asyncio",
    "chromadb",
    "chromadb.utils",
    "chromadb.utils.embedding_functions",
    "langchain_openai",
    "langchain_community",
    "langchain_community.utilities",
    "langgraph",
    "langgraph.graph",
    "langgraph.checkpoint",
    "langgraph.checkpoint.redis",
    "langgraph.checkpoint.redis.aio",
    "langgraph.checkpoint.postgres",
    "langgraph.checkpoint.postgres.aio",
    # Postgres driver (added for the universal-deployment refactor — main.py
    # switched from redis-only checkpointing to postgres, so the test harness
    # must stub the psycopg stack to allow collection without a live DB.)
    "psycopg",
    "psycopg.rows",
    "psycopg_pool",
    # mcp_server/server.py infrastructure deps
    "mcp",
    "mcp.server",
    "mcp.server.fastmcp",
    "uvicorn",
    "fastapi",
    "fastapi.responses",
    "fastapi.middleware",
    "fastapi.middleware.cors",
    "pint",
    # graph_rag/manager.py deps
    "neo4j",
    # starlette (FastAPI dependency) — used directly in main.py for middleware
    "starlette",
    "starlette.middleware",
    "starlette.responses",
    "starlette.requests",
    "starlette.types",
]

for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# starlette.middleware.base needs BaseHTTPMiddleware as a real Python class so
# that `class Foo(_BaseHTTPMiddleware)` works without metaclass conflicts.
class _FakeBaseHTTPMiddleware:
    def __init__(self, app, *args, **kwargs):
        self.app = app
    async def dispatch(self, request, call_next):
        return await call_next(request)

_starlette_mw_base = MagicMock()
_starlette_mw_base.BaseHTTPMiddleware = _FakeBaseHTTPMiddleware
sys.modules["starlette.middleware.base"] = _starlette_mw_base

# Make FastAPI constructor return a sensible mock.
sys.modules["fastapi"].FastAPI = MagicMock(return_value=MagicMock())

# Make @mcp.tool() a transparent pass-through decorator so that all
# decorated functions in mcp_server/server.py remain callable.
_mock_mcp_instance = MagicMock()
_mock_mcp_instance.tool.return_value = lambda fn: fn
_mock_mcp_instance.sse_app.return_value = MagicMock()
sys.modules["mcp.server.fastmcp"].FastMCP = MagicMock(return_value=_mock_mcp_instance)

# ── 3. Shared data constants ─────────────────────────────────────────────────

FAKE_SERVERS = _FAKE_SERVERS

FAKE_TEMPLATE = [
    {
        "id": "tpl-test",
        "experts": {
            "research": {
                "system_prompt": "You are a research expert.",
                "models": [
                    {"model": "llama2", "endpoint": "RTX", "role": "primary"},
                    {"model": "mistral", "endpoint": "TESLA", "role": "fallback"},
                ],
            },
            "coding": {
                "system_prompt": "You are a coding expert.",
                "models": [
                    {"model": "deepseek", "endpoint": "RTX", "role": "always"},
                ],
            },
        },
    }
]

# Permissions JSON that references the fake template above.
FAKE_PERMS_WITH_TEMPLATE = json.dumps({"expert_template": ["tpl-test"]})
# Permissions JSON with no template → global EXPERTS fallback.
FAKE_PERMS_NO_TEMPLATE = json.dumps({})


import pytest  # noqa: E402 — must come after sys.modules setup


@pytest.fixture()
def fake_servers():
    """List of fake inference server configurations."""
    return FAKE_SERVERS


@pytest.fixture()
def fake_template():
    """A minimal expert template with primary/fallback/always roles."""
    return FAKE_TEMPLATE


@pytest.fixture()
def fake_perms_with_template():
    """Permissions JSON string pointing to the fake template."""
    return FAKE_PERMS_WITH_TEMPLATE


@pytest.fixture()
def fake_perms_no_template():
    """Permissions JSON string with no assigned template."""
    return FAKE_PERMS_NO_TEMPLATE

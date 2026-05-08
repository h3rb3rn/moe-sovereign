"""
state.py — Shared mutable application state.

All values start as None/False at import time.
The lifespan handler in main.py sets them at startup via attribute assignment:

    import state
    state.redis_client = aioredis.from_url(...)

Route files that need these objects must import the module, not the names:

    import state
    await state.redis_client.hgetall(...)   # live lookup at call time ✓

Importing the names directly (from state import redis_client) would capture
None at import time and miss the lifespan assignment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool
    from redis.asyncio import Redis
    from aiokafka import AIOKafkaProducer
    from graph_rag.manager import GraphRAGManager

# Set by lifespan() in main.py
redis_client:   Optional["Redis"]                = None
graph_manager:  Optional["GraphRAGManager"]      = None
kafka_producer: Optional["AIOKafkaProducer"]     = None
_userdb_pool:   Optional["AsyncConnectionPool"]  = None

# Set to True in lifespan() if INSTALL_ENTERPRISE_DATA_STACK=true AND services respond
_enterprise_reachable: bool = False

# Compiled LangGraph graph — set by lifespan() after checkpointer is ready
app_graph = None  # type: Optional[Any]  # avoids langgraph import at module level

# ChromaDB collections — set at module level in main.py (before lifespan)
cache_collection = None   # "moe_fact_cache" — used by feedback, cache lookup
route_collection = None   # "task_type_prototypes" — used by semantic routing

# Provider rate-limit state populated from response headers at request time.
# Format: {endpoint_name: {"remaining_tokens": int, "reset_time": float, ...}}
_provider_rate_limits: dict = {}

# Dedicated ontology gap-healer subprocess handle and restart lock.
# Both mutated by routes/admin_ontology.py and background tasks in main.py.
import asyncio as _asyncio
_dedicated_healer_proc = None       # asyncio.subprocess.Process | None
_dedicated_healer_restart_lock: _asyncio.Lock = _asyncio.Lock()

# MCP tool descriptions populated at startup by _load_mcp_tool_descriptions().
# Mutable runtime state — read by graph/nodes.py and main.py:_build_filtered_tool_desc.
MCP_TOOLS_DESCRIPTION: str            = ""   # full plain-text block for the planner
_MCP_TOOLS_DICT: dict[str, str]        = {}  # per-tool descriptions for domain filtering
MCP_TOOL_SCHEMAS: dict                 = {}  # per-tool arg schemas for pre-call validation
AGENTIC_CODE_TOOLS_DESCRIPTION: str   = ""   # repo_map / read_file_chunked / lsp_query block

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

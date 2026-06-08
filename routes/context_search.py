"""routes/context_search.py — Internal context-index retrieval endpoint.

Provides POST /v1/context/search for the search_context MCP tool.
Only reachable inside the Docker network (langgraph-orchestrator:8000) —
not exposed via a host port.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import state

router = APIRouter()


class _SearchRequest(BaseModel):
    session_id: str
    query: str
    n_results: int = 8


@router.post("/v1/context/search")
async def context_search(req: _SearchRequest, request: Request):
    """Retrieve semantically relevant context chunks for a session.

    Called by the search_context MCP tool (mcp-precision → langgraph-orchestrator).
    Returns the top-k chunks as a single concatenated string.
    """
    if not req.session_id or not req.query:
        return JSONResponse(status_code=400, content={"error": "session_id and query are required"})

    redis_client = state.redis_client
    if redis_client is None:
        return JSONResponse(status_code=503, content={"error": "Redis not available"})

    try:
        from services.context_index import is_context_indexed, retrieve_context_for_task
        if not await is_context_indexed(req.session_id, redis_client):
            return JSONResponse(content={"chunks": "", "indexed": False})

        chunks = await retrieve_context_for_task(
            session_id=req.session_id,
            task_text=req.query,
            redis_client=redis_client,
            n_results=req.n_results,
        )
        return JSONResponse(content={"chunks": chunks or "", "indexed": True})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})

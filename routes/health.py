"""routes/health.py — Liveness, metrics, observability, and MCP tool-server proxy."""

import asyncio
import os
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel

import state
from services.boundary_check import validate_boundary_contracts

router = APIRouter()

# Internal MCP precision-tools server — not exposed directly on the public port.
# These proxy endpoints make the MCP tool catalogue visible to Open WebUI's
# Tool Server feature (Admin → Settings → Tools → Add Tool Server).
_MCP_URL = os.getenv("MCP_URL", "http://mcp-precision:8003").rstrip("/")
_MCP_TIMEOUT = 10.0


class _InvokeRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = {}


@router.get("/tools")
async def mcp_tools_list():
    """Proxy GET /tools from the internal MCP server.

    Open WebUI Tool Servers discover available tools via this endpoint.
    Returns the full tool catalogue including inputSchema for each tool.
    """
    try:
        async with httpx.AsyncClient(timeout=_MCP_TIMEOUT) as c:
            r = await c.get(f"{_MCP_URL}/tools")
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse({"error": str(exc), "tools": []}, status_code=503)


@router.post("/tools/{name}/toggle")
async def mcp_tool_toggle(name: str):
    """Proxy POST /tools/{name}/toggle to enable or disable a specific tool."""
    try:
        async with httpx.AsyncClient(timeout=_MCP_TIMEOUT) as c:
            r = await c.post(f"{_MCP_URL}/tools/{name}/toggle")
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@router.post("/invoke")
async def mcp_invoke(body: _InvokeRequest):
    """Proxy POST /invoke to the internal MCP server.

    Open WebUI calls this to execute a specific tool.
    Body: {"tool": "calculate", "args": {"expression": "7*6"}}
    """
    try:
        async with httpx.AsyncClient(timeout=_MCP_TIMEOUT) as c:
            r = await c.post(f"{_MCP_URL}/invoke",
                             json={"tool": body.tool, "args": body.args})
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@router.get("/health")
async def health_check():
    """Liveness probe for Docker HEALTHCHECK and load balancers."""
    return {"status": "ok"}


async def _readiness_checks() -> tuple[dict, bool]:
    """Probe request-critical state plus degradable optional dependencies."""
    checks: dict[str, dict[str, Any]] = {}

    def record(name: str, ok: bool, critical: bool, detail: str = "") -> None:
        checks[name] = {
            "ok": ok,
            "critical": critical,
            **({"detail": detail[:300]} if detail else {}),
        }

    record(
        "orchestration_graph",
        state.app_graph is not None,
        True,
        "" if state.app_graph is not None else "LangGraph is not compiled",
    )

    try:
        contract_count = validate_boundary_contracts(force_reload=True)
        record("boundary_contracts", contract_count > 0, True)
    except Exception as exc:
        record("boundary_contracts", False, True, str(exc))

    if state.redis_client is None:
        record("valkey", False, True, "client is not initialized")
    else:
        try:
            await asyncio.wait_for(state.redis_client.ping(), timeout=2.0)
            record("valkey", True, True)
        except Exception as exc:
            record("valkey", False, True, str(exc))

    if state._userdb_pool is None:
        record("user_database", False, True, "connection pool is not initialized")
    else:
        try:
            async def _postgres_ping() -> None:
                async with state._userdb_pool.connection() as conn:
                    await conn.execute("SELECT 1")

            await asyncio.wait_for(_postgres_ping(), timeout=3.0)
            record("user_database", True, True)
        except Exception as exc:
            record("user_database", False, True, str(exc))

    if state.graph_manager is None:
        record("neo4j", False, False, "GraphRAG manager is unavailable")
    else:
        try:
            await asyncio.wait_for(
                state.graph_manager.driver.verify_connectivity(), timeout=3.0
            )
            record("neo4j", True, False)
        except Exception as exc:
            record("neo4j", False, False, str(exc))

    try:
        client = state.http_client
        if client is None:
            async with httpx.AsyncClient(timeout=3.0) as temporary:
                response = await temporary.get(f"{_MCP_URL}/tools")
        else:
            response = await asyncio.wait_for(
                client.get(f"{_MCP_URL}/tools"), timeout=3.0
            )
        response.raise_for_status()
        record("mcp_precision", True, False)
    except Exception as exc:
        record("mcp_precision", False, False, str(exc))

    record(
        "chroma_cache",
        state.cache_collection is not None,
        False,
        "" if state.cache_collection is not None else "cache collection is unavailable",
    )

    critical_ready = all(
        check["ok"] for check in checks.values() if check["critical"]
    )
    return checks, critical_ready


@router.get("/ready")
async def readiness_check():
    """Deep readiness: 503 for broken request-critical dependencies.

    Optional GraphRAG, MCP and Chroma failures are reported as ``degraded`` but
    do not remove the orchestrator from service because the pipeline has
    explicit fallbacks for those components.
    """
    checks, critical_ready = await _readiness_checks()
    optional_ready = all(
        check["ok"] for check in checks.values() if not check["critical"]
    )
    status = "ready" if critical_ready and optional_ready else (
        "degraded" if critical_ready else "not_ready"
    )
    return JSONResponse(
        status_code=200 if critical_ready else 503,
        content={"status": status, "checks": checks},
    )


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint — returns all moe_* metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/v1/provider-status")
async def provider_status():
    """Rate-limit state of all cached provider endpoints (Claude Code integration)."""
    now = time.time()
    return {ep: {**data, "now": now} for ep, data in state._provider_rate_limits.items()}

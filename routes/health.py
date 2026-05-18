"""routes/health.py — Liveness, metrics, observability, and MCP tool-server proxy."""

import os
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel

import state

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


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint — returns all moe_* metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/v1/provider-status")
async def provider_status():
    """Rate-limit state of all cached provider endpoints (Claude Code integration)."""
    now = time.time()
    return {ep: {**data, "now": now} for ep, data in state._provider_rate_limits.items()}

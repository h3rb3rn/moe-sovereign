"""routes/codex_proxy.py — Transparent reverse proxy to moe-codex.

Forwards all /v1/codex/* requests to the moe-codex service (CODEX_URL).
This makes moe-codex endpoints accessible through the moe-sovereign API
without requiring clients to know the separate codex port/host.

When CODEX_URL is not configured or moe-codex is unreachable, returns 503.
"""
from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

CODEX_URL     = os.getenv("CODEX_URL", "").rstrip("/")
CODEX_TIMEOUT = float(os.getenv("CODEX_TIMEOUT", "60"))

# Headers that must not be forwarded (connection-specific, hop-by-hop)
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "host",  # replaced by the proxied host
})

router = APIRouter()


def _is_configured() -> bool:
    return bool(CODEX_URL)


def _forward_headers(request: Request) -> dict[str, str]:
    return {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }


@router.api_route(
    "/v1/codex/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def codex_proxy(path: str, request: Request):
    """Transparent proxy: forwards to CODEX_URL/v1/codex/{path}."""
    if not _is_configured():
        return JSONResponse(
            status_code=503,
            content={
                "error": "moe-codex is not configured (set CODEX_URL to enable)",
                "docs":  "https://docs.moe-sovereign.org/codex",
            },
        )

    target = f"{CODEX_URL}/v1/codex/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = _forward_headers(request)
    body    = await request.body()

    try:
        async with httpx.AsyncClient(timeout=CODEX_TIMEOUT) as client:
            upstream = await client.request(
                method  = request.method,
                url     = target,
                headers = headers,
                content = body,
            )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"error": f"moe-codex unreachable at {CODEX_URL}"},
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"error": "moe-codex request timed out"},
        )
    except Exception as exc:
        logger.warning("codex proxy error: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"error": f"codex proxy error: {exc}"},
        )

    # Strip hop-by-hop headers from the upstream response
    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    return StreamingResponse(
        content          = _iter_bytes(upstream.content),
        status_code      = upstream.status_code,
        headers          = response_headers,
        media_type       = upstream.headers.get("content-type", "application/json"),
    )


async def _iter_bytes(content: bytes) -> AsyncIterator[bytes]:
    yield content

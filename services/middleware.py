"""services/middleware.py — Starlette HTTP middleware for the orchestrator app.

Extracted from main.py (kept it lean): security headers, a request-body size
limit (payload-DoS guard), and body caching for session fingerprinting. The
classes are wired in main.py via ``app.add_middleware`` in the original order
(security → body-size → body-cache), which Starlette executes in reverse.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse

from config import MAX_REQUEST_BODY_MB

_MAX_BODY_BYTES = MAX_REQUEST_BODY_MB * 1024 * 1024
_BODY_CACHE_MAX = 256 * 1024  # only cache bodies ≤ 256 KB to bound memory overhead


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds standard hardening headers to every response."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]     = "geolocation=(), camera=(), microphone=()"
        # HSTS only when behind TLS (the reverse proxy sets it on the public endpoint).
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized request bodies (413) before they are read into memory."""

    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": f"Request body exceeds {_MAX_BODY_BYTES // (1024 * 1024)} MB limit"},
            )
        return await call_next(request)


class BodyCacheMiddleware(BaseHTTPMiddleware):
    """Caches small request bodies on request.state so downstream handlers can
    fingerprint sessions for clients that do not send session headers."""

    async def dispatch(self, request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            cl = request.headers.get("content-length")
            if not cl or int(cl) <= _BODY_CACHE_MAX:
                body = await request.body()
                request.state._body = body
                # Do NOT replace request._receive. Starlette's _CachedRequest caches
                # the body in self._body after body() is called; wrapped_receive()
                # replays it from _body without touching _receive.
        return await call_next(request)

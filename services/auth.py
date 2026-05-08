"""
services/auth.py — API key and OIDC token validation.

Single source of truth for all authentication logic. Used by:
  - routes/* handlers (direct import, no lazy trick needed)
  - main.py (chat completions, rate limiting, usage tracking)

JWKS cache (_jwks_cache, _cache_lock) is module-level state here —
it is purely auth-scoped and does not belong in state.py.
"""

import hashlib
import json
import logging
import threading
import time
from typing import Optional

import httpx
from fastapi import Request

import state
from config import OIDC_ENABLED, OIDC_JWKS_URL, OIDC_CLIENT_ID, OIDC_ISSUER
from metrics import PROM_BUDGET_EXCEEDED

logger = logging.getLogger("MOE-SOVEREIGN")

# JWKS cache: (keys_dict, fetched_at_monotonic)
_jwks_cache: tuple = (None, 0.0)
_cache_lock  = threading.Lock()


# ---------------------------------------------------------------------------
# DB fallback key lookup (also used by integration tests directly)
# ---------------------------------------------------------------------------

async def _db_fallback_key_lookup(key_hash: str) -> Optional[dict]:
    """Fallback: validate API key directly from Postgres on Valkey cache miss."""
    try:
        if state._userdb_pool is None:
            return None
        import psycopg  # noqa — only needed at call time
        from psycopg.rows import dict_row
        async with state._userdb_pool.connection() as conn:
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
        try:
            from admin_ui.database import sync_user_to_redis as _sync
            await _sync(user_id)
        except Exception as _sync_err:
            logger.warning("sync_user_to_redis failed for user %s: %s", user_id, _sync_err)
        if state.redis_client:
            data = await state.redis_client.hgetall(f"user:apikey:{key_hash}")
            if data and data.get("is_active") == "1":
                return data
        return None
    except Exception as e:
        logger.warning("DB fallback auth error: %s", e)
        return None


# ---------------------------------------------------------------------------
# JWKS + OIDC
# ---------------------------------------------------------------------------

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
    return keys


async def _validate_oidc_token(token: str) -> Optional[dict]:
    """Validate an OIDC JWT from Authentik and return user context dict."""
    if not OIDC_ENABLED:
        return None
    try:
        import jwt as _jwt
        jwks = await _fetch_jwks()
        if not jwks:
            return None
        header = _jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid or kid is None:
                key = _jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                break
        if key is None:
            return None
        payload = _jwt.decode(
            token, key=key, algorithms=["RS256"],
            audience=OIDC_CLIENT_ID, issuer=OIDC_ISSUER,
        )
        username = payload.get("preferred_username") or payload.get("sub", "")
        email    = payload.get("email", "")
        groups   = payload.get("groups", [])
        is_admin = "moe-admins" in groups
        try:
            if state._userdb_pool is not None:
                from psycopg.rows import dict_row
                async with state._userdb_pool.connection() as conn:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute(
                            "SELECT * FROM users WHERE username=%s OR email=%s LIMIT 1",
                            (username, email),
                        )
                        local = await cur.fetchone()
                if local:
                    return {
                        "user_id":        local["id"],
                        "username":       local["username"],
                        "is_admin":       bool(local.get("is_admin", is_admin)),
                        "budget_daily":   None,
                        "budget_monthly": None,
                        "is_active":      "1",
                        "auth_method":    "oidc",
                    }
        except Exception:
            pass
        return {
            "user_id":        hashlib.sha256(
                f"oidc:{payload.get('sub', username)}".encode()
            ).hexdigest()[:32],
            "username":       username,
            "is_admin":       is_admin,
            "budget_daily":   None,
            "budget_monthly": None,
            "is_active":      "1",
            "auth_method":    "oidc",
        }
    except Exception as e:
        logger.debug("OIDC token validation failed: %s", e)
        return None


async def _validate_api_key(raw_key: str) -> Optional[dict]:
    """Validate API key or OIDC JWT. Returns user-dict or {"error": "..."}."""
    if not raw_key:
        return {"error": "invalid_key"}
    if OIDC_ENABLED and not raw_key.startswith("moe-sk-"):
        oidc_ctx = await _validate_oidc_token(raw_key)
        if oidc_ctx:
            return oidc_ctx
        return {"error": "invalid_key"}
    if not raw_key.startswith("moe-sk-"):
        return {"error": "invalid_key"}
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    if state.redis_client is None:
        return {"error": "invalid_key"}
    try:
        data = await state.redis_client.hgetall(f"user:apikey:{key_hash}")
        if not data or data.get("is_active") != "1":
            data = await _db_fallback_key_lookup(key_hash)
            if not data:
                return {"error": "invalid_key"}
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        month = date.today().strftime("%Y-%m")
        uid   = data.get("user_id", "")
        if data.get("budget_daily"):
            used = int(await state.redis_client.get(f"user:{uid}:tokens:daily:{today}") or 0)
            if used >= int(data["budget_daily"]):
                PROM_BUDGET_EXCEEDED.labels(user_id=uid, limit_type="daily").inc()
                return {"error": "budget_exceeded", "limit_type": "daily"}
        if data.get("budget_monthly"):
            used = int(await state.redis_client.get(f"user:{uid}:tokens:monthly:{month}") or 0)
            if used >= int(data["budget_monthly"]):
                PROM_BUDGET_EXCEEDED.labels(user_id=uid, limit_type="monthly").inc()
                return {"error": "budget_exceeded", "limit_type": "monthly"}
        if data.get("budget_total"):
            used = int(await state.redis_client.get(f"user:{uid}:tokens:total") or 0)
            if used >= int(data["budget_total"]):
                PROM_BUDGET_EXCEEDED.labels(user_id=uid, limit_type="total").inc()
                return {"error": "budget_exceeded", "limit_type": "total"}
        if uid and state._userdb_pool is not None:
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
                logger.debug("Team budget check skipped: %s", _be)
        return data
    except Exception as e:
        logger.warning("Auth error: %s", e)
        return {"error": "invalid_key"}


def _extract_api_key(request: Request) -> Optional[str]:
    """Extract API key from Authorization header or x-api-key."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip() or None


def _extract_session_id(request: Request) -> Optional[str]:
    """Extract or derive a session ID for semantic memory continuity.

    Priority: explicit headers → conversation fingerprint (hash of first 3 user messages).
    Supported explicit headers: x-claude-code-session-id, x-stainless-session-id,
    x-session-id, x-conversation-id, x-request-id.
    """
    import json as _json
    import hashlib as _hashlib
    h = request.headers
    explicit = (
        h.get("x-claude-code-session-id") or h.get("x-stainless-session-id") or
        h.get("x-session-id") or h.get("x-conversation-id") or h.get("x-request-id")
    )
    if explicit:
        return explicit
    try:
        body_bytes = request.state._body if hasattr(request.state, "_body") else None
        if body_bytes:
            body = _json.loads(body_bytes)
            user_msgs = [
                str(m.get("content", ""))[:200]
                for m in body.get("messages", []) if m.get("role") == "user"
            ][:3]
            if user_msgs:
                user_id = request.state.user_id if hasattr(request.state, "user_id") else ""
                seed = user_id + "".join(user_msgs)
                fp = _hashlib.sha256(seed.encode()).hexdigest()[:24]
                return f"fp-{fp}"
    except Exception:
        pass
    return None

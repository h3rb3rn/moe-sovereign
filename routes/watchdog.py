"""routes/watchdog.py — Watchdog alerts, Starfleet features, node-status endpoints."""

import asyncio
import hmac
import json
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import starfleet_config as _starfleet
import watchdog as _watchdog
import state
from config import INFERENCE_SERVERS_LIST
from services.auth import _extract_api_key, _validate_api_key

router = APIRouter()


@router.get("/api/watchdog/alerts")
async def watchdog_alerts_endpoint(limit: int = 20):
    """Return recent watchdog alerts from Valkey (most recent first)."""
    if not await _starfleet.is_feature_enabled("watchdog", state.redis_client):
        return JSONResponse({"enabled": False, "alerts": []})
    if state.redis_client is None:
        return JSONResponse({"enabled": True, "alerts": [], "error": "Valkey unavailable"})
    raw = await state.redis_client.lrange(_watchdog.VALKEY_ALERT_KEY, 0, max(0, limit - 1))
    alerts = []
    for item in raw:
        try:
            alerts.append(json.loads(item))
        except Exception:
            pass
    return {"enabled": True, "alerts": alerts}


@router.get("/api/starfleet/features")
async def starfleet_features_endpoint():
    """Return current state of all Starfleet feature toggles."""
    return await _starfleet.get_all_feature_states(state.redis_client)


async def _require_feature_admin(request: Request) -> None:
    raw_key = _extract_api_key(request)
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    system_key = os.getenv("SYSTEM_API_KEY", "")
    if bool(system_key) and hmac.compare_digest(raw_key, system_key):
        return
    user_ctx = await _validate_api_key(raw_key)
    if not user_ctx or "error" in user_ctx:
        raise HTTPException(status_code=401, detail="Invalid API key")
    is_admin = str(user_ctx.get("is_admin", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "admin",
    }
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.put("/api/starfleet/features/{name}")
async def starfleet_feature_set(name: str, request: Request):
    """Set a validated Redis runtime override for a known feature."""
    await _require_feature_admin(request)
    body = await request.json()
    if "enabled" not in body or not isinstance(body["enabled"], bool):
        raise HTTPException(status_code=400, detail="'enabled' must be a boolean")
    known = name in _starfleet.feature_names()
    previous = (
        await _starfleet.is_feature_enabled(name, state.redis_client)
        if known
        else False
    )
    if not await _starfleet.set_feature_enabled(
        name,
        body["enabled"],
        state.redis_client,
    ):
        raise HTTPException(
            status_code=404 if not known else 503,
            detail=(
                f"Unknown feature '{name}'"
                if not known
                else "Valkey unavailable"
            ),
        )
    return {
        "ok": True,
        "feature": name,
        "enabled": body["enabled"],
        "previous": previous,
        "source": "redis",
    }


@router.get("/api/watchdog/config")
async def watchdog_config_get():
    """Return the current watchdog configuration."""
    return await _watchdog._load_config(state.redis_client)


@router.post("/api/watchdog/config")
async def watchdog_config_set(request: Request):
    """Merge-update watchdog configuration (hot-reload, no restart needed)."""
    patch = await request.json()
    return await _watchdog.save_config(state.redis_client, patch)


@router.delete("/api/watchdog/alerts")
async def watchdog_alerts_clear():
    """Delete all stored watchdog alerts from Valkey."""
    if state.redis_client is not None:
        await state.redis_client.delete(_watchdog.VALKEY_ALERT_KEY)
    return {"ok": True, "cleared": True}


@router.get("/api/watchdog/node-status")
async def watchdog_node_status():
    """Return real-time per-node status via direct live health checks (3 s timeout).

    Results are cached in Valkey for 20 s so rapid dashboard refreshes don't
    hammer the inference nodes. Combines liveness from /api/tags (Ollama) or
    /models (OpenAI-compat) with VRAM data from the Prometheus gauges.
    """
    _CACHE_KEY = "moe:watchdog:node_status_cache"
    _CACHE_TTL = 20

    if state.redis_client is not None:
        try:
            cached = await state.redis_client.get(_CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    async def _check_node(srv: dict) -> dict:
        name      = srv["name"]
        url       = srv.get("url", "").rstrip("/")
        api_type  = srv.get("api_type", "ollama")
        token     = srv.get("token", "")
        vram_gb   = int(srv.get("vram_gb", 0))
        up        = False
        models_available = 0
        models_loaded    = 0
        vram_used_gb     = 0.0

        headers = {}
        if token and api_type != "ollama":
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with httpx.AsyncClient(timeout=3.0, headers=headers) as hc:
                if api_type == "ollama":
                    base = url[:-3] if url.endswith("/v1") else url
                    r = await hc.get(f"{base}/api/tags")
                    if r.status_code == 200:
                        up = True
                        models_available = len(r.json().get("models", []))
                    try:
                        rps = await hc.get(f"{base}/api/ps")
                        if rps.status_code == 200:
                            loaded = rps.json().get("models") or []
                            models_loaded = len(loaded)
                            vram_used_gb  = round(sum(
                                int(m.get("size_vram") or 0) for m in loaded
                            ) / 1e9, 1)
                    except Exception:
                        pass
                else:
                    r = await hc.get(f"{url}/models")
                    if r.status_code == 200:
                        up = True
                        models_available = len(r.json().get("data", []))
                    elif r.status_code in (401, 403) and not token:
                        up = None
        except Exception:
            pass

        vram_pct = round(vram_used_gb / vram_gb * 100, 1) if vram_gb > 0 else None
        return {
            "name":             name,
            "api_type":         api_type,
            "up":               up,
            "models_available": models_available,
            "models_loaded":    models_loaded,
            "vram_used_gb":     vram_used_gb,
            "vram_total_gb":    vram_gb,
            "vram_pct":         vram_pct,
            "source":           "admin",
        }

    results = await asyncio.gather(*[_check_node(s) for s in INFERENCE_SERVERS_LIST])
    payload  = {"nodes": list(results), "live": True, "cache_ttl_seconds": _CACHE_TTL}

    if state.redis_client is not None:
        try:
            await state.redis_client.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(payload))
        except Exception:
            pass

    return payload

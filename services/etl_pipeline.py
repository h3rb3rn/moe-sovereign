"""
services/etl_pipeline.py — NiFi ETL pipeline submission for MoE knowledge events.

Knowledge bundles and Kafka ingest events are forwarded to a configurable
NiFi ListenHTTP processor so downstream NiFi flows can fan out, transform,
and route the same data into other sinks (S3, Solr, Elastic, Snowflake, ...).
This decouples the orchestrator from the ETL routing logic — the NiFi flow
graph is owned and edited by data engineers without touching MoE code.

## Endpoint shape
We POST JSON to ``NIFI_INGEST_URL`` — typically a ``ListenHTTP`` processor
port distinct from the NiFi UI port (e.g. ``http://moe-nifi:8081/moe``).
The processor turns each request into a FlowFile whose attributes mirror
the JSON keys of ``metadata`` and whose content is the JSON-serialised
``payload``.

## Status surface
:func:`get_system_diagnostics` proxies NiFi's
``/nifi-api/system-diagnostics`` for the admin dashboard. It uses
``NIFI_URL`` + ``NIFI_ADMIN_USER`` / ``NIFI_ADMIN_PASSWORD`` to authenticate
against the standard REST API (single-user auth, Bearer-token flow).

## Graceful degradation
- Submission helpers are no-ops when ``NIFI_INGEST_URL`` is unset.
- Diagnostics returns ``None`` when ``NIFI_URL`` is unset.
- Failures are logged at DEBUG and never propagate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from config import NIFI_URL

logger = logging.getLogger("MOE-SOVEREIGN")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NIFI_INGEST_URL = os.getenv("NIFI_INGEST_URL", "")
SUBMIT_TIMEOUT  = float(os.getenv("NIFI_SUBMIT_TIMEOUT", "3.0"))
DIAG_TIMEOUT    = 5.0

# NiFi single-user auth caches the bearer token until expiry; we do a token
# fetch once per process and re-fetch on 401.
_token_cache: Dict[str, Any] = {"token": None, "fetched_at": 0.0}


# ---------------------------------------------------------------------------
# Submission (write side)
# ---------------------------------------------------------------------------

def _submit_enabled() -> bool:
    return bool(NIFI_INGEST_URL)


async def submit_to_pipeline(payload: Dict[str, Any], *,
                             source: str,
                             metadata: Optional[Dict[str, str]] = None) -> bool:
    """POST a knowledge event to the NiFi ListenHTTP endpoint.

    Returns True on HTTP 2xx, False otherwise (or when disabled).
    Never raises — designed for use in fire-and-forget background tasks.
    """
    if not _submit_enabled():
        return False
    headers: Dict[str, str] = {
        "Content-Type":     "application/json",
        "X-MoE-Source":     source,
        "X-MoE-Submitted":  datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        for k, v in metadata.items():
            # NiFi attribute names: alnum + underscore + dash, prefix with x-moe-
            safe_k = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(k))
            headers[f"X-MoE-{safe_k[:48]}"] = str(v)[:256]
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        async with httpx.AsyncClient(timeout=SUBMIT_TIMEOUT) as client:
            resp = await client.post(NIFI_INGEST_URL, content=body, headers=headers)
            return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.debug("NiFi submit failed: %s", exc)
        return False


def submit_to_pipeline_background(payload: Dict[str, Any], *,
                                  source: str,
                                  metadata: Optional[Dict[str, str]] = None) -> None:
    """Schedule submission on the running event loop without blocking."""
    if not _submit_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(submit_to_pipeline(payload, source=source, metadata=metadata))


# ---------------------------------------------------------------------------
# Status / diagnostics (read side)
# ---------------------------------------------------------------------------

async def _get_token() -> Optional[str]:
    """Acquire a NiFi access token via single-user auth. Cached per-process."""
    cached = _token_cache.get("token")
    if cached:
        return cached
    user = os.getenv("NIFI_ADMIN_USER", "")
    pwd  = os.getenv("NIFI_ADMIN_PASSWORD", "")
    if not (NIFI_URL and user and pwd):
        return None
    try:
        async with httpx.AsyncClient(timeout=DIAG_TIMEOUT, verify=False) as client:
            resp = await client.post(
                f"{NIFI_URL.rstrip('/')}/nifi-api/access/token",
                data={"username": user, "password": pwd},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 201:
                token = resp.text.strip()
                _token_cache["token"] = token
                return token
    except Exception as exc:
        logger.debug("NiFi token fetch failed: %s", exc)
    return None


async def get_system_diagnostics() -> Optional[Dict[str, Any]]:
    """Fetch /nifi-api/system-diagnostics. Returns None when unavailable."""
    if not NIFI_URL:
        return None
    token = await _get_token()
    if token is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=DIAG_TIMEOUT, verify=False) as client:
            resp = await client.get(
                f"{NIFI_URL.rstrip('/')}/nifi-api/system-diagnostics",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401:
                _token_cache["token"] = None  # invalidate, retry next call
                return None
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as exc:
        logger.debug("NiFi diagnostics failed: %s", exc)
        return None


def summarise_diagnostics(diag: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Distil the raw NiFi diagnostics payload into a UI-friendly subset."""
    if not diag:
        return {"available": False}
    snap = (diag.get("systemDiagnostics") or {}).get("aggregateSnapshot") or {}
    return {
        "available":          True,
        "uptime":             snap.get("uptime"),
        "free_heap":          snap.get("freeHeap"),
        "max_heap":           snap.get("maxHeap"),
        "used_heap_pct":      snap.get("heapUtilization"),
        "available_processors": snap.get("availableProcessors"),
        "thread_count":       snap.get("totalThreads"),
        "version":            snap.get("versionInfo", {}).get("niFiVersion"),
    }

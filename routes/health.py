"""routes/health.py — Liveness, metrics, and observability endpoints."""

import time

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

import state

router = APIRouter()


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

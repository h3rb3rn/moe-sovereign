"""routes/health.py — Liveness, metrics, and observability endpoints."""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe for Docker HEALTHCHECK and load balancers."""
    return {"status": "ok"}


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint — returns all moe_* metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

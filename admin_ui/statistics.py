"""Aggregation helpers for the /statistics admin page.

Pulls from:
- Prometheus (live gauges + range queries)
- Orchestrator's /v1/admin/knowledge-stats (Neo4j snapshot)
- Valkey cache (healer run history: moe:maintenance:ontology:runs)
- Gap-healer snapshot files (/opt/moe-infra/gap-healer-stats/*.json) if mounted

All functions are safe to call — they swallow upstream errors and return
partial data with an ``errors`` list so the UI can still render.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import httpx


PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://moe-prometheus:9090")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://langgraph-orchestrator:8000")
HEALER_SNAPSHOTS_DIR = Path(os.environ.get(
    "GAP_HEALER_STATS_DIR", "/app/gap-healer-stats",
))
ONTOLOGY_RUNS_HISTORY_KEY = "moe:maintenance:ontology:runs"


async def _prom_query(client: httpx.AsyncClient, expr: str) -> Optional[float]:
    try:
        r = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": expr}, timeout=5.0,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            return None
        result = data["data"]["result"]
        if not result:
            return None
        value = result[0]["value"][1]
        return float(value)
    except Exception:
        return None


async def _prom_query_grouped(client: httpx.AsyncClient, expr: str, label: str) -> list[dict]:
    try:
        r = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": expr}, timeout=5.0,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            return []
        out = []
        for row in data["data"]["result"]:
            lbl = row.get("metric", {}).get(label)
            val = float(row["value"][1]) if row.get("value") else 0.0
            if lbl:
                out.append({label: lbl, "value": val})
        return out
    except Exception:
        return []


async def get_live_kpis() -> dict:
    """Aggregate KPIs from the orchestrator's moe_* metrics + node-exporter.

    Ollama 0.20.x has no native /metrics endpoint; we rely on the orchestrator's
    already-instrumented counters (moe_inference_server_*, moe_requests_*,
    moe_response_duration_seconds_*) which are scraped by Prometheus.
    """
    async with httpx.AsyncClient() as client:
        req_rate = await _prom_query(client, 'sum(rate(moe_requests_total[2m]))')
        loaded = await _prom_query(client, 'sum(moe_inference_server_loaded_models)')
        inprog = await _prom_query(client, 'sum(moe_active_requests)')
        cpu_avg = await _prom_query(
            client,
            '100 - avg(rate(node_cpu_seconds_total{job="inference-nodes", mode="idle"}[2m])) * 100',
        )
        p95 = await _prom_query(
            client,
            'histogram_quantile(0.95, sum by (le) '
            '(rate(moe_response_duration_seconds_bucket[5m])))',
        )
        # Latency per mode (the duration histogram is labeled by mode, not node)
        latency_by_node = await _prom_query_grouped(
            client,
            'histogram_quantile(0.95, sum by (le, mode) '
            '(rate(moe_response_duration_seconds_bucket[5m])))',
            "mode",
        )
        latency_by_node = [{"node": r["mode"], "value": r["value"]} for r in latency_by_node]
    return {
        "request_rate": req_rate or 0.0,
        "loaded_models": loaded or 0.0,
        "in_progress": inprog or 0.0,
        "cpu_avg_pct": cpu_avg or 0.0,
        "p95_latency_s": p95 or 0.0,
        "latency_by_node": latency_by_node,
    }


async def get_knowledge_snapshot() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{ORCHESTRATOR_URL.rstrip('/')}/v1/admin/knowledge-stats")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e)[:200]}


async def get_healer_history(limit: int = 50) -> dict:
    """Valkey: last N runs; plus older snapshots from disk if mounted."""
    runs: list[dict] = []
    redis_cli = await _get_redis()
    if redis_cli is not None:
        try:
            items = await redis_cli.lrange(ONTOLOGY_RUNS_HISTORY_KEY, 0, limit - 1)
            for raw in items:
                try:
                    runs.append(json.loads(raw))
                except Exception:
                    pass
        except Exception:
            pass

    # Fall back / augment with disk snapshots
    snapshots: list[dict] = []
    if HEALER_SNAPSHOTS_DIR.exists():
        try:
            for p in sorted(HEALER_SNAPSHOTS_DIR.glob("snap_*.json"), reverse=True)[:limit]:
                try:
                    snapshots.append(json.loads(p.read_text()))
                except Exception:
                    pass
        except Exception:
            pass

    return {"runs": runs, "snapshots": snapshots}


async def get_template_activity(window: str = "7d") -> dict:
    """Prometheus increase over window — which templates are actually used."""
    window_map = {"1d": "1d", "7d": "7d", "30d": "30d"}
    w = window_map.get(window, "7d")
    async with httpx.AsyncClient() as client:
        rows = await _prom_query_grouped(
            client,
            f'sum by (node) (increase(moe_expert_calls_total[{w}]))',
            "node",
        )
        # UI still uses "template" key in the response — rename for compat
        rows = [{"template": r["node"], "value": r["value"]} for r in rows]
    rows.sort(key=lambda r: r["value"], reverse=True)
    return {"window": w, "templates": rows}


async def _get_redis():
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis.asyncio as _redis
        cli = _redis.from_url(url, decode_responses=True)
        await cli.ping()
        return cli
    except Exception:
        return None

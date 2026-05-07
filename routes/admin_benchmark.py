"""routes/admin_benchmark.py — Benchmark node reservation endpoints."""

import json
import time

from fastapi import APIRouter

import state

router = APIRouter()

_BENCHMARK_RESERVED_KEY  = "moe:benchmark_reserved"
_BENCHMARK_LOCK_META_KEY = "moe:benchmark_lock_meta"


def _read_expert_templates():
    """Proxy to main._read_expert_templates() to avoid circular import."""
    import main as _main
    return _main._read_expert_templates()


@router.post("/v1/admin/benchmark/lock")
async def benchmark_lock(body: dict = None):
    """Reserve all nodes used by a template exclusively for benchmark runs.

    While the lock is active, any request whose model/template differs from the
    reserved template receives HTTP 503. Fails open if Redis is unavailable.
    """
    body = body or {}
    template_name = (body.get("template") or "").strip()
    if not template_name:
        return {"ok": False, "reason": "template_required"}

    templates = _read_expert_templates()
    tmpl = next(
        (t for t in templates if t.get("name") == template_name or t.get("id") == template_name),
        None,
    )
    if not tmpl:
        return {"ok": False, "reason": "template_not_found", "template": template_name}

    cfg = tmpl
    nodes: set = set()
    for cat_cfg in cfg.get("experts", {}).values():
        for m in cat_cfg.get("models", []):
            ep = (m.get("endpoint") or "").strip()
            if ep:
                nodes.add(ep)
    for key in ("planner_endpoint", "judge_endpoint"):
        ep = (cfg.get(key) or "").strip()
        if ep:
            nodes.add(ep)

    if not nodes:
        return {"ok": False, "reason": "no_nodes_found_in_template"}
    if state.redis_client is None:
        return {"ok": False, "reason": "redis_unavailable"}

    await state.redis_client.delete(_BENCHMARK_RESERVED_KEY)
    await state.redis_client.sadd(_BENCHMARK_RESERVED_KEY, *nodes)
    await state.redis_client.hset(_BENCHMARK_LOCK_META_KEY, mapping={
        "template":  template_name,
        "nodes":     json.dumps(sorted(nodes)),
        "locked_at": str(time.time()),
    })
    return {"ok": True, "template": template_name, "reserved": sorted(nodes)}


@router.delete("/v1/admin/benchmark/lock")
async def benchmark_unlock():
    """Release all benchmark node reservations."""
    if state.redis_client is None:
        return {"ok": False, "reason": "redis_unavailable"}
    meta = await state.redis_client.hgetall(_BENCHMARK_LOCK_META_KEY) or {}
    try:
        released = json.loads(meta.get("nodes", "[]"))
    except Exception:
        released = []
    await state.redis_client.delete(_BENCHMARK_RESERVED_KEY)
    await state.redis_client.delete(_BENCHMARK_LOCK_META_KEY)
    return {"ok": True, "released": released}


@router.get("/v1/admin/benchmark/lock")
async def benchmark_lock_status():
    """Return the current benchmark node reservation status."""
    if state.redis_client is None:
        return {"active": False, "reason": "redis_unavailable"}
    try:
        raw = await state.redis_client.smembers(_BENCHMARK_RESERVED_KEY)
        reserved = sorted(v if isinstance(v, str) else v.decode() for v in raw)
        meta = await state.redis_client.hgetall(_BENCHMARK_LOCK_META_KEY) or {}
        if not reserved:
            return {"active": False}
        return {
            "active":    True,
            "template":  meta.get("template", ""),
            "nodes":     reserved,
            "locked_at": meta.get("locked_at", ""),
        }
    except Exception as e:
        return {"active": False, "error": str(e)[:100]}

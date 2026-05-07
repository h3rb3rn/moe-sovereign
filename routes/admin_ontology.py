"""routes/admin_ontology.py — Ontology gap-healer trigger and status endpoints.

The dedicated healer (start/stop/status/verify) remains in main.py because it
manages a subprocess with complex lifecycle state. Only the simple trigger/clear
pair lives here.
"""

import asyncio
import uuid

from fastapi import APIRouter

import state

router = APIRouter()

_ONTOLOGY_RUN_KEY = "moe:maintenance:ontology:run"


def _run_healer_task_ref():
    """Return main._run_healer_task via lazy import to avoid circular dependency.

    Python resolves the import from its module cache after the first call (O(1)),
    so there is no runtime overhead. Moving _run_healer_task out of main.py would
    require co-moving _set_healer_status and subprocess env logic — not worth it.
    """
    import main as _main
    return _main._run_healer_task


@router.post("/v1/admin/ontology/trigger")
async def trigger_ontology_healer(body: dict = None):
    """Kick off one gap-healer iteration in the background."""
    body = body or {}
    if state.redis_client is not None:
        try:
            cur = await state.redis_client.hgetall(_ONTOLOGY_RUN_KEY)
            if cur and cur.get("status") == "running":
                return {"ok": False, "reason": "already_running", "status": cur}
        except Exception:
            pass
    concurrency = max(1, min(32, int(body.get("concurrency") or 4)))
    batch_size  = max(1, min(200, int(body.get("batch_size") or 20)))
    run_id = uuid.uuid4().hex[:12]
    asyncio.create_task(_run_healer_task_ref()(concurrency, batch_size, run_id))
    return {"ok": True, "run_id": run_id}


@router.delete("/v1/admin/ontology/status")
async def clear_ontology_healer_status():
    """Delete the healer run status from Redis (dismiss failed/stale entries)."""
    if state.redis_client is None:
        return {"ok": False, "reason": "no_redis"}
    try:
        cur = await state.redis_client.hgetall(_ONTOLOGY_RUN_KEY)
        if cur and cur.get("status") == "running":
            return {"ok": False, "reason": "still_running"}
        await state.redis_client.delete(_ONTOLOGY_RUN_KEY)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:100]}

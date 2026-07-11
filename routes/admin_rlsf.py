"""routes/admin_rlsf.py — Orchestrator API routes for RLSF Loop.

Exposes trigger endpoint so that the Admin UI can launch evaluations.
"""
from fastapi import APIRouter, BackgroundTasks
from services.rlsf_local_loop import run_rlsf_loop, is_enabled

router = APIRouter()

@router.post("/v1/admin/rlsf/trigger")
async def trigger_rlsf_loop(background_tasks: BackgroundTasks, body: dict = None):
    """Trigger the RLSF Closed Loop evaluation in the background."""
    body = body or {}
    batch_size = max(1, min(1000, int(body.get("batch_size") or 100)))
    dry_run = bool(body.get("dry_run", False))
    
    background_tasks.add_task(run_rlsf_loop, batch_size=batch_size, dry_run=dry_run)
    return {"ok": True, "message": "RLSF local loop job triggered"}

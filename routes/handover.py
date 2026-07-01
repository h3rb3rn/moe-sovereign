"""
routes/handover.py — Handover Resume API (TASK-18).

Endpoints:
  GET  /handover/{handover_id}         — retrieve handover snapshot metadata
  POST /handover/{handover_id}/resume  — restore state and continue pipeline
"""

from fastapi import APIRouter, HTTPException

from services.handover import restore_handover

router = APIRouter(prefix="/handover", tags=["handover"])


@router.get("/{handover_id}")
async def get_handover(handover_id: str):
    """Return handover metadata (not the full snapshot)."""
    snapshot = restore_handover(handover_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Handover not found or expired")
    return {
        "handover_id": handover_id,
        "input":       snapshot.get("input", ""),
        "mode":        snapshot.get("mode", ""),
        "agentic_iteration": snapshot.get("agentic_iteration", 0),
        "trust_verdict":     snapshot.get("trust_verdict", ""),
    }


@router.post("/{handover_id}/resume")
async def resume_handover(handover_id: str):
    """Restore handover snapshot and return state for pipeline continuation.

    The client uses the returned state to re-submit to the main pipeline
    endpoint with pre-populated context. This endpoint only validates and
    returns the snapshot — actual pipeline re-invocation is client-side.
    """
    snapshot = restore_handover(handover_id)
    if snapshot is None:
        raise HTTPException(status_code=410, detail="Handover expired or not found")
    return {
        "handover_id": handover_id,
        "status":      "restored",
        "snapshot":    snapshot,
    }

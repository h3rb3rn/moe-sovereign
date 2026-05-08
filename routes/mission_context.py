"""routes/mission_context.py — Persistent mission context endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import starfleet_config as _starfleet
import mission_context as _mission_context
import state

router = APIRouter()


@router.get("/api/mission-context")
async def mission_context_get():
    """Return the current persistent mission context."""
    if not await _starfleet.is_feature_enabled("mission_context", state.redis_client):
        return JSONResponse({"enabled": False})
    return await _mission_context.get_context()


@router.post("/api/mission-context")
async def mission_context_set(request: Request):
    """Replace the mission context with the provided JSON body."""
    if not await _starfleet.is_feature_enabled("mission_context", state.redis_client):
        return JSONResponse({"enabled": False}, status_code=409)
    data = await request.json()
    return await _mission_context.set_context(data)


@router.patch("/api/mission-context")
async def mission_context_patch(request: Request):
    """Merge-update fields in the mission context."""
    if not await _starfleet.is_feature_enabled("mission_context", state.redis_client):
        return JSONResponse({"enabled": False}, status_code=409)
    patch = await request.json()
    return await _mission_context.patch_context(patch)

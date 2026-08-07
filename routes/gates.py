"""
routes/gates.py — Human-in-the-Loop Gate API (TASK-14).

Endpoints:
  GET  /gates                   — list gates (filtered by status)
  GET  /gates/{gate_id}         — poll gate status
  POST /gates/{gate_id}/approve — approve a pending gate
  POST /gates/{gate_id}/reject  — reject a pending gate
"""

from typing import Optional

import hmac
import os

from fastapi import APIRouter, HTTPException, Request

from services.auth import _extract_api_key, _validate_api_key
from services.hitl_gate import approve_gate, get_gate, list_gates, reject_gate

router = APIRouter(prefix="/gates", tags=["hitl-gate"])


async def _authenticated_actor(request: Request) -> dict:
    """Return a verified actor; gate routes never trust ad-hoc request.state."""
    raw_key = _extract_api_key(request)
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    user_ctx = await _validate_api_key(raw_key)
    if not user_ctx or "error" in user_ctx:
        raise HTTPException(status_code=401, detail="Invalid API key")
    system_key = os.getenv("SYSTEM_API_KEY", "")
    admin_value = user_ctx.get("is_admin")
    is_admin = (
        admin_value is True
        or str(admin_value).strip().lower() in {"1", "true", "yes", "admin"}
        or str(user_ctx.get("role", "")).strip().lower() == "admin"
    )
    return {
        "user_id": user_ctx.get("user_id", ""),
        "is_admin": is_admin,
        "is_system": bool(system_key) and hmac.compare_digest(raw_key, system_key),
    }


@router.get("")
async def list_gates_endpoint(request: Request, status: Optional[str] = None):
    """List all gates, optionally filtered by status (pending/approved/rejected/expired)."""
    actor = await _authenticated_actor(request)
    if not (actor["is_admin"] or actor["is_system"]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"gates": list_gates(status_filter=status)}


def _require_gate(gate_id: str) -> dict:
    gate = get_gate(gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="Gate not found or expired")
    return gate


def _check_authorization(gate: dict, actor: dict, action: str) -> None:
    """Only the verified owner, an admin, or the system identity may decide."""
    user_id = actor["user_id"]
    owner   = gate.get("user_id") or ""

    if actor["is_admin"] or actor["is_system"]:
        return
    if owner and user_id and user_id == owner:
        return
    raise HTTPException(status_code=403, detail=f"Not authorized to {action} this gate")


async def _commit_approved_gate(gate: dict) -> str:
    """Commit a frozen gate payload or leave the gate pending on failure."""
    commit_payload = gate.get("commit_payload") or {}
    if not commit_payload:
        return "legacy_gate"
    from services.response_commit import commit_response_payload

    commit_result = await commit_response_payload({
        **commit_payload,
        "final_response": gate.get("response_draft", ""),
    })
    if commit_result.get("status") not in {"complete", "reused", "skipped"}:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Response persistence is incomplete; retry approval",
                "commit_status": commit_result.get("status"),
                "commit_errors": commit_result.get("errors", []),
            },
        )
    return str(commit_result.get("status"))


@router.get("/{gate_id}")
async def get_gate_status(gate_id: str, request: Request):
    """Return current gate state (status, reason, response_draft)."""
    actor = await _authenticated_actor(request)
    gate = _require_gate(gate_id)
    _check_authorization(gate, actor, "view")
    # Never expose full response_draft in GET — only status and metadata
    return {
        "gate_id":    gate_id,
        "status":     gate.get("status"),
        "reason":     gate.get("reason"),
        "request_id": gate.get("request_id"),
    }


@router.post("/{gate_id}/approve")
async def approve_gate_endpoint(gate_id: str, request: Request):
    """Approve a pending gate and release the frozen response."""
    actor = await _authenticated_actor(request)
    gate = _require_gate(gate_id)
    if gate.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Gate already {gate.get('status')}")
    _check_authorization(gate, actor, "approve")

    user_id = actor["user_id"] or ("system" if actor["is_system"] else "admin")
    commit_status = await _commit_approved_gate(gate)
    ok = approve_gate(gate_id, approved_by=user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to approve gate")

    return {
        "gate_id":        gate_id,
        "status":         "approved",
        "response_draft": gate.get("response_draft", ""),
        "commit_status": commit_status,
    }


@router.post("/{gate_id}/reject")
async def reject_gate_endpoint(gate_id: str, request: Request):
    """Reject a pending gate."""
    actor = await _authenticated_actor(request)
    gate = _require_gate(gate_id)
    if gate.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Gate already {gate.get('status')}")
    _check_authorization(gate, actor, "reject")

    user_id = actor["user_id"] or ("system" if actor["is_system"] else "admin")
    ok = reject_gate(gate_id, rejected_by=user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to reject gate")

    return {"gate_id": gate_id, "status": "rejected"}

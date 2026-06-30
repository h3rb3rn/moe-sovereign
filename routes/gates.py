"""
routes/gates.py — Human-in-the-Loop Gate API (TASK-14).

Endpoints:
  GET  /gates/{gate_id}         — poll gate status
  POST /gates/{gate_id}/approve — approve a pending gate
  POST /gates/{gate_id}/reject  — reject a pending gate
"""

from fastapi import APIRouter, HTTPException, Request

from services.hitl_gate import approve_gate, get_gate, reject_gate

router = APIRouter(prefix="/gates", tags=["hitl-gate"])


def _require_gate(gate_id: str) -> dict:
    gate = get_gate(gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="Gate not found or expired")
    return gate


def _check_authorization(gate: dict, request: Request, action: str) -> None:
    """Minimal authorization: only gate owner or admin role may decide."""
    user_id = getattr(request.state, "user_id", None) or ""
    role    = getattr(request.state, "role", None) or ""
    owner   = gate.get("user_id") or ""

    if role == "admin":
        return  # Admins always allowed
    if owner and user_id and user_id == owner:
        return  # Owner may decide their own gate
    if not owner:
        return  # Anonymous gate: allow any authenticated user (development mode)
    raise HTTPException(status_code=403, detail=f"Not authorized to {action} this gate")


@router.get("/{gate_id}")
async def get_gate_status(gate_id: str):
    """Return current gate state (status, reason, response_draft)."""
    gate = _require_gate(gate_id)
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
    gate = _require_gate(gate_id)
    if gate.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Gate already {gate.get('status')}")
    _check_authorization(gate, request, "approve")

    user_id = getattr(request.state, "user_id", None) or ""
    ok = approve_gate(gate_id, approved_by=user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to approve gate")

    return {
        "gate_id":        gate_id,
        "status":         "approved",
        "response_draft": gate.get("response_draft", ""),
    }


@router.post("/{gate_id}/reject")
async def reject_gate_endpoint(gate_id: str, request: Request):
    """Reject a pending gate."""
    gate = _require_gate(gate_id)
    if gate.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Gate already {gate.get('status')}")
    _check_authorization(gate, request, "reject")

    user_id = getattr(request.state, "user_id", None) or ""
    ok = reject_gate(gate_id, rejected_by=user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to reject gate")

    return {"gate_id": gate_id, "status": "rejected"}

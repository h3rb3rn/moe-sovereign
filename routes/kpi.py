"""routes/kpi.py — KPI-Dashboard-Endpunkt (Hierarchisches Kennzahlensystem).

GET /v1/admin/kpi?window=7d
  Berechnet das vollständige KPI-Schema (KSP → KPP → KHP) aus routing_telemetry
  und gibt einen strukturierten JSON-Report zurück inkl. RPZ-Tabelle.

  Query-Parameter:
    window   "1d" | "7d" | "30d" | "90d"  — Zeitfenster (Standard: 7d)
    refresh  bool                          — Snapshot neu berechnen (Standard: False)
"""

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import state
from services.auth import _extract_api_key, _validate_api_key
from services.kpi_system import compute_kpis
from telemetry import save_kpi_snapshot, get_last_kpi_snapshot

router = APIRouter()

_VALID_WINDOWS = {"1d", "7d", "30d", "90d"}


@router.get("/v1/admin/kpi")
async def get_kpi_dashboard(
    raw_request: Request,
    window: str = "7d",
    refresh: bool = False,
) -> JSONResponse:
    """Berechnet oder liefert gecachten KPI-Report für das angegebene Zeitfenster."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    _sys_key  = os.environ.get("SYSTEM_API_KEY", "").strip()
    user_ctx  = await _validate_api_key(raw_key)
    if "error" in user_ctx and raw_key != _sys_key:
        return JSONResponse(status_code=401, content={"error": user_ctx.get("error", "Unauthorized")})

    is_admin = raw_key == _sys_key or user_ctx.get("is_admin", False)
    if not is_admin:
        return JSONResponse(status_code=403, content={"error": "Admin access required"})

    if window not in _VALID_WINDOWS:
        return JSONResponse(status_code=400, content={"error": f"Invalid window. Use: {sorted(_VALID_WINDOWS)}"})

    pool = state._userdb_pool
    if pool is None:
        return JSONResponse(status_code=503, content={"error": "Database unavailable"})

    # Gecachten Snapshot zurückgeben wenn refresh=False
    if not refresh:
        cached = await get_last_kpi_snapshot(pool, window)
        if cached:
            return JSONResponse({
                "source":      "cache",
                "computed_at": cached["computed_at"],
                "window":      window,
                **cached["snapshot"],
            })

    # Frisch berechnen
    try:
        snapshot = await compute_kpis(pool, window)
        result   = snapshot.to_dict()
        await save_kpi_snapshot(pool, window, result)
        return JSONResponse({"source": "live", **result})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})

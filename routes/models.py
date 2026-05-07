"""routes/models.py — GET /v1/models model-listing endpoint."""

import hashlib
import json
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import (
    CLAUDE_CODE_MODELS, CLAUDE_CODE_TOOL_MODEL,
    INFERENCE_SERVERS_LIST, URL_MAP, TOKEN_MAP,
)
from services.auth import _extract_api_key, _validate_api_key
from services.templates import _read_expert_templates

router = APIRouter()

# Lazy accessors for main.py constants that are not in config yet
def _modes():
    import main as _m
    return _m.MODES

def _claude_pretty_names():
    import main as _m
    return _m._CLAUDE_PRETTY_NAMES


def _model_display_name(model_id: str, description: str = "") -> str:
    """Human-readable label for Claude Desktop's model picker."""
    names = _claude_pretty_names()
    if model_id in names:
        return f"{names[model_id]} → MoE (Gateway)"
    return description or model_id


@router.get("/v1/models")
async def list_models(raw_request: Request):
    raw_key  = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "missing_key"}
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key",
            "type":    "invalid_request_error",
            "code":    "invalid_api_key",
        }})

    user_perms        = json.loads(user_ctx.get("permissions_json", "{}"))
    allowed_modes     = user_perms.get("moe_mode")
    allowed_templates = user_perms.get("expert_template")
    _user_cc_json_m   = user_ctx.get("user_cc_profiles_json", "")
    has_cc            = (bool(user_perms.get("cc_profile"))
                         or bool(_user_cc_json_m and _user_cc_json_m not in ("{}", "")))

    _model_created = int(time.time())
    MODES = _modes()

    if allowed_templates:
        all_templates = _read_expert_templates()
        main_models = [
            {
                "id":           t.get("name", t["id"]),
                "object":       "model",
                "owned_by":     "moe-sovereign",
                "created":      _model_created,
                "description":  t.get("description") or t.get("name", t["id"]),
                "display_name": _model_display_name(
                    t.get("name", t["id"]),
                    t.get("description") or t.get("name", t["id"]),
                ),
            }
            for t in all_templates if t.get("id") in allowed_templates
        ]
    else:
        _has_other_perms = bool(
            user_perms.get("model_endpoint") or user_perms.get("cc_profile")
        )
        if allowed_modes is not None or not _has_other_perms:
            main_models = [
                {
                    "id":           cfg["model_id"],
                    "object":       "model",
                    "owned_by":     "moe-sovereign",
                    "created":      _model_created,
                    "description":  cfg["description"],
                    "display_name": _model_display_name(cfg["model_id"], cfg["description"]),
                }
                for cfg in MODES.values()
                if allowed_modes is None or cfg["model_id"] in allowed_modes
            ]
        else:
            main_models = []

    existing_ids = {m["id"] for m in main_models}

    _user_tmpls_m: dict = {}
    try:
        _user_tmpls_m = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    user_tmpl_models = [
        {
            "id":           cfg.get("name", uid),
            "object":       "model",
            "owned_by":     "moe-sovereign",
            "created":      _model_created,
            "description":  cfg.get("description") or cfg.get("name", uid),
            "display_name": _model_display_name(
                cfg.get("name", uid), cfg.get("description") or cfg.get("name", uid)
            ),
        }
        for uid, cfg in _user_tmpls_m.items()
        if cfg.get("name", uid) not in existing_ids
    ]
    existing_ids |= {m["id"] for m in user_tmpl_models}

    claude_models = [
        {
            "id":           mid,
            "object":       "model",
            "owned_by":     "moe-sovereign",
            "created":      _model_created,
            "description":  f"Claude Code compatible → MoE ({CLAUDE_CODE_TOOL_MODEL} for tools)",
            "display_name": _model_display_name(mid),
        }
        for mid in sorted(CLAUDE_CODE_MODELS) if mid not in existing_ids
    ] if has_cc else []

    native_models: list = []
    _api_type_map = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}
    allowed_endpoints = user_perms.get("model_endpoint")
    if allowed_endpoints:
        seen: set = set()
        for entry in allowed_endpoints:
            model_n, _, node = entry.partition("@")
            if not model_n:
                continue
            if model_n == "*":
                if node not in URL_MAP:
                    continue
                try:
                    _api_type = _api_type_map.get(node, "ollama")
                    _wc_url   = URL_MAP[node].rstrip("/")
                    _wc_token = TOKEN_MAP.get(node, "ollama")
                    async with httpx.AsyncClient(timeout=5) as _wc_client:
                        if _api_type == "ollama":
                            _r = await _wc_client.get(
                                f"{_wc_url}/api/tags",
                                headers={"Authorization": f"Bearer {_wc_token}"},
                            )
                            _wc_models = [
                                m["name"] for m in (_r.json().get("models") or [])
                            ] if _r.status_code == 200 else []
                        else:
                            _r = await _wc_client.get(
                                f"{_wc_url}/v1/models",
                                headers={"Authorization": f"Bearer {_wc_token}"},
                            )
                            _wc_models = [
                                m["id"] for m in (_r.json().get("data") or [])
                            ] if _r.status_code == 200 else []
                    for _wc_m in _wc_models:
                        mid = f"{_wc_m}@{node}"
                        if mid not in seen and mid not in existing_ids:
                            seen.add(mid)
                            native_models.append({
                                "id": mid, "object": "model",
                                "owned_by": "moe-sovereign", "created": _model_created,
                                "description": f"Direkt via {node}",
                                "display_name": f"{_wc_m} ({node})",
                            })
                except Exception:
                    pass
                continue
            model_id = f"{model_n}@{node}" if node else model_n
            if model_id not in seen and model_id not in existing_ids:
                seen.add(model_id)
                native_models.append({
                    "id":           model_id,
                    "object":       "model",
                    "owned_by":     "moe-sovereign",
                    "created":      _model_created,
                    "description":  f"Direkt via {node}" if node else "Direktzugriff",
                    "display_name": f"{model_n} ({node})" if node else model_n,
                })

    _user_conns_m: dict = {}
    try:
        _user_conns_m = json.loads(user_ctx.get("user_connections_json", "{}") or "{}")
    except Exception:
        pass
    conn_models: list = []
    _seen_conn: set = existing_ids | {m["id"] for m in native_models}
    for _conn_name, _conn_cfg in _user_conns_m.items():
        for _cm in _conn_cfg.get("models_cache", []):
            _mid = f"{_cm}@{_conn_name}"
            if _mid not in _seen_conn:
                _seen_conn.add(_mid)
                conn_models.append({
                    "id":           _mid,
                    "object":       "model",
                    "owned_by":     "moe-sovereign",
                    "created":      _model_created,
                    "description":  f"Direkt via {_conn_name}",
                    "display_name": f"{_cm} ({_conn_name})",
                })

    return {
        "object": "list",
        "data": main_models + user_tmpl_models + claude_models + native_models + conn_models,
    }

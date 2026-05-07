"""routes/ollama_compat.py — Ollama API compatibility shim.

Translates Ollama-format requests (/api/chat, /api/generate, /api/tags, …) to
the MoE pipeline. This lets clients like Open-WebUI use the Ollama endpoint
format without knowing about the MoE backend.

Previously registered twice in main.py (duplicate block). Moving to a single
APIRouter registration eliminates the silent duplicate.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services.templates import _read_expert_templates

router = APIRouter()


# ---------------------------------------------------------------------------
# Pure helpers — no main.py deps
# ---------------------------------------------------------------------------

def _ollama_now() -> str:
    """Return current UTC time in Ollama's nanosecond ISO8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def _ollama_model_entry(tmpl: dict, *, now_iso: str) -> dict:
    """Convert an admin template dict to an Ollama /api/tags model entry."""
    return {
        "name":        tmpl.get("name", tmpl["id"]),
        "model":       tmpl.get("name", tmpl["id"]),
        "modified_at": now_iso,
        "size":        0,
        "digest":      hashlib.sha256(tmpl["id"].encode()).hexdigest(),
        "details": {
            "parent_model":       "",
            "format":             "gguf",
            "family":             "moe",
            "families":           ["moe"],
            "parameter_size":     tmpl.get("description", ""),
            "quantization_level": "MoE",
        },
    }


def _ollama_messages_to_oai(messages: list) -> list:
    """Translate Ollama messages (with optional base64 images) to OpenAI format."""
    out = []
    for m in messages:
        content = m.get("content", "")
        images  = m.get("images", [])
        if images:
            parts = [{"type": "text", "text": content}]
            for img in images:
                parts.append({
                    "type":      "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img}"},
                })
            content = parts
        out.append({"role": m.get("role", "user"), "content": content})
    return out


# ---------------------------------------------------------------------------
# Lazy auth + stream helpers (avoid circular import from main)
# ---------------------------------------------------------------------------

def _extract_api_key(request):
    import main as _m
    return _m._extract_api_key(request)


async def _validate_api_key(raw_key: str) -> dict:
    import main as _m
    return await _m._validate_api_key(raw_key)


async def _ollama_internal_stream(user_ctx, model, oai_msgs, options) -> AsyncGenerator:
    import main as _m
    async for chunk in _m._ollama_internal_stream(user_ctx, model, oai_msgs, options):
        yield chunk


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/version")
async def ollama_version():
    """Ollama version stub — clients use this to detect Ollama compatibility."""
    return {"version": "0.6.0"}


@router.get("/api/tags")
async def ollama_tags(raw_request: Request):
    """Return templates visible to this API key in Ollama model-list format."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    _ut_tags: dict = {}
    try:
        _ut_tags = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    _visible_names = {t.get("name", t["id"]) for t in visible}
    for _uid_t, _ucfg_t in _ut_tags.items():
        _m = {"id": _uid_t, **_ucfg_t}
        if _m.get("name", _uid_t) not in _visible_names:
            visible.append(_m)
    return {"models": [_ollama_model_entry(t, now_iso=now) for t in visible]}


@router.get("/api/ps")
async def ollama_ps(raw_request: Request):
    """Return templates as 'loaded' models (no real VRAM tracking in MoE)."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()
    expires    = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f000Z"
    )

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    _ut_ps: dict = {}
    try:
        _ut_ps = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    _vis_names = {t.get("name", t["id"]) for t in visible}
    for _uid_ps, _ucfg_ps in _ut_ps.items():
        _m = {"id": _uid_ps, **_ucfg_ps}
        if _m.get("name", _uid_ps) not in _vis_names:
            visible.append(_m)
    models = []
    for t in visible:
        entry = _ollama_model_entry(t, now_iso=now)
        entry["expires_at"] = expires
        entry["size_vram"]  = 0
        models.append(entry)
    return {"models": models}


@router.post("/api/show")
async def ollama_show(raw_request: Request):
    """Return template details in Ollama modelinfo format (no auth required)."""
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    model_name = body.get("model", body.get("name", ""))
    templates  = _read_expert_templates()
    tmpl = next(
        (t for t in templates if t.get("name") == model_name or t.get("id") == model_name),
        None,
    )
    if not tmpl:
        return JSONResponse(status_code=404, content={"error": f"model '{model_name}' not found"})
    return {
        "modelfile":  f"# MoE Sovereign Template: {tmpl.get('name', '')}",
        "parameters": "",
        "template":   "{{ .Prompt }}",
        "details": {
            "family":             "moe",
            "parameter_size":     tmpl.get("description", ""),
            "quantization_level": "MoE",
        },
        "model_info": {
            "general.name":        tmpl.get("name", ""),
            "general.description": tmpl.get("description", ""),
        },
    }


@router.post("/api/chat")
async def ollama_chat(raw_request: Request):
    """Ollama /api/chat — translates Ollama chat format to the MoE pipeline."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    model    = body.get("model", "")
    stream   = body.get("stream", True)
    options  = body.get("options", {})
    oai_msgs = _ollama_messages_to_oai(body.get("messages", []))

    async def _ndjson_stream():
        total_tokens = 0
        async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
            sse_line = sse_line.strip()
            if not sse_line or sse_line.startswith(":"):
                continue
            if sse_line.startswith("data: "):
                payload = sse_line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta   = (chunk.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    total_tokens += 1
                yield json.dumps({
                    "model":      model,
                    "created_at": _ollama_now(),
                    "message":    {"role": "assistant", "content": content},
                    "done":       False,
                }) + "\n"
        yield json.dumps({
            "model": model, "created_at": _ollama_now(),
            "message": {"role": "assistant", "content": ""},
            "done": True, "done_reason": "stop",
            "total_duration": 0, "eval_count": total_tokens,
        }) + "\n"

    if stream:
        return StreamingResponse(_ndjson_stream(), media_type="application/x-ndjson")

    content_parts = []
    async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
        sse_line = sse_line.strip()
        if not sse_line or sse_line.startswith(":"):
            continue
        if sse_line.startswith("data: "):
            payload = sse_line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            content_parts.append(delta.get("content", ""))
    return {
        "model": model, "created_at": _ollama_now(),
        "message": {"role": "assistant", "content": "".join(content_parts)},
        "done": True, "done_reason": "stop",
    }


@router.post("/api/generate")
async def ollama_generate(raw_request: Request):
    """Ollama /api/generate — single-turn prompt routed via MoE pipeline."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    model   = body.get("model", "")
    prompt  = body.get("prompt", "")
    system  = body.get("system", "")
    stream  = body.get("stream", True)
    options = body.get("options", {})

    oai_msgs = []
    if system:
        oai_msgs.append({"role": "system", "content": system})
    oai_msgs.append({"role": "user", "content": prompt})

    async def _gen_stream():
        total_tokens = 0
        async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
            sse_line = sse_line.strip()
            if not sse_line or sse_line.startswith(":"):
                continue
            if sse_line.startswith("data: "):
                payload = sse_line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta   = (chunk.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    total_tokens += 1
                yield json.dumps({
                    "model": model, "created_at": _ollama_now(),
                    "response": content, "done": False,
                }) + "\n"
        yield json.dumps({
            "model": model, "created_at": _ollama_now(),
            "response": "", "done": True, "done_reason": "stop",
            "total_duration": 0, "eval_count": total_tokens,
        }) + "\n"

    if stream:
        return StreamingResponse(_gen_stream(), media_type="application/x-ndjson")

    content_parts = []
    async for sse_line in _ollama_internal_stream(user_ctx, model, oai_msgs, options):
        sse_line = sse_line.strip()
        if not sse_line or sse_line.startswith(":"):
            continue
        if sse_line.startswith("data: "):
            payload = sse_line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            content_parts.append(delta.get("content", ""))
    return {
        "model": model, "created_at": _ollama_now(),
        "response": "".join(content_parts),
        "done": True, "done_reason": "stop",
    }


@router.post("/api/pull")
async def ollama_pull(raw_request: Request):
    """Fake pull-progress stream — MoE models are managed via Admin UI."""
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    if not body.get("stream", True):
        return {"status": "success"}

    async def _progress():
        for status in ["pulling manifest", "verifying sha256 digest",
                        "writing manifest", "success"]:
            yield json.dumps({"status": status}) + "\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(_progress(), media_type="application/x-ndjson")


@router.delete("/api/delete")
async def ollama_delete():
    """Model deletion is not supported — managed via Admin UI."""
    return JSONResponse(status_code=400, content={
        "error": "Model deletion is managed via Admin UI"
    })


@router.post("/api/copy")
@router.post("/api/push")
@router.post("/api/embed")
@router.post("/api/embeddings")
async def ollama_not_supported():
    """Stub for Ollama endpoints not supported by MoE Sovereign."""
    return JSONResponse(status_code=400, content={
        "error": "Not supported by MoE Sovereign"
    })

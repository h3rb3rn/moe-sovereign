"""routes/anthropic_compat.py — Anthropic Messages API and OpenAI Responses API.

/v1/messages and /v1/responses delegate to main.py handler functions via lazy import.
This avoids premature extraction of the full pipeline code which will be tackled
together with /v1/chat/completions in the final split phase.

/v1/messages/count_tokens is self-contained (auth + char count) and lives here fully.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services.auth import _extract_api_key, _validate_api_key

router = APIRouter()


# ---------------------------------------------------------------------------
# /v1/messages — Anthropic Messages API (full handler lives in main.py)
# ---------------------------------------------------------------------------

@router.post("/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic Messages API — drop-in compatible with Claude Code CLI and Anthropic SDK."""
    import main as _m
    return await _m.anthropic_messages(request)


# ---------------------------------------------------------------------------
# /v1/messages/count_tokens — token estimation (self-contained)
# ---------------------------------------------------------------------------

@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    """Token count estimation for Claude Desktop / Claude Code context budget."""
    raw_key  = _extract_api_key(request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key",
            "type":    "invalid_request_error",
            "code":    "invalid_api_key",
        }})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {
            "message": "Invalid JSON body",
            "type":    "invalid_request_error",
            "code":    "invalid_body",
        }})
    messages = body.get("messages", [])
    system   = body.get("system", "")
    char_count = sum(len(str(m.get("content", ""))) for m in messages) + len(
        str(system) if isinstance(system, str)
        else "".join(b.get("text", "") for b in system if isinstance(b, dict))
    )
    return {"input_tokens": max(1, int(char_count / 3.5))}


# ---------------------------------------------------------------------------
# /v1/responses — OpenAI Responses API (handler lives in main.py)
# ---------------------------------------------------------------------------

@router.post("/v1/responses")
async def responses_api(raw_request: Request):
    """OpenAI Responses API compatibility endpoint for Codex CLI."""
    import main as _m
    body = await raw_request.json()
    request = _m._ResponsesRequest(**body)
    return await _m.responses_api(raw_request, request)


# ---------------------------------------------------------------------------
# /v1/chat/completions — Core MoE pipeline (implementation in main.py)
#
# The 540-line handler and its ~20 supporting functions (stream_response,
# resolve_user_experts, register_active_request, etc.) remain in main.py
# until dedicated pipeline extraction in the final split phase.
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
async def chat_completions(raw_request: Request):
    """MoE Sovereign chat completions — OpenAI-compatible streaming endpoint."""
    import main as _m
    body    = await raw_request.json()
    request = _m.ChatCompletionRequest(**body)
    return await _m.chat_completions(raw_request, request)

"""routes/embeddings.py — OpenAI-compatible embeddings and legacy completions.

/v1/embeddings  — proxies to Ollama /api/embed on the first available server
/v1/completions — legacy text completions, converted to chat completions
"""

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from config import INFERENCE_SERVERS_LIST, URL_MAP, TOKEN_MAP, API_TYPE_MAP
from services.auth import _extract_api_key, _validate_api_key

logger = logging.getLogger("MOE-SOVEREIGN")

router = APIRouter()


# ---------------------------------------------------------------------------
# /v1/embeddings
# ---------------------------------------------------------------------------

class _EmbeddingRequest(BaseModel):
    input: Any                              # str | list[str] | list[int] | list[list[int]]
    model: str = "nomic-embed-text"
    encoding_format: Optional[str] = "float"
    dimensions: Optional[int] = None
    user: Optional[str] = None


def _resolve_ollama_base(node_name: str) -> Optional[str]:
    """Return base URL for Ollama native API (strips /v1 suffix if present)."""
    base = URL_MAP.get(node_name, "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base or None


async def _find_embedding_server(model_base: str) -> Optional[tuple]:
    """Find the first Ollama server that has this model loaded.

    Returns (base_url, token) or None.  Falls back to the first Ollama server
    if no per-model check is possible.
    """
    ollama_servers = [
        s for s in INFERENCE_SERVERS_LIST
        if s.get("api_type", "ollama") == "ollama"
    ]
    if not ollama_servers:
        return None

    fallback_server = ollama_servers[0]
    fallback_base   = _resolve_ollama_base(fallback_server["name"])
    fallback_token  = TOKEN_MAP.get(fallback_server["name"], "ollama")
    if not fallback_base:
        return None

    for srv in ollama_servers:
        base  = _resolve_ollama_base(srv["name"])
        token = TOKEN_MAP.get(srv["name"], "ollama")
        if not base:
            continue
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(
                    f"{base}/api/tags",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code == 200:
                    names = [m.get("name", "") for m in r.json().get("models", [])]
                    if any(model_base in n for n in names):
                        return (base, token)
        except Exception:
            continue

    return (fallback_base, fallback_token)


@router.post("/v1/embeddings")
async def create_embeddings(raw_request: Request):
    """OpenAI-compatible embeddings endpoint — proxies to Ollama /api/embed."""
    raw_key  = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key",
            "type": "invalid_request_error", "code": "invalid_api_key",
        }})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {
            "message": "Invalid JSON body", "type": "invalid_request_error",
        }})

    model_raw = body.get("model", "nomic-embed-text")
    # Strip @node suffix if present (e.g. "nomic-embed-text@N04-RTX")
    model_base, _, _node = model_raw.partition("@")
    model_base = model_base.strip()

    # If explicit node given, use it directly
    if _node and _node in URL_MAP:
        base  = _resolve_ollama_base(_node)
        token = TOKEN_MAP.get(_node, "ollama")
    else:
        srv = await _find_embedding_server(model_base)
        if not srv:
            return JSONResponse(status_code=503, content={"error": {
                "message": "No Ollama embedding server available",
                "type": "service_unavailable",
            }})
        base, token = srv

    input_data = body.get("input", [])
    # Normalize input to list
    if isinstance(input_data, str):
        inputs = [input_data]
    elif isinstance(input_data, list):
        # Each element may be str or list[int] (token IDs — pass through)
        inputs = input_data
    else:
        inputs = [str(input_data)]

    ollama_payload: dict = {
        "model":    model_base,
        "input":    inputs,
        "truncate": True,
    }
    if body.get("dimensions"):
        ollama_payload["options"] = {"embedding_length": body["dimensions"]}

    t_start = time.time()
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{base}/api/embed",
                json=ollama_payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            resp_data = r.json()
    except Exception as exc:
        logger.warning("Embedding proxy error: %s", exc)
        return JSONResponse(status_code=502, content={"error": {
            "message": f"Embedding server error: {exc}",
            "type": "server_error",
        }})

    embeddings = resp_data.get("embeddings") or []
    prompt_tokens = resp_data.get("prompt_eval_count", 0)

    # OpenAI response format
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": emb}
            for i, emb in enumerate(embeddings)
        ],
        "model":  model_base,
        "usage":  {
            "prompt_tokens": prompt_tokens,
            "total_tokens":  prompt_tokens,
        },
    }


# ---------------------------------------------------------------------------
# /v1/completions — Legacy text completions
# ---------------------------------------------------------------------------

class _CompletionRequest(BaseModel):
    model: str
    prompt: Any                             # str | list[str] | list[int]
    max_tokens: Optional[int] = 256
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stream: bool = False
    stop: Optional[Any] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    seed: Optional[int] = None
    user: Optional[str] = None
    logprobs: Optional[int] = None
    echo: Optional[bool] = False
    best_of: Optional[int] = None
    suffix: Optional[str] = None


@router.post("/v1/completions")
async def legacy_completions(raw_request: Request):
    """OpenAI legacy text completions — converts to chat completions internally."""
    raw_key  = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key",
            "type": "invalid_request_error", "code": "invalid_api_key",
        }})

    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {
            "message": "Invalid JSON body", "type": "invalid_request_error",
        }})

    model   = body.get("model", "")
    prompt  = body.get("prompt", "")
    stream  = body.get("stream", False)
    n       = body.get("n", 1)
    chat_id = f"cmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    # Normalize prompt
    if isinstance(prompt, list):
        if prompt and isinstance(prompt[0], int):
            # token IDs — best effort: can't decode without tokenizer
            prompt_text = str(prompt)
        else:
            prompt_text = "\n".join(str(p) for p in prompt)
    else:
        prompt_text = str(prompt)

    echo    = body.get("echo", False)
    suffix  = body.get("suffix", "")

    # Build a chat request and forward to the first available Ollama server
    # (or use model@node routing if provided).
    model_base, _, _node = model.partition("@")

    if _node and _node in URL_MAP:
        base  = URL_MAP[_node].rstrip("/")
        token = TOKEN_MAP.get(_node, "ollama")
        api_type = API_TYPE_MAP.get(_node, "ollama")
    else:
        # Use the primary inference server
        srv_list = INFERENCE_SERVERS_LIST
        if not srv_list:
            return JSONResponse(status_code=503, content={"error": {
                "message": "No inference servers configured", "type": "service_unavailable",
            }})
        _srv    = srv_list[0]
        base    = _srv.get("url", "").rstrip("/")
        token   = TOKEN_MAP.get(_srv["name"], _srv.get("token", "ollama"))
        api_type = _srv.get("api_type", "ollama")

    chat_url = base + "/chat/completions"

    chat_payload: dict = {
        "model":    model_base or model,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream":   stream,
        "n":        n,
    }
    if body.get("max_tokens"):
        chat_payload["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        chat_payload["temperature"] = body["temperature"]
    for _p in ("top_p", "stop", "presence_penalty", "frequency_penalty", "seed", "user"):
        if body.get(_p) is not None:
            chat_payload[_p] = body[_p]

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if not stream:
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(chat_url, json=chat_payload, headers=headers)
                r.raise_for_status()
                chat_resp = r.json()
        except Exception as exc:
            logger.warning("Legacy completions error: %s", exc)
            return JSONResponse(status_code=502, content={"error": {
                "message": f"Backend error: {exc}", "type": "server_error",
            }})

        choices = []
        for i, ch in enumerate(chat_resp.get("choices", [])):
            text = (ch.get("message") or {}).get("content") or ""
            if echo:
                text = prompt_text + text
            if suffix:
                text = text + suffix
            choices.append({
                "text":          text,
                "index":         i,
                "logprobs":      None,
                "finish_reason": ch.get("finish_reason", "stop"),
            })

        usage = chat_resp.get("usage", {})
        return {
            "id":      chat_id,
            "object":  "text_completion",
            "created": created,
            "model":   model,
            "choices": choices,
            "usage":   usage,
        }

    # Streaming path
    async def _completions_stream():
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                async with c.stream("POST", chat_url, json=chat_payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                        except Exception:
                            continue
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        text  = delta.get("content", "")
                        fr    = (chunk.get("choices") or [{}])[0].get("finish_reason")
                        cchunk = {
                            "id":      chat_id,
                            "object":  "text_completion",
                            "created": created,
                            "model":   model,
                            "choices": [{"text": text, "index": 0, "logprobs": None,
                                         "finish_reason": fr}],
                        }
                        yield f"data: {json.dumps(cchunk)}\n\n"
        except Exception as exc:
            err = {"id": chat_id, "object": "text_completion", "created": created,
                   "model": model,
                   "choices": [{"text": f"[Error: {exc}]", "index": 0,
                                "logprobs": None, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(err)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_completions_stream(), media_type="text/event-stream")

"""
services/pipeline.py — MoE LLM pipeline: chat completions, Anthropic API, Ollama streaming.

Extracted from main.py (3098 lines). All state globals use state.*; all other
main.py globals are accessed via `import main as _m` (lazy, cached by Python).

No circular import: main.py does NOT import from services.pipeline.
Only routes/anthropic_compat.py and routes/ollama_compat.py import from here.
"""

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

import state
from config import (
    KAFKA_TOPIC_INGEST, KAFKA_TOPIC_REQUESTS, KAFKA_TOPIC_FEEDBACK,
    CLAUDE_CODE_MODELS, CLAUDE_CODE_TOOL_MODEL, CLAUDE_CODE_TOOL_ENDPOINT,
    CLAUDE_CODE_MODE, CLAUDE_CODE_REASONING_MODEL, CLAUDE_CODE_REASONING_ENDPOINT,
    _CLAUDE_CODE_TOOL_URL, _CLAUDE_CODE_TOOL_TOKEN, _CLAUDE_CODE_REASONING_URL,
    JUDGE_TIMEOUT, EXPERT_TIMEOUT, PLANNER_TIMEOUT,
    JUDGE_MODEL, JUDGE_URL, JUDGE_TOKEN,
    PLANNER_MODEL, PLANNER_URL, PLANNER_TOKEN,
    URL_MAP, TOKEN_MAP, API_TYPE_MAP, INFERENCE_SERVERS_LIST,
    MODES, _MODEL_ID_TO_MODE, _CLAUDE_PRETTY_NAMES, _model_display_name,
    MAX_GRAPH_CONTEXT_CHARS, MCP_URL, GRAPH_VIA_MCP,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    CACHE_MIN_RESPONSE_LEN, ROUTE_THRESHOLD, ROUTE_GAP,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    TOOL_MAX_TOKENS, REASONING_MAX_TOKENS,
    PLANNER_RETRIES, PLANNER_MAX_TASKS, SSE_CHUNK_SIZE,
    EVAL_CACHE_FLAG_THRESHOLD, FEEDBACK_POSITIVE_THRESHOLD, FEEDBACK_NEGATIVE_THRESHOLD,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    _FALLBACK_NODE, _FALLBACK_MODEL, _FALLBACK_MODEL_SECOND,
    _FALLBACK_ENABLED, _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY, _ENDPOINT_DEGRADED_TTL,
    LITELLM_URL, _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    CORRECTION_MEMORY_ENABLED, GRAPH_INGEST_MODEL, GRAPH_INGEST_URL, GRAPH_INGEST_TOKEN,
    _CUSTOM_EXPERT_PROMPTS, THOMPSON_SAMPLING_ENABLED,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
)
from metrics import (
    PROM_TOKENS, PROM_REQUESTS, PROM_EXPERT_CALLS, PROM_CONFIDENCE,
    PROM_CACHE_HITS, PROM_CACHE_MISSES, PROM_RESPONSE_TIME,
    PROM_SELF_EVAL, PROM_COMPLEXITY,
    PROM_ACTIVE_REQUESTS, PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS,
    PROM_TOOL_FORMAT_ERRORS, PROM_TOOL_CALL_SUCCESS,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
    PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES,
    PROM_SYNTHESIS_CREATED, PROM_THOMPSON,
)
from services.auth import _validate_api_key, _extract_api_key, _extract_session_id
from services.kafka import _kafka_publish
from services.routing import (
    _resolve_user_experts, _resolve_template_prompts,
    _server_info, _is_endpoint_error,
)
from services.tracking import (
    _log_usage_to_db, _register_active_request,
    _deregister_active_request, _increment_user_budget,
    _check_ip_rate_limit,
)

# Lazy reference to main.py for globals not yet extracted to services/.
# Python caches module imports — this is O(1) after first access.
import main as _m

# ---------------------------------------------------------------------------
# Pydantic request models (defined here — used by chat_completions and routes)
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: Optional[Any] = None

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Any]
    stream: bool = False
    tools: Optional[Any] = None
    tool_choice: Optional[Any] = None
    temperature: Optional[Any] = None
    max_tokens: Optional[Any] = None
    stream_options: Optional[Any] = None
    files: Optional[Any] = None
    no_cache: bool = False
    max_agentic_rounds: Optional[Any] = None


async def chat_completions(raw_request: Request, request: ChatCompletionRequest):
    chat_id    = f"chatcmpl-{uuid.uuid4()}"
    session_id = _extract_session_id(raw_request)

    # IP-based rate limit (pre-auth, guards against credential bruteforce & DoS)
    if not await _check_ip_rate_limit(raw_request):
        return JSONResponse(status_code=429, content={"error": {
            "message": "Rate limit exceeded — too many requests from this IP",
            "type": "rate_limit_error", "code": "rate_limit_exceeded",
        }}, headers={"Retry-After": "60"})

    # Auth
    raw_key  = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        if user_ctx["error"] == "budget_exceeded":
            return JSONResponse(status_code=429, content={"error": {
                "message": f"Budget exceeded ({user_ctx.get('limit_type', 'unknown')} limit reached)",
                "type": "insufficient_quota", "code": "budget_exceeded"
            }})
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key", "type": "invalid_request_error", "code": "invalid_api_key"
        }})
    user_id      = user_ctx.get("user_id", "anon")
    api_key_id   = user_ctx.get("key_id", "")
    user_perms   = json.loads(user_ctx.get("permissions_json", "{}"))

    # Template names and MoE mode IDs take precedence over native endpoints.
    # Wildcard permissions (*@node) would otherwise intercept template names and
    # route them as direct Ollama calls (model does not exist → empty response).
    _req_raw = request.model
    _req_at = _req_raw.rindex("@") if "@" in _req_raw else -1
    _req_model_base = _req_raw[:_req_at] if _req_at >= 0 else _req_raw
    _req_node_hint  = _req_raw[_req_at + 1:] if _req_at >= 0 else None
    _all_tmpls = _m._read_expert_templates()
    _matched_tmpl = next((t for t in _all_tmpls if t.get("name") == request.model), None)
    _tmpl_override: Optional[str] = _matched_tmpl["id"] if _matched_tmpl else None
    mode = _MODEL_ID_TO_MODE.get(request.model, "default")

    # User-owned templates (stored in Valkey, not in admin DB) are invisible to the
    # admin template lookup above. When a user selects their own template, _tmpl_override
    # stays None and the native-endpoint block below incorrectly intercepts the request,
    # routing it as a direct LLM call (native mode) instead of through the MoE pipeline.
    # Fix: detect user templates early and set _tmpl_override so the pipeline is used.
    if not _tmpl_override:
        _early_user_tmpls: dict = {}
        try:
            _early_user_tmpls = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
        except Exception:
            pass
        if _early_user_tmpls:
            # Match by template name (display name) or by template ID.
            # Also try _req_model_base (the name without any "@node" suffix) because
            # Open WebUI appends "@connectionname" to model IDs when a user selects a
            # template — e.g. "nff-test@MY_ENDPOINT" → base name is "nff-test".
            for _ut_id, _ut_cfg in _early_user_tmpls.items():
                _ut_name = _ut_cfg.get("name", "")
                if (
                    _ut_name == request.model          # exact match with full model string
                    or _ut_name == _req_model_base     # match without @node suffix
                    or _ut_id  == request.model        # match by template ID
                    or _ut_id  == _req_model_base
                ):
                    _tmpl_override = _ut_id
                    logger.debug("User template detected: '%s' (req='%s') → override=%s",
                                 _ut_name or _ut_id, request.model, _ut_id)
                    break
            # Also check templates referenced in the user's expert_template permissions
            if not _tmpl_override:
                _early_perms = json.loads(user_ctx.get("permissions_json", "{}") or "{}")
                for _perm_tid in _early_perms.get("expert_template", []):
                    if _perm_tid in _early_user_tmpls:
                        _ut_cfg = _early_user_tmpls[_perm_tid]
                        _ut_name = _ut_cfg.get("name", "")
                        if (
                            _ut_name == request.model
                            or _ut_name == _req_model_base
                            or _perm_tid == request.model
                            or _perm_tid == _req_model_base
                        ):
                            _tmpl_override = _perm_tid
                            logger.debug("User template detected via permissions: %s", _perm_tid)
                            break

    # Benchmark node reservation: reject non-benchmark requests when nodes are locked.
    # Fails open if Redis is unavailable so a Redis outage never blocks all traffic.
    if state.redis_client is not None:
        try:
            _bench_reserved = await redis_client.smembers("moe:benchmark_reserved")
            if _bench_reserved:
                _bench_meta = await redis_client.hgetall("moe:benchmark_lock_meta") or {}
                _bench_tmpl = _bench_meta.get("template", "")
                if request.model != _bench_tmpl:
                    # Allow templates whose experts run exclusively on non-reserved nodes.
                    # Collect the endpoint names used by this template's experts.
                    _tmpl_nodes: set = set()
                    if _matched_tmpl:
                        for _exp in (_matched_tmpl.get("experts") or {}).values():
                            for _em in (_exp.get("models") or []):
                                _ep = _em.get("endpoint", "")
                                if _ep:
                                    _tmpl_nodes.add(_ep)
                    # Block only if the template uses reserved nodes, or has no node info
                    # (unknown template — block by default for safety).
                    if not _tmpl_nodes or _tmpl_nodes.intersection(_bench_reserved):
                        return JSONResponse(status_code=503, content={"error": {
                            "message": (
                                f"Service temporarily unavailable: inference nodes are reserved "
                                f"for benchmark run (template: {_bench_tmpl}). "
                                "Please retry after the benchmark completes."
                            ),
                            "type": "service_unavailable",
                            "code": "benchmark_lock_active",
                            "benchmark_template": _bench_tmpl,
                        }})
        except Exception:
            pass  # Fail open — never block traffic due to Redis errors

    # If the matched template sets force_think=true and no explicit mode was requested,
    # upgrade to agent_orchestrated so the thinking_node activates before routing.
    # (Resolved after _tmpl_prompts is populated below; pre-read here to avoid circular dep.)
    if _tmpl_override:
        _early_tmpl = next((t for t in _all_tmpls if t.get("id") == _tmpl_override), None)
        if _early_tmpl and _early_tmpl.get("force_think") and mode == "default":
            mode = "agent_orchestrated"

    # Native LLM? check model_endpoint permission — only if no template and no MoE mode
    # Supports "model_name" (legacy) and "model_name@node" (new, OpenWebUI format)
    _native_endpoint: Optional[dict] = None
    _user_conns_map: dict = {}
    try:
        _user_conns_map = json.loads(user_ctx.get("user_connections_json", "{}") or "{}")
    except Exception:
        pass
    if not _tmpl_override and request.model not in _MODEL_ID_TO_MODE:
        for _ep_entry in user_perms.get("model_endpoint", []):
            _ep_model, _, _ep_node = _ep_entry.partition("@")
            if _ep_node not in URL_MAP:
                continue
            if (_ep_model == _req_model_base or _ep_model == "*") and \
               (_req_node_hint is None or _req_node_hint == _ep_node):
                _native_endpoint = {
                    "url":   URL_MAP[_ep_node],
                    "token": TOKEN_MAP.get(_ep_node, "ollama"),
                    "model": _req_model_base,  # Model name only to backend, no @host suffix
                    "node":  _ep_node,
                }
                break
        # Fallback: user-owned private connections (lower priority than global URL_MAP)
        if not _native_endpoint and _user_conns_map:
            if _req_node_hint and _req_node_hint in _user_conns_map:
                _uc = _user_conns_map[_req_node_hint]
                _native_endpoint = {
                    "url":        _uc["url"],
                    "token":      _uc.get("api_key") or "ollama",
                    "model":      _req_model_base,
                    "node":       _req_node_hint,
                    "api_type":   _uc.get("api_type", "openai"),
                    "_user_conn": True,
                }
            elif not _req_node_hint:
                # Bare model name: check models_cache of each user connection
                for _cname, _uc in _user_conns_map.items():
                    if _req_model_base in _uc.get("models_cache", []):
                        _native_endpoint = {
                            "url":        _uc["url"],
                            "token":      _uc.get("api_key") or "ollama",
                            "model":      _req_model_base,
                            "node":       _cname,
                            "api_type":   _uc.get("api_type", "openai"),
                            "_user_conn": True,
                        }
                        break
    _user_tmpls_json = user_ctx.get("user_templates_json", "{}")
    # Template name match is NOT authorization — check expert_template permissions explicitly.
    # Exception: user-owned templates (present in user_templates_json) are authorized by ownership.
    if _tmpl_override:
        _allowed_tmpls = user_perms.get("expert_template", [])
        _owned_tmpls: dict = {}
        try:
            _owned_tmpls = json.loads(_user_tmpls_json or "{}")
        except Exception:
            pass
        if _tmpl_override not in _allowed_tmpls and _tmpl_override not in _owned_tmpls:
            return JSONResponse(status_code=403, content={"error": {
                "message": f"Template '{request.model}' is not authorized for this API key",
                "type": "permission_denied",
                "code": "template_not_authorized",
            }})
    _user_conns_json = user_ctx.get("user_connections_json", "{}")
    user_experts  = _resolve_user_experts(user_ctx.get("permissions_json", ""), override_tmpl_id=_tmpl_override,
                                           user_templates_json=_user_tmpls_json,
                                           admin_override=False,
                                           user_connections_json=_user_conns_json) or {}
    _tmpl_prompts = _resolve_template_prompts(user_ctx.get("permissions_json", ""), override_tmpl_id=_tmpl_override,
                                               user_templates_json=_user_tmpls_json,
                                               admin_override=False,
                                               user_connections_json=_user_conns_json)
    # Ghost-template detection: template in permissions but absent from DB and not user-owned
    if _tmpl_override and not user_experts and not user_perms.get("expert_template") == []:
        _ghost_check = next((t for t in _all_tmpls if t.get("id") == _tmpl_override), None)
        if _ghost_check is None and _tmpl_override not in _owned_tmpls:
            logger.error("Ghost template detected: %s in permissions but not in DB", _tmpl_override)
            return JSONResponse(status_code=422, content={"error": {
                "message": f"Template '{_tmpl_override}' is configured but no longer exists in the database",
                "type": "template_not_found",
                "code": "ghost_template",
            }})

    # Extract system message (coding agents send file/codebase context here)
    system_msgs   = [m for m in request.messages if m.role == "system"]
    system_prompt = _m._oai_content_to_str(system_msgs[0].content) if system_msgs else ""

    # Mission Context injection — prepend compact project summary to the system prompt
    # when enabled system-wide AND the active template does not opt out.
    if (
        _starfleet.is_feature_enabled_sync("mission_context")
        and _tmpl_prompts.get("enable_mission_context", False)
    ):
        try:
            _mc = await _mission_context.get_context()
            _mc_title = (_mc.get("title") or "").strip()
            if _mc_title:
                _mc_lines = [f"## Mission Context: {_mc_title}"]
                if _mc.get("description"):
                    _mc_lines.append(_mc["description"].strip())
                if _mc.get("open_tasks"):
                    _mc_lines.append("Open tasks: " + "; ".join(_mc["open_tasks"][:5]))
                if _mc.get("recent_decisions"):
                    last = _mc["recent_decisions"][-1]
                    _mc_lines.append(f"Last decision: {last.get('text', '')}")
                _mc_block = "\n".join(_mc_lines)
                system_prompt = f"{_mc_block}\n\n{system_prompt}" if system_prompt else _mc_block
        except Exception:
            pass

    # Last user message as the actual query
    user_msgs  = [m for m in request.messages if m.role in ("user", "assistant")]
    last_user  = next((m for m in reversed(request.messages) if m.role == "user"), None)
    _user_images = _m._extract_oai_images(last_user.content) if last_user else []
    allowed_skills = user_perms.get("skill")  # None = all allowed (backwards compatible)
    _raw_user_input = _m._oai_content_to_str(last_user.content) if last_user else ""
    user_input = await _resolve_skill_secure(_raw_user_input, allowed_skills, user_id=user_id, session_id=session_id)
    # Shadow-Mode: sample every BENCHMARK_SHADOW_RATE-th request to the candidate template.
    # Fire-and-forget — never blocks the production response.
    if BENCHMARK_SHADOW_TEMPLATE:
        with _m._shadow_lock:
            _m._shadow_request_counter += 1
            _fire_shadow = (_m._shadow_request_counter % BENCHMARK_SHADOW_RATE == 0)
        if _fire_shadow:
            asyncio.create_task(_m._shadow_request(_raw_user_input, user_id, raw_key or ""))
            logger.debug(f"🔬 Shadow request enqueued (counter={_m._shadow_request_counter})")

    _pending_reports: List[str] = []
    if user_input != _raw_user_input:
        _sm = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)", _raw_user_input)
        _sname = _sm.group(1) if _sm else "?"
        _sargs = _raw_user_input[len(_sname)+1:].strip() if _sm else ""
        _pending_reports.append(
            f"🎯 Skill /{_sname} resolved (args: '{_sargs}', {len(user_input)} chars):\n{user_input}"
        )
    else:
        # No manual skill command → auto-detect whether a known file is attached.
        # Skip file-skill detection when the message already contains an explicit routing
        # directive (e.g. from the GAIA benchmark runner or structured data injection).
        # The directive "[ROUTE TO: reasoning OR general" signals that the caller has
        # already classified the content and no automatic skill dispatch should occur.
        _routing_bypass = (
            "[ROUTE TO: reasoning OR general" in _raw_user_input
            or "[ROUTING: Use reasoning or general expert" in _raw_user_input
        )
        _auto_skill = None if _routing_bypass else _detect_file_skill(
            request.files, _raw_user_input, allowed_skills
        )
        if _auto_skill:
            _auto_input = f"/{_auto_skill} {_raw_user_input}"
            _resolved = await _resolve_skill_secure(_auto_input, allowed_skills, user_id=user_id, session_id=session_id)
            if _resolved != _auto_input:  # skill exists and was resolved
                user_input = _resolved
                _pending_reports.append(f"📎 File skill /{_auto_skill} triggered automatically")

    # Open WebUI internal requests (follow-ups, title, autocomplete) directly without pipeline
    # Skip in agent mode — coding tools do not send OpenWebUI-internal markers
    if mode != "agent" and _is_openwebui_internal(request.messages):
        return await _handle_internal_direct(request.messages, chat_id, request.stream)

    # Model availability check: does the requested model actually exist on the target node?
    # Prevents hanging requests when a model is requested via wildcard permission,
    # but is not present on the host (e.g. gemma4:31b@MY_ENDPOINT even though it is not installed).
    # Skipped for user-owned connections — their models_cache already served that check.
    if _native_endpoint and not _native_endpoint.get("_user_conn"):
        _avail_models = await _m._get_available_models(_native_endpoint["node"])
        if _avail_models is not None and _native_endpoint["model"] not in _avail_models:
            _avail_list = sorted(_avail_models)
            logger.warning(
                "Model-Not-Found: '%s' not available on %s. Available: %s",
                _native_endpoint["model"], _native_endpoint["node"], _avail_list,
            )
            return JSONResponse(
                status_code=404,
                content={"error": {
                    "message": (
                        f"Model '{_native_endpoint['model']}' is not available on "
                        f"{_native_endpoint['node']}. "
                        f"Available models: {_avail_list}"
                    ),
                    "type":  "invalid_request_error",
                    "code":  "model_not_found",
                }},
            )

    # Live monitoring: register request as active
    _tmpl_name = request.model if _tmpl_override else ""
    _req_type  = "streaming" if request.stream else "batch"
    _req_type  = "native"    if _native_endpoint else _req_type
    _client_ip = raw_request.client.host if raw_request.client else ""
    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=user_id, model=_req_model_base,
        moe_mode=("native" if _native_endpoint else mode),
        req_type=_req_type, template_name=_tmpl_name, client_ip=_client_ip,
        backend_model=_native_endpoint["model"] if _native_endpoint else "",
        backend_host=_native_endpoint["node"]  if _native_endpoint else "",
        api_key_id=api_key_id,
    ))

    # Conversation history: only user/assistant messages (no system messages)
    # Multimodal content is extracted as text (history for MoE pipeline is text-only)
    raw_history = [
        {"role": m.role, "content": _m._oai_content_to_str(m.content)}
        for m in request.messages
        if m.role in ("user", "assistant") and m != last_user
    ]
    _hist_turns = _tmpl_prompts.get("history_max_turns", 0) or None
    _hist_chars = _tmpl_prompts.get("history_max_chars", 0) or None
    history = _m._truncate_history(raw_history, max_turns=_hist_turns, max_chars=_hist_chars)
    _sm_team_ids: List[str] = []
    _sm_user_prefs: dict = {}
    if _tmpl_prompts.get("enable_semantic_memory") and user_id:
        try:
            from admin_ui.database import (
                get_user_teams        as _get_user_teams,
                get_user_memory_prefs as _get_mem_prefs,
            )
            if _tmpl_prompts.get("enable_cross_session_memory"):
                _sm_team_ids = await _get_user_teams(user_id)
            _sm_user_prefs = await _get_mem_prefs(user_id)
        except Exception:
            pass
    history = await _apply_semantic_memory(
        raw_history, history, user_input, session_id,
        enabled=_tmpl_prompts.get("enable_semantic_memory", False),
        user_id=user_id,
        team_ids=_sm_team_ids,
        cross_session_enabled=_tmpl_prompts.get("enable_cross_session_memory", False),
        cross_session_scopes=_tmpl_prompts.get("cross_session_scopes", ["private"]),
        n_results=_tmpl_prompts.get("semantic_memory_n_results", 0),
        ttl_hours=_tmpl_prompts.get("semantic_memory_ttl_hours", 0),
        prefer_fresh=_sm_user_prefs.get("prefer_fresh", False),
        share_with_team=_sm_user_prefs.get("share_with_team", False),
    )

    # Native LLM: forward directly to endpoint, no MoE pipeline
    if _native_endpoint:
        _native_is_user_conn = bool(_native_endpoint.get("_user_conn"))
        if request.stream:
            return StreamingResponse(
                _stream_native_llm(request, chat_id, _native_endpoint, user_id, request.model,
                                   session_id=session_id, is_user_conn=_native_is_user_conn),
                media_type="text/event-stream",
            )
        # Non-streaming native: blockierender httpx-Call
        async with httpx.AsyncClient(timeout=300) as _hc:
            _nr = await _hc.post(
                _native_endpoint["url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {_native_endpoint['token']}", "Content-Type": "application/json"},
                json={"model": _native_endpoint["model"],
                      "messages": [{"role": m.role, "content": m.content if m.content is not None else ""} for m in request.messages],
                      "stream": False,
                      **({"max_tokens": request.max_tokens} if request.max_tokens else {}),
                      **({"temperature": request.temperature} if request.temperature is not None else {})},
            )
        _nr.raise_for_status()
        _nj = _nr.json()
        _nu = _nj.get("usage", {})
        if user_id != "anon":
            asyncio.create_task(_log_usage_to_db(user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                model=request.model, moe_mode="native",
                prompt_tokens=_nu.get("prompt_tokens", 0), completion_tokens=_nu.get("completion_tokens", 0),
                session_id=session_id))
            # User-owned connections are billed by the user's own provider — exclude from MoE budget.
            if not _native_is_user_conn:
                asyncio.create_task(_increment_user_budget(user_id, _nu.get("total_tokens", 0), prompt_tokens=_nu.get("prompt_tokens", 0), completion_tokens=_nu.get("completion_tokens", 0)))
        asyncio.create_task(_deregister_active_request(chat_id))
        _nj["id"] = chat_id
        return _nj

    _moe_resp_headers = {}
    if _tmpl_override:
        _moe_resp_headers["X-MoE-Template-Id"]   = _tmpl_override
        _moe_resp_headers["X-MoE-Template-Name"]  = _tmpl_name or request.model

    # Expose user token budget so callers can track their quota without a separate API call.
    if user_id and user_id != "anon" and state.redis_client is not None:
        try:
            from datetime import date as _date
            _today = _date.today().strftime("%Y-%m-%d")
            _used_tok = int(await redis_client.get(f"user:{user_id}:tokens:daily:{_today}") or 0)
            _moe_resp_headers["X-MoE-Budget-Daily-Used"] = str(_used_tok)
            _daily_limit = user_ctx.get("budget_daily")
            if _daily_limit:
                _moe_resp_headers["X-MoE-Budget-Daily-Limit"] = str(_daily_limit)
        except Exception:
            pass

    if request.stream:
        return StreamingResponse(
            stream_response(user_input, chat_id, mode, chat_history=history,
                            system_prompt=system_prompt, user_id=user_id,
                            user_permissions=user_perms, user_experts=user_experts,
                            planner_prompt=_tmpl_prompts["planner_prompt"],
                            judge_prompt=_tmpl_prompts["judge_prompt"],
                            judge_model_override=_tmpl_prompts["judge_model_override"],
                            judge_url_override=_tmpl_prompts["judge_url_override"],
                            judge_token_override=_tmpl_prompts["judge_token_override"],
                            planner_model_override=_tmpl_prompts["planner_model_override"],
                            planner_url_override=_tmpl_prompts["planner_url_override"],
                            planner_token_override=_tmpl_prompts["planner_token_override"],
                            model_name=request.model,
                            pending_reports=_pending_reports,
                            images=_user_images,
                            session_id=session_id,
                            max_agentic_rounds=(
                                request.max_agentic_rounds
                                if request.max_agentic_rounds is not None
                                else _tmpl_prompts.get("max_agentic_rounds", 0)
                            ),
                            no_cache=request.no_cache),
            media_type="text/event-stream",
            headers=_moe_resp_headers or None,
        )
    result = await app_graph.ainvoke(
        {"input": user_input, "response_id": chat_id, "mode": mode,
         "user_id": user_id, "api_key_id": api_key_id,
         "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
         "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
         "chat_history": history, "reasoning_trace": "", "system_prompt": system_prompt,
         "images": _user_images,
         "user_permissions": user_perms, "user_experts": user_experts,
         # Prepend personal namespace so user-created knowledge is private by default.
         # graph_tenant permissions add team/tenant namespaces on top.
         "tenant_ids": ([f"user:{user_id}"] if user_id and user_id != "anon" else [])
                       + [t for t in user_perms.get("graph_tenant", [])
                          if t != f"user:{user_id}"],
         "provenance_sources": [],
         "output_skill_body": "",
         "enable_cache": _tmpl_prompts.get("enable_cache", True),
         "enable_graphrag": _tmpl_prompts.get("enable_graphrag", True),
         "enable_web_research": _tmpl_prompts.get("enable_web_research", True),
         "search_fallback_ddg": _tmpl_prompts.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG),
         "graphrag_max_chars": _tmpl_prompts.get("graphrag_max_chars", 0),
         "history_max_turns": _tmpl_prompts.get("history_max_turns", 0),
         "history_max_chars": _tmpl_prompts.get("history_max_chars", 0),
         "planner_prompt": _tmpl_prompts["planner_prompt"],
         "judge_prompt":   _tmpl_prompts["judge_prompt"],
         "judge_model_override":   _tmpl_prompts["judge_model_override"],
         "judge_url_override":     _tmpl_prompts["judge_url_override"],
         "judge_token_override":   _tmpl_prompts["judge_token_override"],
         "planner_model_override": _tmpl_prompts["planner_model_override"],
         "planner_url_override":   _tmpl_prompts["planner_url_override"],
         "planner_token_override": _tmpl_prompts["planner_token_override"],
         "template_name":  _tmpl_name,
         "pending_reports": _pending_reports,
         "max_agentic_rounds": _tmpl_prompts.get("max_agentic_rounds", 0),
         "agentic_iteration": 0,
         "agentic_history": [],
         "agentic_gap": "",
         "attempted_queries": [],
         "search_strategy_hint": "",
         "conflict_registry": [],
         "vector_confidence": 0.5,
         "graph_confidence": 0.5,
         "fuzzy_routing_scores": {},
         "no_cache": request.no_cache,
         # Pass explicit temperature into state so planner, judge and experts
         # can honour it. None means "use query-adaptive detection".
         "query_temperature": request.temperature,
         },
        {"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    p_tok = result.get("prompt_tokens",     0)
    c_tok = result.get("completion_tokens", 0)
    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=MODES.get(mode, MODES["default"])["model_id"],
            moe_mode=mode, prompt_tokens=p_tok, completion_tokens=c_tok,
            session_id=session_id,
        ))
        _uc_p = result.get("user_conn_prompt_tokens", 0)
        _uc_c = result.get("user_conn_completion_tokens", 0)
        _bill_p = max(0, p_tok - _uc_p)
        _bill_c = max(0, c_tok - _uc_c)
        asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))
    asyncio.create_task(_deregister_active_request(chat_id))
    resp = {
        "id":      chat_id,
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   MODES.get(mode, MODES["default"])["model_id"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result["final_response"]}, "finish_reason": "stop"}],
        "usage":   {
            "prompt_tokens":     p_tok,
            "completion_tokens": c_tok,
            "total_tokens":      p_tok + c_tok,
        },
    }
    # Add provenance metadata if available (non-standard, backward-compatible)
    _prov = result.get("provenance_sources")
    if _prov:
        resp["metadata"] = {"sources": _prov}
    if _moe_resp_headers:
        return JSONResponse(content=resp, headers=_moe_resp_headers)
    return resp

# ============================================================
#  ANTHROPIC MESSAGES API  (/v1/messages)
#  Enables Claude Code CLI and other Anthropic API clients
#  to use the MoE Orchestrator.
#
#  Routing:
#    - Requests WITH tools / tool_results → judge_llm (magistral:24b via Ollama)
#      with OpenAI→Anthropic format conversion
#    - Pure text requests                 → MoE Agent-Pipeline (mode="agent")
# ============================================================

# _anthropic_content_to_text, _extract_images, _extract_oai_images,
# _anthropic_to_openai_messages, _anthropic_tools_to_openai — see parsing.py


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _anthropic_content_blocks_to_sse(
    content_blocks: list, chat_id: str, model_id: str,
    input_tokens: int, output_tokens: int, stop_reason: str
):
    """Emit finished content blocks as Anthropic SSE stream."""
    yield _sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [], "model": model_id,
            "stop_reason": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1}
        }
    })
    for idx, block in enumerate(content_blocks):
        if block["type"] == "text":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "text", "text": ""}
            })
            if idx == 0:
                yield _sse_event("ping", {"type": "ping"})
            text = block.get("text", "")
            for i in range(0, max(len(text), 1), SSE_CHUNK_SIZE):
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "text_delta", "text": text[i:i+SSE_CHUNK_SIZE]}
                })
                await asyncio.sleep(0.005)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block["type"] == "thinking":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "thinking", "thinking": ""}
            })
            if idx == 0:
                yield _sse_event("ping", {"type": "ping"})
            thinking_text = block.get("thinking", "")
            for i in range(0, max(len(thinking_text), 1), SSE_CHUNK_SIZE):
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "thinking_delta", "thinking": thinking_text[i:i+SSE_CHUNK_SIZE]}
                })
                await asyncio.sleep(0.005)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block["type"] == "tool_use":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {
                    "type": "tool_use", "id": block["id"],
                    "name": block["name"], "input": {}
                }
            })
            args_json = json.dumps(block.get("input", {}))
            yield _sse_event("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": args_json}
            })
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
    yield _sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens}
    })
    yield _sse_event("message_stop", {"type": "message_stop"})


async def _anthropic_tool_handler(body: dict, chat_id: str, tool_model: Optional[str] = None, tool_url: Optional[str] = None, tool_token: Optional[str] = None, tool_timeout: Optional[int] = None, tool_node: Optional[str] = None, user_id: str = "anon", api_key_id: str = "", session_id: str = None, is_user_conn: bool = False):
    """Forwards tool-capable requests to an inference server and converts formats.

    tool_model/tool_url/tool_token: override default judge if specified (e.g. for Claude Code sessions).
    is_user_conn: when True the tool endpoint is a user-owned connection — tokens are NOT charged to the MoE budget.
    tool_timeout: per-node timeout in seconds (fallback: JUDGE_TIMEOUT).
    tool_node: node name for Prometheus labels (e.g. "MY-NODE").
    """
    model_id   = body.get("model", "moe-orchestrator-agent")
    messages   = body.get("messages", [])
    system     = body.get("system") or ""
    # Prepend system-prompt prefix from the active Claude Code profile
    if _CC_SYSTEM_PREFIX and system:
        system = f"{_CC_SYSTEM_PREFIX}\n\n{system}"
    elif _CC_SYSTEM_PREFIX:
        system = _CC_SYSTEM_PREFIX
    tools      = body.get("tools", [])
    do_stream  = body.get("stream", False)
    max_tokens = body.get("max_tokens", TOOL_MAX_TOKENS)

    # Eval timing + input classification
    _eval_t0 = time.monotonic()
    _last_msg = messages[-1] if messages else {}
    _last_content = _last_msg.get("content", "")
    _last_msg_type = (
        "tool_result" if (
            isinstance(_last_content, list)
            and any(b.get("type") == "tool_result" for b in _last_content)
        )
        else "tool_use_request" if tools
        else "text"
    )

    oai_messages = _m._anthropic_to_openai_messages(messages, system)
    oai_tools    = _m._anthropic_tools_to_openai(tools) if tools else None

    effective_model = tool_model or JUDGE_MODEL
    effective_url   = tool_url   or JUDGE_URL
    effective_token = tool_token or JUDGE_TOKEN
    effective_node  = tool_node or "unknown"
    _node_timeout   = float(tool_timeout if tool_timeout is not None else JUDGE_TIMEOUT)

    # Guard: empty model name causes inference servers to return HTTP 400.
    # Happens when CLAUDE_CODE_TOOL_MODEL is unset and no CC profile overrides it.
    if not effective_model or not effective_model.strip():
        _no_model_err = (
            "⚠️ No tool model configured. Set CLAUDE_CODE_TOOL_MODEL in .env "
            "or configure a CC profile with a tool_model."
        )
        logger.warning("⚠️ _anthropic_tool_handler: effective_model is empty — returning config error")
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _no_model_err}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _no_model_err}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }

    payload: dict = {
        "model":      effective_model,
        "messages":   oai_messages,
        "stream":     False,        # collect tool calls completely, then convert
        "max_tokens": max_tokens,
    }
    if oai_tools:
        payload["tools"] = oai_tools
        # Guard: if the last message is a tool_result (synthesis turn), don't force tool_use
        _has_tool_results = any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "tool_result" for b in m.get("content", []))
            for m in messages
        )
        _effective_tool_choice = (
            "auto" if (_has_tool_results and not tools) else CLAUDE_CODE_TOOL_CHOICE
        )
        payload["tool_choice"] = _effective_tool_choice
    else:
        _effective_tool_choice = "auto"

    # Pre-check: if this endpoint is known to be rate-limited, fail fast instead of timing out.
    # This prevents Claude Code CLI from making 10 retry attempts and risking a DDoS ban.
    _tool_ep = CLAUDE_CODE_TOOL_ENDPOINT if (effective_url == _CLAUDE_CODE_TOOL_URL) else None
    if _tool_ep and _m._check_rate_limit_exhausted(_tool_ep):
        _rl_entry = _provider_rate_limits.get(_tool_ep, {})
        _reset_str = ""
        if _rl_entry.get("reset_time"):
            import datetime as _dt
            _reset_str = f" Reset: {_dt.datetime.fromtimestamp(_rl_entry['reset_time']).strftime('%H:%M:%S')}."
        _err_body = {"type": "error", "error": {"type": "overloaded_error",
            "message": f"Provider token limit exhausted.{_reset_str} Remaining: {_rl_entry.get('remaining_tokens', 0)}"}}
        asyncio.create_task(_deregister_active_request(chat_id))
        if do_stream:
            async def _rl_err_stream():
                yield f"data: {json.dumps(_err_body)}\n\n"
            return StreamingResponse(_rl_err_stream(), media_type="text/event-stream", status_code=529)
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(content=_err_body, status_code=529)

    _llm_t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_node_timeout) as client:
            resp = await client.post(
                f"{effective_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {effective_token}"}
            )
            # Parse and cache rate limit headers from the provider response
            if _tool_ep:
                _m._update_rate_limit_headers(_tool_ep, resp.headers, resp.status_code)
            if resp.status_code == 429:
                _rl_entry = _provider_rate_limits.get(_tool_ep or "", {})
                _reset_str = ""
                if _rl_entry.get("reset_time"):
                    import datetime as _dt
                    _reset_str = f" Reset: {_dt.datetime.fromtimestamp(_rl_entry['reset_time']).strftime('%H:%M:%S')}."
                _err_body = {"type": "error", "error": {"type": "overloaded_error",
                    "message": f"Provider Rate-Limit (429).{_reset_str}"}}
                asyncio.create_task(_deregister_active_request(chat_id))
                from fastapi.responses import JSONResponse as _JSONResponse
                return _JSONResponse(content=_err_body, status_code=529)
            resp.raise_for_status()
            oai_resp = resp.json()
    except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError) as _tex:
        _llm_latency = time.monotonic() - _llm_t0
        PROM_TOOL_TIMEOUTS.labels(node=effective_node, model=effective_model).inc()
        PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="llm_call").observe(_llm_latency)
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "timeout",
            "timeout_s": _node_timeout, "elapsed_s": round(_llm_latency, 3),
        }))
        logger.warning(f"⏱️ Tool handler timeout after {_llm_latency:.1f}s on {effective_node} (limit={_node_timeout}s)")
        # Return valid Anthropic response — Claude Code can abort or restart the prompt
        _timeout_text = (
            f"⚠️ The inference server '{effective_node}' did not respond within "
            f"{int(_node_timeout)}s. Please simplify the prompt or "
            f"choose a faster server."
        )
        _timeout_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _timeout_text}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _timeout_text}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return _timeout_resp
    except httpx.HTTPStatusError as _hex:
        _llm_latency = time.monotonic() - _llm_t0
        _status_code = _hex.response.status_code
        logger.warning(f"⚠️ Tool handler HTTP error {_status_code} from {effective_node}: {_hex}")
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "http_error",
            "status_code": _status_code, "elapsed_s": round(_llm_latency, 3),
        }))
        _err_text = (
            f"⚠️ The inference server '{effective_node}' returned an error "
            f"(HTTP {_status_code}). Please try again."
        )
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _err_text}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }

    _llm_latency = time.monotonic() - _llm_t0
    PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="llm_call").observe(_llm_latency)

    choice = oai_resp["choices"][0]
    msg    = choice["message"]
    usage  = oai_resp.get("usage", {})
    in_tok  = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=effective_model, moe_mode="cc_tool",
            prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
        if not is_user_conn:
            asyncio.create_task(_increment_user_budget(user_id, in_tok + out_tok, prompt_tokens=in_tok, completion_tokens=out_tok))

    # Format detection: raw Qwen tags in text content indicate missing tool schema
    _raw_text = msg.get("content") or ""
    _has_qwen_tags = "<|invoke|>" in _raw_text or "<|plugin|>" in _raw_text
    _has_tool_calls = bool(msg.get("tool_calls"))
    _format_detected = "unknown"

    # Fallback detection: some models (e.g. Gemma, Mistral) return tool calls as
    # JSON in the content field instead of the tool_calls field.
    # Detection: content is valid JSON with "name"+"arguments"/"parameters"/"input" keys
    # and the name matches a known tool — then synthesize into tool_calls.
    if not _has_tool_calls and not _has_qwen_tags and _raw_text and tools:
        _known_tool_names = {t.get("name", "") for t in tools}
        _extracted_tcs: list = []
        # Strip optional markdown code fences
        _probe = re.sub(r"^```(?:json)?\s*|\s*```$", "", _raw_text.strip(), flags=re.DOTALL).strip()
        _json_candidates: list = []
        try:
            _parsed = json.loads(_probe)
            _json_candidates = [_parsed] if isinstance(_parsed, dict) else (_parsed if isinstance(_parsed, list) else [])
        except (json.JSONDecodeError, ValueError):
            # Try to find individual JSON objects (handles trailing text)
            for _m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', _probe):
                try:
                    _json_candidates.append(json.loads(_m.group()))
                except Exception:
                    pass
        for _cand in _json_candidates:
            if not isinstance(_cand, dict):
                continue
            _tc_name = _cand.get("name") or _cand.get("tool") or _cand.get("function")
            _tc_args = _cand.get("arguments") or _cand.get("parameters") or _cand.get("input") or {}
            if _tc_name and _tc_name in _known_tool_names:
                _extracted_tcs.append({
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": _tc_name,
                        "arguments": json.dumps(_tc_args) if isinstance(_tc_args, dict) else str(_tc_args),
                    },
                })
        if _extracted_tcs:
            msg["tool_calls"] = _extracted_tcs
            msg["content"] = None   # No text — tool call only
            _has_tool_calls = True
            _format_detected = "json_in_text"
            PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
            PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="json_in_text").inc()
            _tool_eval_logger.warning(json.dumps({
                "ts": datetime.utcnow().isoformat() + "Z",
                "chat_id": chat_id, "model": effective_model, "node": effective_node,
                "phase": "tool_call", "event": "format_recovered", "format": "json_in_text",
                "tools": [t["function"]["name"] for t in _extracted_tcs],
                "snippet": _raw_text[:200],
            }))
            logger.info(f"🔧 JSON-in-text tool call detected and converted: {[t['function']['name'] for t in _extracted_tcs]}")

    if _has_qwen_tags:
        _format_detected = "qwen_raw"
        PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="qwen_raw").inc()
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "format_error", "format": "qwen_raw",
            "snippet": _raw_text[:200],
        }))
        logger.warning(f"⚠️ Qwen raw format detected on {effective_node} — tools were not passed correctly!")
    elif _has_tool_calls and not (_format_detected == "json_in_text"):
        _format_detected = "json_tool_use"
        PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
    elif not _has_tool_calls:
        _format_detected = "text_only" if _raw_text else "empty"
        if not _raw_text:
            PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="empty").inc()

    PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="total").observe(time.monotonic() - _eval_t0)

    # OpenAI-Response → Anthropic Content-Blocks
    content_blocks: list = []
    if msg.get("content"):
        content_blocks.append({"type": "text", "text": msg["content"]})
    stop_reason = "end_turn"
    if msg.get("tool_calls"):
        stop_reason = "tool_use"
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                args = {}
            content_blocks.append({
                "type":  "tool_use",
                "id":    tc["id"],
                "name":  fn.get("name", "unknown"),
                "input": args
            })
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    # Tool-eval structured log
    _log_tool_eval({
        "ts":              datetime.utcnow().isoformat() + "Z",
        "chat_id":         chat_id,
        "model":           effective_model,
        "node":            effective_node,
        "input_type":      _last_msg_type,
        "tools_available": len(tools),
        "output_type":     stop_reason,
        "tools_called":    [b["name"] for b in content_blocks if b.get("type") == "tool_use"],
        "tool_call_count": sum(1 for b in content_blocks if b.get("type") == "tool_use"),
        "has_text":        any(b["type"] == "text" and b.get("text") for b in content_blocks),
        "latency_s":       round(time.monotonic() - _eval_t0, 3),
        "llm_latency_s":   round(_llm_latency, 3),
        "input_tokens":    in_tok,
        "output_tokens":   out_tok,
        "tool_choice_sent": _effective_tool_choice,
        "format_detected": _format_detected,
    })

    asyncio.create_task(_deregister_active_request(chat_id))
    if not do_stream:
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": content_blocks, "model": model_id,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok}
        }

    return StreamingResponse(
        _anthropic_content_blocks_to_sse(
            content_blocks, chat_id, model_id, in_tok, out_tok, stop_reason
        ),
        media_type="text/event-stream"
    )


async def _anthropic_reasoning_handler(body: dict, chat_id: str, user_id: str = "anon", api_key_id: str = "", session_id: str = None):
    """Text requests via reasoning expert (deepseek-r1/qwq) with <think> parsing.

    Returns responses in Anthropic Extended Thinking format with optional
    thinking block when the model outputs <think>...</think> tags.
    """
    model_id  = body.get("model", "claude-sonnet-4-6")
    messages  = body.get("messages", [])
    system    = body.get("system") or ""
    do_stream = body.get("stream", False)

    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        empty_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        if do_stream:
            async def _empty():
                async for chunk in _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": ""}], chat_id, model_id, 0, 0, "end_turn"
                ):
                    yield chunk
            return StreamingResponse(_empty(), media_type="text/event-stream")
        return empty_resp

    oai_messages = _m._anthropic_to_openai_messages(messages, system)

    # Determine model/node — explicit override takes precedence over dynamic selection
    _reasoning_node_name = "unknown"
    if CLAUDE_CODE_REASONING_MODEL and _CLAUDE_CODE_REASONING_URL:
        reasoning_model = CLAUDE_CODE_REASONING_MODEL
        reasoning_url   = _CLAUDE_CODE_REASONING_URL
        reasoning_token = "ollama"
        _reasoning_node_name = CLAUDE_CODE_REASONING_ENDPOINT or "unknown"
        _reasoning_timeout = float(_server_info(_reasoning_node_name).get("timeout", EXPERT_TIMEOUT))
        logger.info(f"🧠 Reasoning: Override {reasoning_model} @ {CLAUDE_CODE_REASONING_ENDPOINT}")
    else:
        reasoning_experts = EXPERTS.get("reasoning", [])
        if reasoning_experts:
            scored = []
            for e in reasoning_experts:
                score = await _get_expert_score(e["model"], "reasoning")
                scored.append((score, e))
            scored.sort(key=lambda x: -x[0])
            best = scored[0][1]
            reasoning_model = best["model"]
            node = await _m._select_node(reasoning_model, best.get("endpoints") or [best.get("endpoint", "")])
            reasoning_url   = node.get("url") or URL_MAP.get(node["name"])
            reasoning_token = node.get("token", "ollama")
            _reasoning_node_name = node.get("name", "unknown")
            _reasoning_timeout = float(node.get("timeout", EXPERT_TIMEOUT))
            logger.info(f"🧠 Reasoning: Dynamic {reasoning_model} @ {node.get('name','?')} (score={scored[0][0]:.2f})")
        else:
            reasoning_model = CLAUDE_CODE_TOOL_MODEL
            reasoning_url   = _CLAUDE_CODE_TOOL_URL
            reasoning_token = "ollama"
            _reasoning_node_name = CLAUDE_CODE_TOOL_ENDPOINT or "unknown"
            _reasoning_timeout = float(_server_info(_reasoning_node_name).get("timeout", EXPERT_TIMEOUT))
            logger.info(f"🧠 Reasoning: Fallback to tool model {reasoning_model}")

    payload = {
        "model":      reasoning_model,
        "messages":   oai_messages,
        "stream":     False,
        "max_tokens": body.get("max_tokens", REASONING_MAX_TOKENS),
    }
    try:
        async with httpx.AsyncClient(timeout=_reasoning_timeout) as client:
            resp = await client.post(
                f"{reasoning_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {reasoning_token}"}
            )
            resp.raise_for_status()
            oai_resp = resp.json()
    except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError):
        asyncio.create_task(_deregister_active_request(chat_id))
        _err_text = f"⚠️ The reasoning server '{_reasoning_node_name}' did not respond in time."
        if do_stream:
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}], chat_id, model_id, 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return {"id": chat_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": _err_text}], "model": model_id,
                "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
    except httpx.HTTPStatusError as _hex:
        asyncio.create_task(_deregister_active_request(chat_id))
        _err_text = f"⚠️ The reasoning server '{_reasoning_node_name}' returned HTTP {_hex.response.status_code}."
        if do_stream:
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}], chat_id, model_id, 0, 0, "end_turn"
                ),
                media_type="text/event-stream"
            )
        return {"id": chat_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": _err_text}], "model": model_id,
                "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}

    raw   = oai_resp["choices"][0]["message"].get("content", "") or ""
    usage = oai_resp.get("usage", {})
    in_tok  = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=reasoning_model, moe_mode="cc_reasoning",
            prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
        asyncio.create_task(_increment_user_budget(user_id, in_tok + out_tok, prompt_tokens=in_tok, completion_tokens=out_tok))

    # Parse <think>...</think> blocks (deepseek-r1, qwq)
    think_match = re.search(r'<think>(.*?)</think>', raw, re.S)
    if think_match:
        thinking_text = think_match.group(1).strip()
        answer_text   = raw[think_match.end():].strip()
    else:
        thinking_text = ""
        answer_text   = raw.strip()

    content_blocks = []
    if thinking_text:
        content_blocks.append({"type": "thinking", "thinking": thinking_text})
    content_blocks.append({"type": "text", "text": answer_text or ""})

    asyncio.create_task(_deregister_active_request(chat_id))
    if not do_stream:
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": content_blocks, "model": model_id,
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok}
        }

    return StreamingResponse(
        _anthropic_content_blocks_to_sse(content_blocks, chat_id, model_id, in_tok, out_tok, "end_turn"),
        media_type="text/event-stream"
    )


async def _anthropic_moe_handler(body: dict, chat_id: str,
                                  user_permissions: Optional[dict] = None,
                                  user_experts: Optional[dict] = None,
                                  planner_prompt: str = "",
                                  judge_prompt: str = "",
                                  judge_model_override: str = "",
                                  judge_url_override: str = "",
                                  judge_token_override: str = "",
                                  planner_model_override: str = "",
                                  planner_url_override: str = "",
                                  planner_token_override: str = "",
                                  user_id: str = "anon",
                                  api_key_id: str = "",
                                  session_id: str = None,
                                  enable_semantic_memory: bool = False,
                                  cross_session_enabled: bool = False,
                                  cross_session_scopes: Optional[List[str]] = None):
    """Route pure text requests through the MoE agent pipeline."""
    model_id   = body.get("model", "moe-orchestrator-agent")
    messages   = body.get("messages", [])
    system     = body.get("system") or ""
    do_stream  = body.get("stream", False)

    # Last user message as pipeline input
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        empty_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        if do_stream:
            async def _empty():
                async for chunk in _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": ""}], chat_id, model_id, 0, 0, "end_turn"
                ):
                    yield chunk
            return StreamingResponse(_empty(), media_type="text/event-stream")
        return empty_resp

    last_user_content = last_user.get("content", "")
    allowed_skills = (user_permissions or {}).get("skill")
    _raw_cc_input = _m._anthropic_content_to_text(last_user_content)
    user_input  = await _resolve_skill_secure(_raw_cc_input, allowed_skills, user_id=user_id, session_id=session_id)
    _cc_pending_reports: List[str] = []
    if user_input != _raw_cc_input:
        _csm = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)", _raw_cc_input)
        _csname = _csm.group(1) if _csm else "?"
        _csargs = _raw_cc_input[len(_csname)+1:].strip() if _csm else ""
        _cc_pending_reports.append(
            f"🎯 Skill /{_csname} resolved (args: '{_csargs}', {len(user_input)} chars):\n{user_input}"
        )
    user_images = _m._extract_images(last_user_content)
    history_raw = [
        {"role": m["role"],
         "content": _m._anthropic_content_to_text(m.get("content", ""))}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant")
    ]
    history = _m._truncate_history(history_raw)
    _sm_team_ids_h: List[str] = []
    _sm_prefs_h:   dict      = {}
    if enable_semantic_memory and user_id and user_id != "anon":
        try:
            from admin_ui.database import (
                get_user_teams        as _gut_h,
                get_user_memory_prefs as _gmp_h,
            )
            if cross_session_enabled:
                _sm_team_ids_h = await _gut_h(user_id)
            _sm_prefs_h = await _gmp_h(user_id)
        except Exception:
            pass
    history = await _apply_semantic_memory(
        history_raw, history, user_input, session_id,
        enabled=enable_semantic_memory,
        user_id=user_id,
        team_ids=_sm_team_ids_h,
        cross_session_enabled=cross_session_enabled,
        cross_session_scopes=cross_session_scopes or ["private"],
        n_results=0,   # anthropic handler: use template default (passed via caller)
        ttl_hours=0,
        prefer_fresh=_sm_prefs_h.get("prefer_fresh", False),
        share_with_team=_sm_prefs_h.get("share_with_team", False),
    )

    invoke_state = {
        "input": user_input, "response_id": chat_id,
        "mode": "agent_orchestrated" if CLAUDE_CODE_MODE == "moe_orchestrated" else "agent",
        "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
        "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
        "chat_history": history, "reasoning_trace": "", "system_prompt": system,
        "images": user_images,
        "user_permissions": user_permissions or {},
        "user_experts": user_experts or {},
        "planner_prompt": planner_prompt or "",
        "judge_prompt":   judge_prompt or "",
        "judge_model_override":   judge_model_override or "",
        "judge_url_override":     judge_url_override or "",
        "judge_token_override":   judge_token_override or "",
        "planner_model_override": planner_model_override or "",
        "planner_url_override":   planner_url_override or "",
        "planner_token_override": planner_token_override or "",
        "template_name":          body.get("model", "") or "",
        "pending_reports": _cc_pending_reports,
    }
    invoke_cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}

    if do_stream:
        async def _moe_stream():
            _did_deregister = False
            try:
                # Set up progress queue so _report() calls don't hang
                progress_q: asyncio.Queue = asyncio.Queue()
                ctx_token = _progress_queue.set(progress_q)
                result_box: dict = {}

                async def _run():
                    try:
                        result_box["data"] = await app_graph.ainvoke(invoke_state, invoke_cfg)
                    except Exception as e:
                        result_box["error"] = str(e)
                    finally:
                        await progress_q.put(None)

                asyncio.create_task(_run())

                # Immediately send message_start + content_block_start
                # → client sees response start, keeps connection open
                yield _sse_event("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": chat_id, "type": "message", "role": "assistant",
                        "content": [], "model": model_id,
                        "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 1}
                    }
                })
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}
                })
                yield _sse_event("ping", {"type": "ping"})

                # Wait for pipeline end; stream progress messages as text when _CC_STREAM_THINK is set
                while True:
                    try:
                        msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                        if msg is None:
                            break
                        if _CC_STREAM_THINK and msg:
                            think_chunk = f"\n{msg}\n"
                            yield _sse_event("content_block_delta", {
                                "type": "content_block_delta", "index": 0,
                                "delta": {"type": "text_delta", "text": think_chunk}
                            })
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"

                _progress_queue.reset(ctx_token)

                # Stream result
                data    = result_box.get("data", {})
                content = data.get("final_response", "") if "data" in result_box \
                          else f"Error: {result_box.get('error', 'Unknown')}"
                in_tok  = data.get("prompt_tokens", 0)
                out_tok = data.get("completion_tokens", 0)

                if user_id != "anon":
                    asyncio.create_task(_log_usage_to_db(
                        user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                        model="moe-orchestrator", moe_mode="cc_moe",
                        prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
                    _uc_p = data.get("user_conn_prompt_tokens", 0)
                    _uc_c = data.get("user_conn_completion_tokens", 0)
                    _bill_p = max(0, in_tok - _uc_p)
                    _bill_c = max(0, out_tok - _uc_c)
                    asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))

                for i in range(0, max(len(content), 1), SSE_CHUNK_SIZE):
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": content[i:i+SSE_CHUNK_SIZE]}
                    })
                    await asyncio.sleep(0.005)

                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
                yield _sse_event("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": out_tok}
                })
                yield _sse_event("message_stop", {"type": "message_stop"})
                asyncio.create_task(_deregister_active_request(chat_id))
                _did_deregister = True
            finally:
                if not _did_deregister:
                    asyncio.create_task(_deregister_active_request(chat_id))

        return StreamingResponse(_moe_stream(), media_type="text/event-stream")

    # Non-Streaming
    result  = await app_graph.ainvoke(invoke_state, invoke_cfg)
    content = result.get("final_response", "")
    _p_tok = result.get("prompt_tokens", 0)
    _c_tok = result.get("completion_tokens", 0)
    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model="moe-orchestrator", moe_mode="cc_moe",
            prompt_tokens=_p_tok, completion_tokens=_c_tok, session_id=session_id))
        _uc_p = result.get("user_conn_prompt_tokens", 0)
        _uc_c = result.get("user_conn_completion_tokens", 0)
        _bill_p = max(0, _p_tok - _uc_p)
        _bill_c = max(0, _c_tok - _uc_c)
        asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))
    asyncio.create_task(_deregister_active_request(chat_id))
    return {
        "id": chat_id, "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {
            "input_tokens":  _p_tok,
            "output_tokens": _c_tok
        }
    }


async def anthropic_messages(request: Request):
    """Anthropic Messages API — drop-in compatible with Claude Code CLI and Anthropic SDK.

    Routing:
    - Claude Code sessions (claude-* model ID):
        - tool_use / tool_result  → devstral:24b (code specialist, robust function calling)
        - pure text requests      → MoE agent pipeline (mode=agent)
    - Standard MoE sessions:
        - tools / tool_results    → _m.judge_llm (magistral:24b)
        - pure text requests      → MoE agent pipeline

    Configuration for Claude Code:
        ANTHROPIC_BASE_URL=http://<server>:8002
        ANTHROPIC_API_KEY=<any>        # not validated
        CLAUDE_MODEL=claude-sonnet-4-6  (or other claude-* ID)
    """
    body       = await request.json()
    chat_id    = f"msg_{uuid.uuid4().hex[:24]}"
    session_id = _extract_session_id(request)
    messages   = body.get("messages", [])
    tools      = body.get("tools", [])
    model     = body.get("model", "")

    # Auth
    raw_key  = _extract_api_key(request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        if user_ctx["error"] == "budget_exceeded":
            return JSONResponse(status_code=429, content={"error": {
                "message": f"Budget exceeded ({user_ctx.get('limit_type', 'unknown')} limit reached)",
                "type": "insufficient_quota", "code": "budget_exceeded"
            }})
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key", "type": "invalid_request_error", "code": "invalid_api_key"
        }})
    _user_id      = user_ctx.get("user_id", "anon")
    _api_key_id   = user_ctx.get("key_id", "")
    _user_perms   = json.loads(user_ctx.get("permissions_json", "{}"))
    _user_tmpls_json2   = user_ctx.get("user_templates_json", "{}")
    _user_conns_json2   = user_ctx.get("user_connections_json", "{}")
    _user_experts = _resolve_user_experts(user_ctx.get("permissions_json", ""),
                                           user_templates_json=_user_tmpls_json2,
                                           user_connections_json=_user_conns_json2) or {}
    _user_tmpl_prompts = _resolve_template_prompts(user_ctx.get("permissions_json", ""),
                                                    user_templates_json=_user_tmpls_json2,
                                                    user_connections_json=_user_conns_json2)

    # ─── Resolve per-user CC profile ──────────────────────────────────────────
    # Priority: 1. key-specific profile  2. user default  3. first available
    _cc_profile_ids = _user_perms.get("cc_profile", [])
    _user_cc_profiles_json = user_ctx.get("user_cc_profiles_json", "{}")
    _user_cc_map: dict = {}
    try:
        _user_cc_map = json.loads(_user_cc_profiles_json or "{}")
    except Exception:
        pass
    _effective_cc_mode       = CLAUDE_CODE_MODE
    _effective_tool_model    = CLAUDE_CODE_TOOL_MODEL
    _effective_tool_endpoint = CLAUDE_CODE_TOOL_ENDPOINT
    _effective_tool_url      = _CLAUDE_CODE_TOOL_URL
    _effective_tool_token    = _CLAUDE_CODE_TOOL_TOKEN
    _user_cc_profile         = None
    if _cc_profile_ids:
        def _resolve_cc_profile(profile_id: str):
            if not profile_id:
                return None
            return (_user_cc_map.get(profile_id)
                    or next((p for p in _m._read_cc_profiles() if p.get("id") == profile_id), None))

        _key_profile_id     = user_ctx.get("key_cc_profile_id", "") or ""
        _default_profile_id = user_ctx.get("default_cc_profile_id", "") or ""
        _user_cc_profile = (
            _resolve_cc_profile(_key_profile_id)
            or _resolve_cc_profile(_default_profile_id)
            or next((v for pid in _cc_profile_ids for v in [_user_cc_map.get(pid)] if v), None)
            or next((p for p in _m._read_cc_profiles() if p.get("id") in _cc_profile_ids and p.get("enabled", True)), None)
        )
        if _user_cc_profile:
            _effective_cc_mode       = _user_cc_profile.get("moe_mode", CLAUDE_CODE_MODE)
            _effective_tool_model    = _user_cc_profile.get("tool_model", CLAUDE_CODE_TOOL_MODEL).strip().rstrip("*")
            _effective_tool_endpoint = _user_cc_profile.get("tool_endpoint", CLAUDE_CODE_TOOL_ENDPOINT)
            _effective_tool_url      = URL_MAP.get(_effective_tool_endpoint)
            _effective_tool_token    = TOKEN_MAP.get(_effective_tool_endpoint, "ollama")
            _effective_tool_is_user_conn = False  # tracks whether tool_url came from a user connection
            if not _effective_tool_url:
                # Fallback: resolve tool_endpoint as a user private connection
                _cc_conns: dict = {}
                try:
                    _cc_conns = json.loads(_user_conns_json2 or "{}")
                except Exception:
                    pass
                _uc = _cc_conns.get(_effective_tool_endpoint)
                if _uc:
                    _effective_tool_url          = _uc["url"]
                    _effective_tool_token        = _uc.get("api_key") or "ollama"
                    _effective_tool_is_user_conn = True
                else:
                    _effective_tool_url = _CLAUDE_CODE_TOOL_URL
            # CC profile can force an expert template (admin_override bypasses permission check)
            _cc_tmpl_id = _user_cc_profile.get("expert_template_id") or None
            if _cc_tmpl_id:
                _user_experts = _resolve_user_experts(
                    user_ctx.get("permissions_json", ""),
                    override_tmpl_id=_cc_tmpl_id,
                    user_templates_json=_user_tmpls_json2,
                    admin_override=True,
                    user_connections_json=_user_conns_json2,
                ) or {}
                _user_tmpl_prompts = _resolve_template_prompts(
                    user_ctx.get("permissions_json", ""),
                    override_tmpl_id=_cc_tmpl_id,
                    user_templates_json=_user_tmpls_json2,
                    admin_override=True,
                    user_connections_json=_user_conns_json2,
                )

    # Detect whether request originates from Claude Code / Anthropic SDK
    is_claude_code = model.startswith("claude-") or model in CLAUDE_CODE_MODELS

    has_tool_results = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m.get("content", []))
        for m in messages
    )

    # Per-node timeout for the configured tool model (profile override takes precedence)
    _cc_tool_node_cfg = _server_info(_effective_tool_endpoint)
    _cc_tool_timeout  = int(_cc_tool_node_cfg.get("timeout", JUDGE_TIMEOUT))
    if _user_cc_profile and _user_cc_profile.get("tool_timeout"):
        _cc_tool_timeout = int(_user_cc_profile["tool_timeout"])

    # Live monitoring: register request
    _cc_moe_mode = (
        "cc_tool"      if (tools or has_tool_results or _effective_cc_mode == "native") else
        "cc_reasoning" if _effective_cc_mode == "moe_reasoning" else
        "cc_moe"
    )
    _cc_req_type   = "streaming" if body.get("stream", False) else "batch"
    _cc_client_ip  = request.client.host if request.client else ""
    # Backend model for live monitoring: shows the actual LLM / template in parentheses
    _cc_backend_model = _effective_tool_model
    if _cc_moe_mode == "cc_moe":
        _cc_tmpl_id_for_display = _user_cc_profile.get("expert_template_id") if _user_cc_profile else None
        if _cc_tmpl_id_for_display:
            _cc_backend_model = next(
                (t.get("name", _cc_tmpl_id_for_display) for t in _m._read_expert_templates()
                 if t.get("id") == _cc_tmpl_id_for_display),
                _cc_tmpl_id_for_display
            )
        else:
            _cc_backend_model = "MoE"
    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=_user_id, model=model,
        moe_mode=_cc_moe_mode, req_type=_cc_req_type, client_ip=_cc_client_ip,
        backend_model=_cc_backend_model,
        api_key_id=_api_key_id,
    ))

    try:
        # Mode 1: Native — pass everything directly to the configured tool model
        if _effective_cc_mode == "native":
            return await _anthropic_tool_handler(
                body, chat_id,
                tool_model=_effective_tool_model,
                tool_url=_effective_tool_url,
                tool_token=_effective_tool_token,
                tool_timeout=_cc_tool_timeout,
                tool_node=_effective_tool_endpoint,
                user_id=_user_id,
                api_key_id=_api_key_id,
                session_id=session_id,
                is_user_conn=_effective_tool_is_user_conn,
            )

        # All modes: tool calls always go to the tool model (precise function calling needed)
        if tools or has_tool_results:
            if is_claude_code:
                return await _anthropic_tool_handler(
                    body, chat_id,
                    tool_model=_effective_tool_model,
                    tool_url=_effective_tool_url,
                    tool_token=_effective_tool_token,
                    tool_timeout=_cc_tool_timeout,
                    tool_node=_effective_tool_endpoint,
                    user_id=_user_id,
                    api_key_id=_api_key_id,
                    session_id=session_id,
                    is_user_conn=_effective_tool_is_user_conn,
                )
            return await _anthropic_tool_handler(body, chat_id, user_id=_user_id, api_key_id=_api_key_id, session_id=session_id)

        # Mode 2: MoE Reasoning — reasoning expert with <think> parsing and thinking blocks
        if _effective_cc_mode == "moe_reasoning":
            return await _anthropic_reasoning_handler(body, chat_id, user_id=_user_id, api_key_id=_api_key_id, session_id=session_id)

        # Mode 3 + fallback: MoE Orchestrated — full planner, all experts
        return await _anthropic_moe_handler(body, chat_id,
                                             user_permissions=_user_perms,
                                             user_experts=_user_experts,
                                             planner_prompt=_user_tmpl_prompts["planner_prompt"],
                                             judge_prompt=_user_tmpl_prompts["judge_prompt"],
                                             judge_model_override=_user_tmpl_prompts["judge_model_override"],
                                             judge_url_override=_user_tmpl_prompts["judge_url_override"],
                                             judge_token_override=_user_tmpl_prompts["judge_token_override"],
                                             planner_model_override=_user_tmpl_prompts["planner_model_override"],
                                             planner_url_override=_user_tmpl_prompts["planner_url_override"],
                                             planner_token_override=_user_tmpl_prompts["planner_token_override"],
                                             user_id=_user_id,
                                             api_key_id=_api_key_id,
                                             session_id=session_id,
                                             enable_semantic_memory=_user_tmpl_prompts.get("enable_semantic_memory", False),
                                             cross_session_enabled=_user_tmpl_prompts.get("enable_cross_session_memory", False),
                                             cross_session_scopes=_user_tmpl_prompts.get("cross_session_scopes", ["private"]))
    except Exception as _exc:
        logger.error("Messages-Endpoint unbehandelte Exception (chat_id=%s): %s", chat_id, _exc, exc_info=True)
        asyncio.create_task(_deregister_active_request(chat_id))
        return JSONResponse(status_code=500, content={"error": {
            "type": "api_error", "message": f"Internal server error: {_exc}"
        }})


_ONTOLOGY_RUN_KEY = "moe:maintenance:ontology:run"
_ONTOLOGY_RUNS_HISTORY_KEY = "moe:maintenance:ontology:runs"


async def _set_healer_status(**fields) -> None:
    if state.redis_client is None:
        return
    try:
        await redis_client.hset(_ONTOLOGY_RUN_KEY, mapping={k: str(v) for k, v in fields.items()})
        await redis_client.expire(_ONTOLOGY_RUN_KEY, 86400)
    except Exception:
        pass


async def _run_healer_task(concurrency: int, batch_size: int, run_id: str) -> None:
    """Spawn gap_healer_templates.py --once in the orchestrator container."""
    import time as _t
    import uuid as _uuid
    start = _t.time()
    # Clear stale fields from previous runs first.
    if state.redis_client is not None:
        try:
            await redis_client.delete(_ONTOLOGY_RUN_KEY)
        except Exception:
            pass
    await _set_healer_status(
        status="running", run_id=run_id, started_at=str(start),
        concurrency=concurrency, batch_size=batch_size,
        processed=0, written=0, failed=0, message="",
    )
    env = os.environ.copy()
    env["CONCURRENCY"] = str(concurrency)
    env["BATCH_SIZE"] = str(batch_size)
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    # The healer calls /v1/chat/completions which requires a valid Bearer.
    # Use the SYSTEM_API_KEY (installed with an active api_keys row).
    sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
    if sys_key:
        env["MOE_API_KEY"] = sys_key
    proc = await asyncio.create_subprocess_exec(
        "python3", "/app/scripts/gap_healer_templates.py", "--once",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stats = {"processed": 0, "written": 0, "failed": 0}
    assert proc.stdout is not None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            if "✓" in text and "→" in text:
                stats["written"] += 1
            elif "?" in text and "→" in text:
                stats["processed"] += 1
            elif "✗" in text:
                stats["failed"] += 1
            if any(stats.values()):
                await _set_healer_status(status="running", **stats)
        rc = await proc.wait()
    except Exception as e:
        await _set_healer_status(status="failed", message=str(e)[:200])
        return
    final = "ready" if rc == 0 else "failed"
    await _set_healer_status(
        status=final, run_id=run_id, finished_at=str(_t.time()),
        exit_code=rc, **stats,
    )
    if state.redis_client is not None:
        try:
            entry = json.dumps({
                "run_id": run_id, "type": "oneshot", "template": "",
                "started_at": start, "finished_at": _t.time(),
                "exit_code": rc, **stats,
            })
            await redis_client.lpush(_ONTOLOGY_RUNS_HISTORY_KEY, entry)
            await redis_client.ltrim(_ONTOLOGY_RUNS_HISTORY_KEY, 0, 99)
        except Exception:
            pass


async def clear_ontology_healer_status():
    """Delete the healer run status from Redis (dismiss failed/stale entries)."""
    if state.redis_client is None:
        return {"ok": False, "reason": "no_redis"}
    try:
        cur = await redis_client.hgetall(_ONTOLOGY_RUN_KEY)
        if cur and cur.get("status") == "running":
            return {"ok": False, "reason": "still_running"}
        await redis_client.delete(_ONTOLOGY_RUN_KEY)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:100]}


# ─── Dedicated Ontology Gap Healer (permanent loop, single node) ─────────────

_DEDICATED_HEALER_KEY = "moe:ontology:dedicated"
# _dedicated_healer_proc lives in state.py
# Mutex prevents concurrent auto-restart tasks (stream task + watchdog can both trigger).
# _dedicated_healer_restart_lock lives in state.py


async def _auto_resume_dedicated_healer() -> None:
    """Resume the dedicated healer after a container restart if Redis shows it was running.

    Called as a background task during startup. A short sleep lets the ASGI
    server finish binding before the healer subprocess sends its first request
    to the local API.
    """
    # state._dedicated_healer_proc is in state — no global needed
    import time as _t
    await asyncio.sleep(5)  # wait for ASGI server to start serving

    if state.redis_client is None:
        return
    try:
        data = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
    except Exception:
        return

    if not data or data.get("status") != "running":
        return

    template = data.get("template", "").strip()
    if not template:
        return

    # Confirm the previous PID is dead — if still alive, no restart needed.
    pid = int(data.get("pid", 0))
    if pid:
        try:
            os.kill(pid, 0)
            logger.info("🔄 Dedicated healer PID %s still alive — skipping auto-resume", pid)
            return
        except OSError:
            pass  # process dead; container was restarted

    logger.info("🔄 Auto-resuming dedicated healer (template=%s, prev_pid=%s)", template, pid)

    # Clean stale fields before resuming so the watchdog gets a fresh baseline.
    try:
        await redis_client.delete(_DEDICATED_HEALER_KEY)
        await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
            "status": "starting",
            "template": template,
            "processed": "0",
            "written": "0",
            "failed": "0",
            "stalled": "0",
            "started_at": str(_t.time()),
            "auto_restart": "1",
        })
    except Exception:
        pass

    env = os.environ.copy()
    env["TEMPLATE_POOL"] = template
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    sys_key = (os.environ.get("SYSTEM_API_KEY", "") or os.environ.get("MOE_API_KEY", "")).strip()
    if sys_key:
        env["MOE_API_KEY"] = sys_key

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "/app/scripts/gap_healer_templates.py",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        state._dedicated_healer_proc = proc
        await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"pid": str(proc.pid)})

        # Early-exit check: if the process dies within 3 s, mark as stopped.
        first_line: "bytes | None" = None
        try:
            fl_task = asyncio.create_task(proc.stdout.readline())
            wt_task = asyncio.create_task(proc.wait())
            done, pending = await asyncio.wait({fl_task, wt_task}, timeout=3.0,
                                               return_when=asyncio.FIRST_COMPLETED)
            if wt_task in done:
                rc = wt_task.result()
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                    "status": "stopped", "exit_code": str(rc),
                })
                state._dedicated_healer_proc = None
                logger.error("❌ Dedicated healer exited immediately on resume (rc=%s)", rc)
                return
            wt_task.cancel()
            try:
                await wt_task
            except asyncio.CancelledError:
                pass
            if fl_task in done:
                first_line = fl_task.result() or None
            else:
                fl_task.cancel()
                try:
                    await fl_task
                except asyncio.CancelledError:
                    pass
        except Exception:
            pass

        await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "running"})
        asyncio.create_task(_stream_dedicated_healer(proc, first_line=first_line))
        logger.info("✅ Dedicated healer auto-resumed — PID %s (template=%s)", proc.pid, template)
    except Exception as e:
        logger.error("❌ Failed to auto-resume dedicated healer: %s", e)
        try:
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "stopped"})
        except Exception:
            pass


_HEALER_STALL_SECONDS = 300  # 5 minutes without output → stalled
_HEALER_RESTART_DELAY_S = 30  # pause between auto-restarts


async def _watchdog_dedicated_healer() -> None:
    """Periodic watchdog: escalate or auto-restart a stalled dedicated healer.

    Runs every 60 s. If the healer reports 'running' in Redis but has not
    produced any stdout output for HEALER_STALL_SECONDS, it is marked 'stalled'
    and the subprocess is restarted automatically.
    """
    import time as _t
    # state._dedicated_healer_proc is in state — no global needed
    while True:
        await asyncio.sleep(60)
        if state.redis_client is None:
            continue
        try:
            data = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
        except Exception:
            continue

        if not data or data.get("status") not in ("running", "stalled"):
            continue

        # PID liveness check — catches clean exits that didn't update status.
        pid = int(data.get("pid", 0))
        pid_alive = False
        if pid:
            try:
                os.kill(pid, 0)
                pid_alive = True
            except OSError:
                pass
        if not pid_alive:
            logger.warning(
                "⚠️  Dedicated healer PID %d is dead but status=%s — triggering auto-restart.",
                pid, data.get("status", "?"),
            )
            asyncio.create_task(_dedicated_healer_auto_restart_if_needed(
                template_hint=data.get("template", ""),
            ))
            continue

        last_ts = float(data.get("last_activity_ts") or 0)
        age = _t.time() - last_ts if last_ts else float("inf")

        if age < _HEALER_STALL_SECONDS:
            # Remove stalled flag if activity resumed.
            if data.get("stalled") == "1":
                try:
                    await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"stalled": "0", "status": "running"})
                except Exception:
                    pass
            continue

        logger.warning(
            "⚠️  Dedicated healer stalled — no output for %.0f s (template=%s). Auto-restarting.",
            age, data.get("template", "?"),
        )

        # Mark as stalled so the UI can show a warning immediately.
        try:
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"stalled": "1", "status": "stalled"})
        except Exception:
            pass

        # Kill the stuck subprocess before restarting.
        proc = state._dedicated_healer_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Re-use the same template to restart.
        template = data.get("template", "").strip()
        if not template:
            continue
        env = os.environ.copy()
        env["TEMPLATE_POOL"] = template
        env.setdefault("REQUEST_TIMEOUT", "900")
        env.setdefault("MOE_API_BASE", "http://localhost:8000")
        sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
        if sys_key:
            env["MOE_API_KEY"] = sys_key
        try:
            new_proc = await asyncio.create_subprocess_exec(
                "python3", "/app/scripts/gap_healer_templates.py",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            state._dedicated_healer_proc = new_proc
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "running",
                "stalled": "0",
                "pid": str(new_proc.pid),
                "started_at": str(_t.time()),
                "last_activity_ts": str(_t.time()),
            })
            asyncio.create_task(_stream_dedicated_healer(new_proc))
            logger.info("✅ Dedicated healer auto-restarted after stall — PID %s", new_proc.pid)
        except Exception as exc:
            logger.error("❌ Dedicated healer restart failed: %s", exc)


# _set_healer_status_dedicated, _stream_dedicated_healer,
# _dedicated_healer_auto_restart_if_needed moved to services/healer.py
from services.healer import (
    _set_healer_status_dedicated,
    _stream_dedicated_healer,
    _dedicated_healer_auto_restart_if_needed,
    _DEDICATED_HEALER_KEY,
    _HEALER_STALL_SECONDS,
    _HEALER_RESTART_DELAY_S,
)


async def stop_dedicated_healer():
    """Stop the running dedicated healer loop."""
    # state._dedicated_healer_proc is in state — no global needed
    import signal as _sig
    stopped = False
    # Try in-memory handle first
    if state._dedicated_healer_proc is not None and state._dedicated_healer_proc.returncode is None:
        try:
            state._dedicated_healer_proc.terminate()
            await asyncio.wait_for(state._dedicated_healer_proc.wait(), timeout=5.0)
        except Exception:
            try:
                state._dedicated_healer_proc.kill()
            except Exception:
                pass
        state._dedicated_healer_proc = None
        stopped = True

    # Also kill by PID from Redis (handles cross-restart cases)
    template_name = ""
    if state.redis_client is not None:
        try:
            cur = await redis_client.hgetall(_DEDICATED_HEALER_KEY)
            template_name = cur.get("template", "")
            pid = int(cur.get("pid", 0))
            if pid and not stopped:
                try:
                    os.kill(pid, _sig.SIGTERM)
                    stopped = True
                except OSError:
                    pass
            # Full clean: delete all fields, keep only template + stopped status so the
            # UI can display which template was last used without stale counters.
            await redis_client.delete(_DEDICATED_HEALER_KEY)
            await redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "stopped",
                "template": template_name,
                "auto_restart": "0",
            })
        except Exception:
            pass

    return {"ok": True, "stopped": stopped}


async def verify_dedicated_healer():
    """Verify that the dedicated healer is genuinely running.

    Returns whether the subprocess PID is alive AND whether the healer has
    produced an active API request visible in the live-monitoring table
    (moe:active:* keys with model prefix 'moe-ontology-curator').

    The UI polls this endpoint after clicking 'Dauerlauf starten' to confirm
    the process actually started before switching the button to 'running'.
    """
    import json as _json
    data: dict = {}
    if state.redis_client is not None:
        try:
            data = await redis_client.hgetall(_DEDICATED_HEALER_KEY) or {}
        except Exception:
            pass

    pid = int(data.get("pid", 0))
    pid_alive = False
    if pid:
        try:
            os.kill(pid, 0)
            pid_alive = True
        except OSError:
            pass
    # Also trust the in-memory handle if PID lookup fails (same container).
    if not pid_alive and state._dedicated_healer_proc is not None and state._dedicated_healer_proc.returncode is None:
        pid_alive = True

    # Scan moe:active:* for a live healer request.
    active_chat_id = None
    if state.redis_client is not None:
        try:
            async for key in redis_client.scan_iter("moe:active:*"):
                try:
                    raw = await redis_client.get(key)
                    if raw:
                        meta = _json.loads(raw)
                        if (meta.get("model") or "").startswith("moe-ontology-curator"):
                            active_chat_id = meta.get("chat_id")
                            break
                except Exception:
                    continue
        except Exception:
            pass

    import time as _t
    last_ts = float(data.get("last_activity_ts") or 0)
    activity_age = round(_t.time() - last_ts) if last_ts else None
    # Consider the healer "active" if it has a live API request OR produced output within 60 s.
    is_active = active_chat_id is not None or (activity_age is not None and activity_age < 60)

    return {
        "pid_alive": pid_alive,
        "has_active_request": active_chat_id is not None,
        "is_active": is_active,
        "chat_id": active_chat_id,
        "status": data.get("status", "stopped"),
        "pid": pid,
        "activity_age_seconds": activity_age,
    }


# ─── Benchmark Node Reservation ──────────────────────────────────────────────

_m._BENCHMARK_RESERVED_KEY  = "moe:benchmark_reserved"
_m._BENCHMARK_LOCK_META_KEY = "moe:benchmark_lock_meta"


async def benchmark_unlock():
    """Release all benchmark node reservations."""
    if state.redis_client is None:
        return {"ok": False, "reason": "redis_unavailable"}
    meta = await redis_client.hgetall(_m._BENCHMARK_LOCK_META_KEY) or {}
    try:
        released = json.loads(meta.get("nodes", "[]"))
    except Exception:
        released = []
    await redis_client.delete(_m._BENCHMARK_RESERVED_KEY)
    await redis_client.delete(_m._BENCHMARK_LOCK_META_KEY)
    logger.info(f"🔓 Benchmark lock released: nodes={released}")
    return {"ok": True, "released": released}


async def get_knowledge_stats():
    """Aggregate Neo4j counters for the stats dashboard."""
    try:
        from neo4j import AsyncGraphDatabase
        uri = os.environ.get("NEO4J_URI", "bolt://neo4j-knowledge:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        pwd = os.environ.get("NEO4J_PASSWORD") or os.environ.get("NEO4J_PASS") or ""
        driver = AsyncGraphDatabase.driver(uri, auth=(user, pwd))
    except Exception as e:
        return {"error": f"neo4j init: {e}"}
    stats: dict = {}
    try:
        async with driver.session() as s:
            r = await s.run("MATCH (e:Entity) RETURN count(e) AS n")
            stats["entities_total"] = (await r.single())["n"]
            r = await s.run("MATCH ()-[r]->() RETURN count(r) AS n")
            stats["relations_total"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.created_at >= datetime() - duration('P1D') "
                "RETURN count(e) AS n"
            )
            stats["entities_last_24h"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.created_at >= datetime() - duration('P7D') "
                "RETURN count(e) AS n"
            )
            stats["entities_last_7d"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.source IS NOT NULL "
                "RETURN e.source AS source, count(e) AS n ORDER BY n DESC LIMIT 10"
            )
            stats["entities_by_source"] = [
                {"source": rec["source"], "n": rec["n"]} async for rec in r
            ]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.type IS NOT NULL "
                "RETURN e.type AS type, count(e) AS n ORDER BY n DESC LIMIT 10"
            )
            stats["top_types"] = [
                {"type": rec["type"], "n": rec["n"]} async for rec in r
            ]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.curator_template IS NOT NULL "
                "RETURN e.curator_template AS template, count(e) AS n "
                "ORDER BY n DESC LIMIT 20"
            )
            stats["entities_by_curator"] = [
                {"template": rec["template"], "n": rec["n"]} async for rec in r
            ]
    except Exception as e:
        stats["error"] = str(e)
    finally:
        await driver.close()
    return stats


async def get_planner_patterns(limit: int = 20):
    """Shows proven planner patterns based on positive user feedback."""
    if state.redis_client is None:
        return {"error": "Valkey not available"}
    try:
        patterns = await redis_client.zrevrange("moe:planner_success", 0, limit - 1, withscores=True)
        return {"patterns": [{"signature": sig, "count": int(score)} for sig, score in patterns]}
    except Exception as e:
        return {"error": str(e)}


_PL_SORT_COLS = {
    "requested_at": "ul.requested_at",
    "model":        "ul.model",
    "moe_mode":     "ul.moe_mode",
    "username":     "u.username",
    "total_tokens": "ul.total_tokens",
    "latency_ms":   "ul.latency_ms",
    "complexity_level": "ul.complexity_level",
}

async def pipeline_log(
    raw_request: Request,
    limit: int = 100,
    offset: int = 0,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    model: Optional[str] = None,
    moe_mode: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    complexity_level: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    sort_by: str = "requested_at",
    sort_dir: str = "desc",
    format: str = "json",
) -> Response:
    """Pipeline Transparency Log — query routing decisions, expert domains, and latency per request.

    Supports filtering by user, model, mode, date range, complexity level, and cache hit status.
    Supports sorting by any column via sort_by/sort_dir.
    Returns JSON (default) or CSV for BI/export use. Auth: admin API key required.
    """
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    _sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
    _is_sys_key = bool(_sys_key and raw_key == _sys_key)
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})
    if not (_is_sys_key or user_ctx.get("is_admin")):
        return JSONResponse(status_code=403, content={"error": "Admin access required"})

    try:
        if state._userdb_pool is None:
            return JSONResponse(status_code=503, content={"error": "Database unavailable"})

        conditions: list[str] = []
        params: list = []

        if user_id:
            conditions.append("ul.user_id = %s")
            params.append(user_id)
        if username:
            conditions.append("u.username ILIKE %s")
            params.append(f"%{username}%")
        if model:
            conditions.append("ul.model ILIKE %s")
            params.append(f"%{model}%")
        if moe_mode:
            conditions.append("ul.moe_mode = %s")
            params.append(moe_mode)
        if from_date:
            conditions.append("ul.requested_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("ul.requested_at <= %s")
            params.append(to_date + "T23:59:59")
        if complexity_level:
            conditions.append("ul.complexity_level = %s")
            params.append(complexity_level)
        if cache_hit is not None:
            conditions.append("ul.cache_hit = %s")
            params.append(cache_hit)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        _sort_col = _PL_SORT_COLS.get(sort_by, "ul.requested_at")
        _sort_ord = "ASC" if sort_dir.lower() == "asc" else "DESC"
        params.extend([limit, offset])

        async with _userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""SELECT ul.request_id, ul.user_id, u.username,
                               ul.model, ul.moe_mode, ul.session_id,
                               ul.prompt_tokens, ul.completion_tokens, ul.total_tokens,
                               ul.latency_ms, ul.complexity_level, ul.expert_domains,
                               ul.cache_hit, ul.agentic_rounds, ul.status, ul.requested_at
                        FROM usage_log ul
                        LEFT JOIN users u ON ul.user_id = u.id
                        {where}
                        ORDER BY {_sort_col} {_sort_ord}
                        LIMIT %s OFFSET %s""",
                    params,
                )
                rows = await cur.fetchall()
                cols = [d.name for d in cur.description]

                await cur.execute(
                    f"SELECT COUNT(*) FROM usage_log ul LEFT JOIN users u ON ul.user_id = u.id {where}",
                    params[:-2],
                )
                total = (await cur.fetchone())[0]

        records = [dict(zip(cols, row)) for row in rows]

        if format == "csv":
            import io, csv as _csv
            buf = io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=cols)
            writer.writeheader()
            writer.writerows(records)
            return Response(
                content=buf.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=pipeline_log.csv"},
            )

        return JSONResponse({
            "total": total,
            "limit": limit,
            "offset": offset,
            "records": records,
        })
    except Exception as e:
        logger.warning("Pipeline log query failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ─── OLLAMA COMPATIBILITY API (/api/*) ───────────────────────────────────────
#
# Translates the Ollama wire-protocol to the MoE pipeline and back.
# Auth: same Bearer-Token (moe-sk-*) as /v1/.
# Streaming: NDJSON (one JSON object per line, \n-separated) — not SSE.
#
# Supported:
#   GET  /api/version   – version stub
#   GET  /api/tags      – model list in Ollama format (auth required)
#   GET  /api/ps        – templates as "loaded" models
#   POST /api/show      – template details
#   POST /api/chat      – main chat endpoint (streaming NDJSON or single JSON)
#   POST /api/generate  – single-turn prompt (routes through /api/chat logic)
#   POST /api/pull      – fake pull-progress stream
#   DELETE /api/delete  – 400 stub (managed via Admin UI)
#   POST /api/copy|push|embed – 400 stubs
# ─────────────────────────────────────────────────────────────────────────────


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


def _ollama_options_to_oai(options: dict) -> dict:
    """Map Ollama generation options to OpenAI-compatible parameters."""
    result = {}
    if "temperature" in options:
        result["temperature"] = options["temperature"]
    if "num_predict" in options:
        result["max_tokens"] = options["num_predict"]
    if "top_p" in options:
        result["top_p"] = options["top_p"]
    if "seed" in options:
        result["seed"] = options["seed"]
    return result


async def _ollama_resolve_template(model_name: str, user_perms: dict, user_templates_json: str = "{}"):
    """Return (tmpl_id, tmpl_dict | None, error_response | None) for an Ollama model name."""
    all_tmpls   = _m._read_expert_templates()
    matched     = next((t for t in all_tmpls if t.get("name") == model_name or t.get("id") == model_name), None)
    owned_tmpls: dict = {}
    try:
        owned_tmpls = json.loads(user_templates_json or "{}")
    except Exception:
        pass
    if not matched:
        # Check user-owned templates before giving up
        for ut_id, ut_cfg in owned_tmpls.items():
            ut_name = ut_cfg.get("name", "")
            if ut_name == model_name or ut_id == model_name:
                return ut_id, ut_cfg, None
        return None, None, None  # No template match — will fall through to default MoE mode
    tmpl_id      = matched["id"]
    allowed      = user_perms.get("expert_template", [])
    if tmpl_id not in allowed and tmpl_id not in owned_tmpls:
        return tmpl_id, matched, JSONResponse(status_code=403, content={
            "error": f"Template '{model_name}' is not authorized for this API key"
        })
    return tmpl_id, matched, None


async def _ollama_internal_stream(
    user_ctx: dict,
    model_name: str,
    oai_messages: list,
    extra_params: dict,
):
    """Async generator: yields raw SSE lines from the MoE pipeline for an Ollama request.

    Performs full template resolution and calls stream_response() directly,
    identical to /v1/chat/completions after auth, so no pipeline logic is duplicated.
    """
    chat_id   = f"chatcmpl-{uuid.uuid4()}"
    user_id   = user_ctx.get("user_id", "anon")
    api_key_id = user_ctx.get("key_id", "")
    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))

    tmpl_id, matched_tmpl, err = await _ollama_resolve_template(
        model_name, user_perms, user_ctx.get("user_templates_json", "{}")
    )
    if err is not None:
        yield f"data: {json.dumps({'error': 'template_not_authorized'})}\n\n"
        return

    _user_tmpls_json = user_ctx.get("user_templates_json", "{}")
    _user_conns_json = user_ctx.get("user_connections_json", "{}")
    user_experts  = _resolve_user_experts(
        user_ctx.get("permissions_json", ""), override_tmpl_id=tmpl_id,
        user_templates_json=_user_tmpls_json, admin_override=False,
        user_connections_json=_user_conns_json) or {}
    tmpl_prompts  = _resolve_template_prompts(
        user_ctx.get("permissions_json", ""), override_tmpl_id=tmpl_id,
        user_templates_json=_user_tmpls_json, admin_override=False,
        user_connections_json=_user_conns_json)

    mode = _MODEL_ID_TO_MODE.get(model_name, "default")
    if matched_tmpl and matched_tmpl.get("force_think") and mode == "default":
        mode = "agent_orchestrated"

    system_msgs   = [m for m in oai_messages if m.get("role") == "system"]
    system_prompt = system_msgs[0]["content"] if system_msgs else ""

    user_msgs = [m for m in oai_messages if m.get("role") in ("user", "assistant")]
    last_user = next((m for m in reversed(oai_messages) if m.get("role") == "user"), None)
    user_input = last_user["content"] if last_user else ""
    if isinstance(user_input, list):
        user_input = " ".join(p.get("text", "") for p in user_input if isinstance(p, dict))

    _user_images: list = []
    if last_user:
        raw_content = last_user.get("content", "")
        if isinstance(raw_content, list):
            _user_images = _m._extract_oai_images(raw_content)

    raw_history = [
        {"role": m.get("role"), "content": m.get("content", "") if isinstance(m.get("content"), str) else ""}
        for m in oai_messages
        if m.get("role") in ("user", "assistant") and m is not last_user
    ]
    _hist_turns = tmpl_prompts.get("history_max_turns", 0) or None
    _hist_chars = tmpl_prompts.get("history_max_chars", 0) or None
    history = _m._truncate_history(raw_history, max_turns=_hist_turns, max_chars=_hist_chars)

    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=user_id, model=model_name,
        moe_mode=mode, req_type="streaming", template_name=model_name,
        client_ip="", backend_model="", backend_host="", api_key_id=api_key_id,
    ))

    async for sse_line in stream_response(
        user_input, chat_id, mode,
        chat_history=history,
        system_prompt=system_prompt,
        user_id=user_id,
        api_key_id=api_key_id,
        user_permissions=user_perms,
        user_experts=user_experts,
        planner_prompt=tmpl_prompts["planner_prompt"],
        judge_prompt=tmpl_prompts["judge_prompt"],
        judge_model_override=tmpl_prompts["judge_model_override"],
        judge_url_override=tmpl_prompts["judge_url_override"],
        judge_token_override=tmpl_prompts["judge_token_override"],
        planner_model_override=tmpl_prompts["planner_model_override"],
        planner_url_override=tmpl_prompts["planner_url_override"],
        planner_token_override=tmpl_prompts["planner_token_override"],
        model_name=model_name,
        pending_reports=[],
        images=_user_images,
        session_id=None,
        max_agentic_rounds=tmpl_prompts.get("max_agentic_rounds", 0),
        no_cache=False,
    ):
        yield sse_line


async def ollama_version():
    """Ollama version stub — clients use this to detect Ollama compatibility."""
    return {"version": "0.6.0"}


async def ollama_tags(raw_request: Request):
    """Return templates visible to this API key in Ollama model-list format."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _m._read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    # Append user-owned templates (stored per-user in Valkey, not in admin DB)
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


async def ollama_ps(raw_request: Request):
    """Return templates as 'loaded' models (no real VRAM tracking in MoE)."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    user_ctx = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})

    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    templates  = _m._read_expert_templates()
    allowed    = user_perms.get("expert_template")
    now        = _ollama_now()
    expires    = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")

    visible = list(templates if allowed is None else [t for t in templates if t.get("id") in allowed])
    # Append user-owned templates (stored per-user in Valkey, not in admin DB)
    _ut_ps: dict = {}
    try:
        _ut_ps = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    _vis_names_ps = {t.get("name", t["id"]) for t in visible}
    for _uid_ps, _ucfg_ps in _ut_ps.items():
        _m_ps = {"id": _uid_ps, **_ucfg_ps}
        if _m_ps.get("name", _uid_ps) not in _vis_names_ps:
            visible.append(_m_ps)
    models = []
    for t in visible:
        entry = _ollama_model_entry(t, now_iso=now)
        entry["expires_at"] = expires
        entry["size_vram"]  = 0
        models.append(entry)
    return {"models": models}


async def ollama_show(raw_request: Request):
    """Return template details in Ollama modelinfo format (no auth required for show)."""
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    model_name = body.get("model", body.get("name", ""))
    templates  = _m._read_expert_templates()
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
                continue  # skip SSE keep-alives and empty lines
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
            "model":           model,
            "created_at":      _ollama_now(),
            "message":         {"role": "assistant", "content": ""},
            "done":            True,
            "done_reason":     "stop",
            "total_duration":  0,
            "eval_count":      total_tokens,
        }) + "\n"

    if stream:
        return StreamingResponse(_ndjson_stream(), media_type="application/x-ndjson")

    # Non-streaming: collect all content, return single response object
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
        "model":       model,
        "created_at":  _ollama_now(),
        "message":     {"role": "assistant", "content": "".join(content_parts)},
        "done":        True,
        "done_reason": "stop",
    }


async def ollama_generate(raw_request: Request):
    """Ollama /api/generate — single-turn prompt, routed as chat through the MoE pipeline.

    Response uses key 'response' (not 'message.content') per Ollama spec.
    """
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
                    "model":      model,
                    "created_at": _ollama_now(),
                    "response":   content,
                    "done":       False,
                }) + "\n"
        yield json.dumps({
            "model":           model,
            "created_at":      _ollama_now(),
            "response":        "",
            "done":            True,
            "done_reason":     "stop",
            "total_duration":  0,
            "eval_count":      total_tokens,
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
        "model":       model,
        "created_at":  _ollama_now(),
        "response":    "".join(content_parts),
        "done":        True,
        "done_reason": "stop",
    }


async def ollama_pull(raw_request: Request):
    """Fake pull-progress stream — MoE models are managed via Admin UI, not downloaded."""
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    do_stream = body.get("stream", True)
    if not do_stream:
        return {"status": "success"}

    async def _progress():
        for status in ["pulling manifest", "verifying sha256 digest", "writing manifest", "success"]:
            yield json.dumps({"status": status}) + "\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(_progress(), media_type="application/x-ndjson")


async def ollama_delete():
    """Model deletion is not supported — managed via Admin UI."""
    return JSONResponse(status_code=400, content={
        "error": "Model deletion is managed via Admin UI"
    })


async def ollama_not_supported():
    """Stub for Ollama endpoints not supported by MoE Sovereign."""
    return JSONResponse(status_code=400, content={
        "error": "Not supported by MoE Sovereign"
    })


# ─── End of OLLAMA COMPATIBILITY API ─────────────────────────────────────────


# Converts OpenAI Responses API (/v1/responses) to Chat Completions internally.
# Required by Codex CLI (wire_api = "responses").

class _ResponsesRequest(BaseModel):
    model: str
    input: Any                        # str or list of {role, content} items
    instructions: Optional[str] = None
    stream: bool = False
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    previous_response_id: Optional[str] = None
    # passthrough fields silently accepted
    tools: Optional[Any] = None
    text: Optional[Any] = None
    include: Optional[Any] = None
    background: Optional[bool] = None
    context_management: Optional[Any] = None
    max_agentic_rounds: Optional[int] = None
    no_cache: bool = False


def _flatten_responses_content(content: Any) -> str:
    """Flatten Responses API content blocks to a plain string for Ollama."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("text", "input_text", "output_text"):
                    parts.append(block.get("text", ""))
                elif btype == "input_file":
                    fname = block.get("filename", "attachment")
                    fdata = block.get("file_data") or block.get("text", "")
                    if fdata:
                        parts.append(f"\n\n--- {fname} ---\n{fdata}\n---")
                elif btype == "image_url":
                    pass  # text-only experts skip images
                else:
                    text = block.get("text", "") or str(block.get("value", ""))
                    if text:
                        parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _responses_input_to_messages(inp: Any, instructions: Optional[str]) -> list:
    """Convert Responses API input to Chat Completions messages list.

    Handles both simple strings and the richer Responses API format:
    items may be plain message dicts, type='message' wrappers, or direct
    text/file content blocks. Content blocks are flattened to strings so
    downstream Ollama experts receive only plain text.
    """
    messages: list = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            itype = item.get("type", "")
            # type="message" wrapper or plain role/content dict
            if itype == "message" or "role" in item:
                role = item.get("role", "user")
                if role == "developer":
                    role = "system"
                content = _flatten_responses_content(item.get("content", ""))
                if content:
                    messages.append({"role": role, "content": content})
            elif itype in ("input_text", "text"):
                # top-level text item without role wrapper → treat as user
                text = item.get("text", "")
                if text:
                    messages.append({"role": "user", "content": text})
    return messages


def _chat_completion_to_responses(chat_resp: dict, response_id: str) -> dict:
    """Convert a Chat Completions response dict to Responses API format."""
    choice = (chat_resp.get("choices") or [{}])[0]
    message = choice.get("message", {})
    content_text = message.get("content", "") or ""
    finish = choice.get("finish_reason", "stop")
    status = "completed" if finish in ("stop", "length", None) else "incomplete"
    usage = chat_resp.get("usage", {})
    ts = int(time.time())
    return {
        "id": response_id,
        "object": "response",
        "created_at": ts,
        "status": status,
        "model": chat_resp.get("model", ""),
        "output": [
            {
                "id": f"msg_{response_id}",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": content_text, "annotations": []}
                ],
            }
        ],
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


async def _invoke_pipeline_for_responses(
    raw_request: Request,
    request: "_ResponsesRequest",
    messages: list,
) -> tuple[str, int, int]:
    """Call the MoE pipeline directly and return (full_text, prompt_tokens, completion_tokens).

    Consumes stream_response() as an async generator so no HTTP self-call is needed.
    Collects only the final synthesised chat.completion chunk; skips all
    status/progress/debug delta lines emitted by the pipeline.
    """
    raw_key = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "missing_key"}
    if "error" in user_ctx:
        return "", 0, 0

    user_id = user_ctx.get("user_id", "anon")
    api_key_id = user_ctx.get("key_id", "")
    chat_id = f"chatcmpl-{uuid.uuid4()}"

    # Separate system messages from conversation history
    sys_msgs = [m for m in messages if m["role"] == "system"]
    conv = [m for m in messages if m["role"] != "system"]
    system_prompt = sys_msgs[-1]["content"] if sys_msgs else ""
    user_input = ""
    history: list = []
    if conv:
        *history, last = conv
        user_input = last.get("content", "") if isinstance(last.get("content"), str) else ""

    mode = _MODEL_ID_TO_MODE.get(request.model, "default")
    _tp = _resolve_template_prompts(
        user_ctx.get("permissions_json", ""),
        override_tmpl_id=request.model if request.model and request.model != "moe-orchestrator" else None,
        user_templates_json=user_ctx.get("user_templates_json", "{}"),
        admin_override=False,
        user_connections_json=user_ctx.get("user_connections_json", "{}"),
    )
    _max_rounds = (
        request.max_agentic_rounds
        if request.max_agentic_rounds is not None
        else _tp.get("max_agentic_rounds", 0)
    )

    full_text = ""
    prompt_tokens = 0
    completion_tokens = 0
    _text_parts: list[str] = []
    _in_think = False  # skip everything between <think> and </think> (pipeline internals)
    try:
        async for sse_line in stream_response(
            user_input=user_input,
            chat_id=chat_id,
            mode=mode,
            chat_history=history,
            system_prompt=system_prompt,
            user_id=user_id,
            api_key_id=api_key_id,
            planner_prompt=_tp.get("planner_prompt", ""),
            judge_prompt=_tp.get("judge_prompt", ""),
            judge_model_override=_tp.get("judge_model_override", ""),
            judge_url_override=_tp.get("judge_url_override", ""),
            judge_token_override=_tp.get("judge_token_override", ""),
            planner_model_override=_tp.get("planner_model_override", ""),
            planner_url_override=_tp.get("planner_url_override", ""),
            planner_token_override=_tp.get("planner_token_override", ""),
            model_name=request.model,
            session_id=_extract_session_id(raw_request),
            max_agentic_rounds=_max_rounds,
            no_cache=request.no_cache,
        ):
            if not isinstance(sse_line, str) or not sse_line.startswith("data:"):
                continue
            payload = sse_line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            # Usage chunk has choices=[] — extract token counts
            u = chunk.get("usage") or {}
            if u.get("prompt_tokens"):
                prompt_tokens = u["prompt_tokens"]
            if u.get("completion_tokens"):
                completion_tokens = u["completion_tokens"]
            # Content chunks carry delta.content — skip pipeline internals inside <think>
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            piece = delta.get("content", "")
            if not piece:
                continue
            if "<think>" in piece:
                _in_think = True
                continue
            if "</think>" in piece:
                _in_think = False
                continue
            if not _in_think:
                _text_parts.append(piece)
        full_text = "".join(_text_parts)
    except Exception as _pe:
        logger.warning("Responses API pipeline error: %s", _pe)

    return full_text, prompt_tokens, completion_tokens


async def _stream_responses_api(
    raw_request: Request,
    request: "_ResponsesRequest",
    response_id: str,
) -> AsyncGenerator[str, None]:
    """Stream Responses API SSE events matching OpenAI spec exactly.

    Uses sequence_number, output_index, content_index as required by Codex CLI.
    Sends keepalive SSE comments every 15 s while the pipeline runs.
    """
    messages = _responses_input_to_messages(request.input, request.instructions)
    ts = int(time.time())
    item_id = f"msg_{response_id}"
    seq = 0

    def _ev(event_type: str, data: dict) -> str:
        nonlocal seq
        data["sequence_number"] = seq
        seq += 1
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    yield _ev("response.created", {
        "type": "response.created",
        "response": {"id": response_id, "object": "response", "status": "in_progress",
                     "created_at": ts, "output": []},
    })
    yield _ev("response.output_item.added", {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {"id": item_id, "type": "message", "role": "assistant",
                 "status": "in_progress", "content": []},
    })
    yield _ev("response.content_part.added", {
        "type": "response.content_part.added",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": ""},
    })

    # Run pipeline as background task; send keepalives every 15 s while waiting
    pipeline_task = asyncio.create_task(
        _invoke_pipeline_for_responses(raw_request, request, messages)
    )
    while not pipeline_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(pipeline_task), timeout=15.0)
        except asyncio.TimeoutError:
            yield ": keep-alive\n\n"
    full_text, prompt_tokens, completion_tokens = pipeline_task.result()

    for i in range(0, max(len(full_text), 1), 50):
        piece = full_text[i:i + 50]
        if piece:
            yield _ev("response.output_text.delta", {
                "type": "response.output_text.delta",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "delta": piece,
            })

    yield _ev("response.output_text.done", {
        "type": "response.output_text.done",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "text": full_text,
    })
    yield _ev("response.content_part.done", {
        "type": "response.content_part.done",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": full_text, "annotations": []},
    })
    yield _ev("response.output_item.done", {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {"id": item_id, "type": "message", "role": "assistant",
                 "status": "completed",
                 "content": [{"type": "output_text", "text": full_text, "annotations": []}]},
    })
    yield _ev("response.completed", {
        "type": "response.completed",
        "response": {
            "id": response_id, "object": "response", "created_at": ts,
            "status": "completed", "model": request.model,
            "output": [{"id": item_id, "type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": full_text,
                                     "annotations": []}]}],
            "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens,
                      "output_tokens_details": {"reasoning_tokens": 0}},
        },
    })


async def responses_api(raw_request: Request, request: _ResponsesRequest):
    """OpenAI Responses API compatibility endpoint for Codex CLI."""
    response_id = f"resp_{uuid.uuid4().hex}"

    if request.stream:
        return StreamingResponse(
            _stream_responses_api(raw_request, request, response_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming: call pipeline directly (no HTTP self-call to avoid deadlock)
    messages = _responses_input_to_messages(request.input, request.instructions)
    try:
        full_text, prompt_tokens, completion_tokens = await _invoke_pipeline_for_responses(
            raw_request, request, messages
        )
        ts = int(time.time())
        return JSONResponse({
            "id": response_id,
            "object": "response",
            "created_at": ts,
            "status": "completed",
            "model": request.model,
            "output": [
                {
                    "id": f"msg_{response_id}",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": full_text, "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        })
    except Exception as _ie:
        logger.warning("Responses API non-streaming pipeline failed: %s", _ie)
        return JSONResponse(status_code=500, content={"error": {"message": str(_ie)}})



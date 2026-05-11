"""services/pipeline/chat.py — OpenAI-compatible chat completions endpoint."""

import asyncio
import json
import logging
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
import starfleet_config as _starfleet
import mission_context as _mission_context
from parsing import _oai_content_to_str, _extract_oai_images
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
from services.llm_instances import judge_llm, planner_llm, ingest_llm, search
from services.helpers import (
    _log_tool_eval,
    _update_rate_limit_headers, _check_rate_limit_exhausted,
    _conf_format_for_mode, _get_expert_prompt,
    _truncate_history, _apply_semantic_memory,
    _web_search_with_citations,
    _store_response_metadata, _self_evaluate, _neo4j_terms_exist,
    _report,
    _shadow_request, _shadow_lock,
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.inference import _select_node as _select_node_svc, _get_available_models as _get_available_models_svc
from services.skills import _build_skill_catalog, _resolve_skill_secure, _detect_file_skill

logger = logging.getLogger("MOE-SOVEREIGN")
# Pydantic request models (defined here — used by chat_completions and routes)
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: Optional[Any] = None

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
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
    from main import stream_response, _is_openwebui_internal, _handle_internal_direct, _stream_native_llm
    async def _ol_start(*a, **kw): return None   # lineage owned by moe-codex
    async def _ol_complete(*a, **kw): pass
    async def _ol_fail(*a, **kw): pass
    def dataset_user_query(*a, **kw): return {}
    def dataset_response(*a, **kw): return {}
    chat_id    = f"chatcmpl-{uuid.uuid4()}"
    session_id = _extract_session_id(raw_request)
    _ol_run_id = await _ol_start(
        "chat_completion",
        inputs=[dataset_user_query(session_id or "")],
        extra_facets={"requestModel": {"_producer": "https://github.com/h3rb3rn/moe-sovereign",
                                       "_schemaURL": "moe-sovereign://requestModel",
                                       "model": request.model}},
    )

    # IP-based rate limit (pre-auth, guards against credential bruteforce & DoS)
    if not await _check_ip_rate_limit(raw_request):
        return JSONResponse(status_code=429, content={"error": {
            "message": "Rate limit exceeded — too many requests from this IP",
            "type": "rate_limit_error", "code": "rate_limit_exceeded",
        }}, headers={"Retry-After": "60"})

    # Auth
    raw_key  = _extract_api_key(raw_request)
    # ── Diagnostic auth log (remove after debugging missing-API-key issue) ──
    # Never logs the key itself, only its prefix shape and which header it came from.
    _auth_hdr   = raw_request.headers.get("authorization", "")
    _xapi_hdr   = raw_request.headers.get("x-api-key", "")
    _hdr_source = (
        "authorization-bearer" if _auth_hdr.lower().startswith("bearer ") else
        "x-api-key"             if _xapi_hdr else
        "authorization-other"   if _auth_hdr else
        "none"
    )
    _key_prefix = (raw_key or "")[:10]
    _key_len    = len(raw_key or "")
    _is_moe_sk  = bool(raw_key and raw_key.startswith("moe-sk-"))
    _origin_ip  = raw_request.client.host if raw_request.client else "?"
    logger.warning(
        "🔍 chat-auth-debug ip=%s hdr_source=%s key_prefix=%r key_len=%d is_moe_sk=%s model=%r",
        _origin_ip, _hdr_source, _key_prefix, _key_len, _is_moe_sk, request.model,
    )
    # ── End diagnostic block ───────────────────────────────────────────────
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        if user_ctx["error"] == "budget_exceeded":
            return JSONResponse(status_code=429, content={"error": {
                "message": f"Budget exceeded ({user_ctx.get('limit_type', 'unknown')} limit reached)",
                "type": "insufficient_quota", "code": "budget_exceeded"
            }})
        # Diagnostic: surface why _validate_api_key rejected the key
        logger.warning(
            "🔍 chat-auth-rejected ip=%s reason=%s key_len=%d is_moe_sk=%s",
            _origin_ip, user_ctx.get("error", "?"), _key_len, _is_moe_sk,
        )
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
    _all_tmpls = _read_expert_templates()
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
    system_prompt = _oai_content_to_str(system_msgs[0].content) if system_msgs else ""

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
    _user_images = _extract_oai_images(last_user.content) if last_user else []
    allowed_skills = user_perms.get("skill")  # None = all allowed (backwards compatible)
    _raw_user_input = _oai_content_to_str(last_user.content) if last_user else ""
    user_input = await _resolve_skill_secure(_raw_user_input, allowed_skills, user_id=user_id, session_id=session_id)
    # Shadow-Mode: sample every BENCHMARK_SHADOW_RATE-th request to the candidate template.
    # Fire-and-forget — never blocks the production response.
    if BENCHMARK_SHADOW_TEMPLATE:
        import services.helpers as _h
        with _shadow_lock:
            _h._shadow_request_counter += 1
            _fire_shadow = (_h._shadow_request_counter % BENCHMARK_SHADOW_RATE == 0)
        if _fire_shadow:
            asyncio.create_task(_shadow_request(_raw_user_input, user_id, raw_key or ""))
            logger.debug(f"🔬 Shadow request enqueued (counter={_h._shadow_request_counter})")

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
        _avail_models = await _get_available_models_svc(_native_endpoint["node"])
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
        {"role": m.role, "content": _oai_content_to_str(m.content)}
        for m in request.messages
        if m.role in ("user", "assistant") and m != last_user
    ]
    _hist_turns = _tmpl_prompts.get("history_max_turns", 0) or None
    _hist_chars = _tmpl_prompts.get("history_max_chars", 0) or None
    history = _truncate_history(raw_history, max_turns=_hist_turns, max_chars=_hist_chars)
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
        await _ol_complete(_ol_run_id, job_name="chat_completion",
                           outputs=[dataset_response(chat_id)])
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
        _inner = stream_response(user_input, chat_id, mode, chat_history=history,
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
                            no_cache=request.no_cache)

        async def _lineage_wrapped_stream():
            """Wrap the upstream generator so COMPLETE/FAIL fires when streaming ends."""
            try:
                async for _chunk in _inner:
                    yield _chunk
            except Exception as _stream_err:
                await _ol_fail(_ol_run_id, job_name="chat_completion",
                               error=str(_stream_err))
                raise
            else:
                await _ol_complete(_ol_run_id, job_name="chat_completion",
                                   outputs=[dataset_response(chat_id)])

        return StreamingResponse(
            _lineage_wrapped_stream(),
            media_type="text/event-stream",
            headers=_moe_resp_headers or None,
        )
    result = await state.app_graph.ainvoke(
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
    await _ol_complete(_ol_run_id, job_name="chat_completion",
                       outputs=[dataset_response(chat_id)])
    if _moe_resp_headers:
        return JSONResponse(content=resp, headers=_moe_resp_headers)
    return resp

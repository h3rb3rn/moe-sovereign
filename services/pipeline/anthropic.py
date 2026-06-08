"""services/pipeline/anthropic.py — Anthropic Messages API (/v1/messages)."""

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
from config import (
    KAFKA_TOPIC_INGEST, KAFKA_TOPIC_REQUESTS, KAFKA_TOPIC_FEEDBACK,
    CLAUDE_CODE_MODELS, CLAUDE_CODE_TOOL_MODEL, CLAUDE_CODE_TOOL_ENDPOINT,
    CLAUDE_CODE_MODE, CLAUDE_CODE_REASONING_MODEL, CLAUDE_CODE_REASONING_ENDPOINT,
    CLAUDE_CODE_TOOL_CHOICE,
    _CLAUDE_CODE_TOOL_URL, _CLAUDE_CODE_TOOL_TOKEN, _CLAUDE_CODE_REASONING_URL,
    JUDGE_TIMEOUT, EXPERT_TIMEOUT, PLANNER_TIMEOUT,
    JUDGE_MODEL, JUDGE_URL, JUDGE_TOKEN, JUDGE_NUM_CTX,
    PLANNER_MODEL, PLANNER_URL, PLANNER_TOKEN, PLANNER_NUM_CTX,
    URL_MAP, TOKEN_MAP, API_TYPE_MAP, INFERENCE_SERVERS_LIST,
    MODES, _MODEL_ID_TO_MODE, _CLAUDE_PRETTY_NAMES, _model_display_name,
    MAX_GRAPH_CONTEXT_CHARS, MCP_URL, GRAPH_VIA_MCP,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    CACHE_MIN_RESPONSE_LEN, ROUTE_THRESHOLD, ROUTE_GAP,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    CC_HISTORY_COMPRESS_THRESHOLD, CC_HISTORY_COMPRESS_KEEP_TURNS,
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
    CC_SAFETY_BUFFER_TOKENS,
    CC_PREANALYSIS_DELAY_SECS,
    CC_CONTEXT_INDEX_ENABLED,
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
    _log_tool_eval, _tool_eval_logger,
    _update_rate_limit_headers, _check_rate_limit_exhausted,
    _conf_format_for_mode, _get_expert_prompt,
    _truncate_history, _apply_semantic_memory,
    _web_search_with_citations,
    _store_response_metadata, _self_evaluate, _neo4j_terms_exist,
    _report,
    _shadow_request, _shadow_lock,
    _progress_queue,
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.inference import _select_node as _select_node_svc, _get_available_models as _get_available_models_svc
from services.skills import _build_skill_catalog, _resolve_skill_secure
from parsing import (
    _anthropic_content_to_text,
    _extract_images,
    _extract_oai_images,
    _anthropic_to_openai_messages,
    _anthropic_tools_to_openai,
)
from context_budget import (
    get_model_ctx_async as _get_tool_ctx_async,
    get_model_context_window as _get_static_ctx_window,
    CHARS_PER_TOKEN as _CHARS_PER_TOKEN,
)
# CC tool conversations contain code, JSON, shell output and tool results —
# all denser than prose. Use 3 chars/token for input estimation to avoid
# running the total context into num_ctx and getting done_reason=length.
_CC_TOOL_CHARS_PER_TOKEN = 3
from services.pipeline.cc_session import CCSession, _resolve_cc_session

logger = logging.getLogger("MOE-SOVEREIGN")


def _trim_oai_to_budget(oai_msgs: list, available_input_tokens: int) -> tuple:
    """Remove oldest non-system message groups until history fits the token budget.

    Groups are defined by: one non-tool message + all immediately following tool messages.
    This preserves tool_call/tool_result integrity — they are dropped as atomic pairs.
    The last group (current user turn) is never dropped.
    """
    budget_chars = int(available_input_tokens * _CHARS_PER_TOKEN)
    total_chars  = sum(len(json.dumps(m)) for m in oai_msgs)
    if total_chars <= budget_chars:
        return oai_msgs, False

    sys_msgs  = [m for m in oai_msgs if m.get("role") == "system"]
    conv_msgs = [m for m in oai_msgs if m.get("role") != "system"]
    if len(conv_msgs) <= 1:
        return oai_msgs, False

    # Build groups: each starts with a non-tool message, collects trailing tool messages
    groups: list = []
    for msg in conv_msgs:
        if msg.get("role") != "tool":
            groups.append([msg])
        elif groups:
            groups[-1].append(msg)
    if len(groups) <= 1:
        return oai_msgs, False

    sys_chars   = sum(len(json.dumps(m)) for m in sys_msgs)
    kept_groups = list(groups)
    dropped     = False
    while len(kept_groups) > 1:
        cand = sys_chars + sum(len(json.dumps(m)) for g in kept_groups for m in g)
        if cand <= budget_chars:
            break
        kept_groups.pop(0)
        dropped = True

    return sys_msgs + [m for g in kept_groups for m in g], dropped


import hashlib as _hashlib


_CC_WORK_TTL = 4 * 3600  # 4 h — long enough to survive context loss + model reload

# Track active Ollama streaming tasks per CC session so zombie requests from
# previous CC retries can be cancelled when a new request arrives for the same session.
_cc_active_ollama_tasks: dict[str, asyncio.Task] = {}


async def _cc_expert_preanalysis(
    session_id: str,
    user_query: str,
    planner_model: str,
    planner_url: str,
    planner_token: str,
    planner_prompt: str,
    redis_client,
    delay_s: float = 20.0,
) -> None:
    """Background: call the planner with Tier-3 codebase context to pre-analyse the task.

    Waits delay_s seconds so the first tool-call (qwen3.6:35b) completes before
    phi4:14b-fp16 loads — avoids simultaneous VRAM pressure on the same node.

    The planner receives:
      1. The Tier-3 TOC (table-of-contents of the indexed codebase/document)
      2. Semantically relevant Tier-3 chunks for the user's query
    Together these give it full awareness of the codebase without exceeding its
    16k context window, enabling a meaningful plan that references real files.

    The result is stored in cc:work:{session_id}:task_plan and injected on turn 2+.
    """
    if delay_s > 0:
        await asyncio.sleep(delay_s)
    try:
        # ── Build Tier-3 context block for the planner ────────────────────────
        _ctx_block = ""
        try:
            from services.context_index import (
                is_context_indexed as _ctx_indexed,
                get_context_toc as _get_toc,
                retrieve_context_for_task as _ctx_retrieve,
            )
            if await _ctx_indexed(session_id, redis_client):
                _toc = await _get_toc(session_id, redis_client)
                _chunks = await _ctx_retrieve(
                    session_id=session_id,
                    task_text=user_query[:500],
                    redis_client=redis_client,
                    n_results=4,
                )
                if _toc:
                    _ctx_block += f"\n\nCODEBASE OVERVIEW (table of contents):\n{_toc}"
                if _chunks:
                    _ctx_block += f"\n\nRELEVANT CODEBASE SECTIONS:\n{_chunks}"
        except Exception:
            pass

        _sys = (
            planner_prompt
            or "You are a senior software engineer with full access to the codebase index below. "
               "Analyse the task and create a concise step-by-step plan. "
               "Identify: exact file paths involved, tools needed, risks, and the correct execution order. "
               "Reference specific files and functions from the codebase overview. "
               "Be specific and brief — max 500 words."
        )
        _user_content = f"Task:\n{user_query[:2000]}{_ctx_block}"
        _planner_base = planner_url.rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=120.0) as _cl:
            _resp = await _cl.post(
                f"{_planner_base}/v1/chat/completions",
                json={
                    "model":      planner_model,
                    "messages":   [
                        {"role": "system", "content": _sys},
                        {"role": "user",   "content": _user_content},
                    ],
                    "stream":     False,
                    "max_tokens": 800,
                    "extra_body": {"options": {"num_ctx": PLANNER_NUM_CTX or 16384}},
                },
                headers={"Authorization": f"Bearer {planner_token}"},
            )
        if _resp.status_code != 200:
            return
        _plan_text = _resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not _plan_text:
            return

        # Store in cc:work so _inject_cc_work_context picks it up on turn 2+
        _key = f"cc:work:{session_id}"
        _raw = await redis_client.get(_key)
        _work: dict = json.loads(_raw) if _raw else {"steps": [], "files_read": [], "findings": []}
        _work["task_plan"] = _plan_text[:4000]
        await redis_client.set(_key, json.dumps(_work, ensure_ascii=False), ex=_CC_WORK_TTL)
        logger.info(
            "cc_tool: expert pre-analysis stored for session=%s (%d chars, ctx_indexed=%s, model=%s)",
            session_id[:8], len(_plan_text), bool(_ctx_block), planner_model,
        )
    except Exception as _e:
        logger.debug("cc_tool: expert pre-analysis failed: %s", _e)


async def _update_cc_work_summary(
    redis_client,
    session_id: str,
    role: str,
    content: str,
    tool_name: str | None = None,
) -> None:
    """Accumulate structured work-progress in cc:work:{session_id} (Valkey, TTL 4h).

    Called whenever a message is compressed so that the information is not silently
    discarded. On the next request _inject_cc_work_context() can read this back and
    prepend it as a system block so the model knows what was already accomplished.
    """
    if not redis_client or not session_id:
        return
    key = f"cc:work:{session_id}"
    try:
        raw = await redis_client.get(key)
        work: dict = json.loads(raw) if raw else {"steps": [], "files_read": [], "findings": []}

        if role == "tool" and tool_name:
            entry = f"[{tool_name}] {content[:300]}"
            if entry not in work["steps"]:
                work["steps"].append(entry)
            # Track file reads specifically for deduplication
            if tool_name in ("Read", "read_file_chunked") and content and len(content) > 10:
                path_hint = content.split("\n")[0][:120]
                if path_hint not in work["files_read"]:
                    work["files_read"].append(path_hint)
        elif role == "assistant":
            # Extract the first meaningful sentence as a finding
            first = content.strip().split("\n")[0][:200]
            if first and first not in work["findings"]:
                work["findings"].append(first)

        # Cap list sizes to prevent unbounded growth
        work["steps"]     = work["steps"][-30:]
        work["files_read"] = work["files_read"][-20:]
        work["findings"]  = work["findings"][-15:]

        await redis_client.set(key, json.dumps(work, ensure_ascii=False), ex=_CC_WORK_TTL)
    except Exception as _e:
        logger.debug("cc_work: update failed: %s", _e)


async def _inject_cc_work_context(
    oai_msgs: list,
    redis_client,
    session_id: str | None,
) -> list:
    """Prepend a [CC_WORK_CONTEXT] system block if prior work is recorded for this session.

    Injected BEFORE the first user message so the model always knows what it already
    did — even after the history compressor discarded old tool-result turns.
    """
    if not redis_client or not session_id:
        return oai_msgs
    try:
        raw = await redis_client.get(f"cc:work:{session_id}")
        if not raw:
            return oai_msgs
        work = json.loads(raw)
        if not any(work.get(k) for k in ("steps", "files_read", "findings", "task_plan")):
            return oai_msgs

        lines = ["[CC_WORK_CONTEXT — already completed in this session]"]
        if work.get("task_plan"):
            lines.append("[EXPERT ANALYSIS — task plan from specialist model]")
            lines.append(work["task_plan"])
            lines.append("[End of expert analysis]")
        if work.get("files_read"):
            lines.append("Files already read: " + ", ".join(work["files_read"]))
        if work.get("steps"):
            lines.append("Steps completed:")
            for s in work["steps"][-10:]:
                lines.append(f"  • {s}")
        if work.get("findings"):
            lines.append("Key findings:")
            for f in work["findings"][-5:]:
                lines.append(f"  → {f}")
        lines.append("[Do NOT repeat completed steps. Continue from where you left off.]")

        ctx_block = {"role": "system", "content": "\n".join(lines)}
        # Insert after the first system message (if any), before user messages
        sys_idx = next((i for i, m in enumerate(oai_msgs) if m.get("role") == "system"), -1)
        insert_at = sys_idx + 1 if sys_idx >= 0 else 0
        return oai_msgs[:insert_at] + [ctx_block] + oai_msgs[insert_at:]
    except Exception as _e:
        logger.debug("cc_work: inject failed: %s", _e)
        return oai_msgs


async def _compress_history_responses(
    oai_msgs: list,
    redis_client,
    session_id: str | None,
    *,
    threshold: int,
    keep_turns: int,
) -> list:
    """Condense long assistant/tool messages in history and cache originals in Redis.

    Messages older than the last keep_turns turns are compressed when their content
    exceeds threshold chars. The full content is stored in Redis (TTL 3600 s) under
    cc:hist:<session_id>:<sha1[:12]> so it can be retrieved if needed. The last
    keep_turns * 2 non-system messages are always left untouched to preserve
    immediate context.

    When a message is compressed, its key information is also accumulated in
    cc:work:<session_id> via _update_cc_work_summary so the model retains awareness
    of completed steps across context-window resets.
    """
    if not oai_msgs or threshold <= 0:
        return oai_msgs

    non_sys = [i for i, m in enumerate(oai_msgs) if m.get("role") != "system"]
    protected = set(non_sys[-(keep_turns * 2):]) if non_sys else set()

    result: list = []
    compressed_count = 0
    for idx, msg in enumerate(oai_msgs):
        role = msg.get("role", "")
        if idx in protected or role in ("system", "user"):
            result.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= threshold:
            result.append(msg)
            continue

        orig_len = len(content)
        if redis_client and session_id:
            key = f"cc:hist:{session_id}:{_hashlib.sha1(content.encode()).hexdigest()[:12]}"
            try:
                await redis_client.set(key, content, ex=3600)
            except Exception:
                pass
            # Persist work-progress so the model can resume after context loss
            tool_name = msg.get("name") or (
                msg.get("tool_calls", [{}])[0].get("function", {}).get("name")
                if msg.get("tool_calls") else None
            )
            asyncio.create_task(_update_cc_work_summary(
                redis_client, session_id, role, content, tool_name
            ))
            # Store evicted turn in Tier-2 Semantic Memory (ChromaDB) for later retrieval
            try:
                from memory_retrieval import get_memory_store as _get_mem_store_e
                _mem_e = _get_mem_store_e()
                if _mem_e is not None:
                    asyncio.create_task(_mem_e.store_turns(
                        session_id=session_id,
                        turns=[{"role": role, "content": content[:2000]}],
                        base_turn_index=idx,
                    ))
            except Exception:
                pass

        head = content[:800]
        tail = content[-200:] if orig_len > 1000 else ""
        sep  = f"\n[…{orig_len} chars — condensed for context window]\n"
        result.append({**msg, "content": f"{head}{sep}{tail}" if tail else f"{head}{sep}"})
        compressed_count += 1

    if compressed_count:
        logger.info(
            "cc_hist: compressed %d history message(s) above %d chars (session=%s)",
            compressed_count, threshold, session_id or "none",
        )
    return result


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


async def _anthropic_tool_handler(
    body: dict,
    chat_id: str,
    session: CCSession,
    user_id: str = "anon",
    api_key_id: str = "",
    session_id: str | None = None,
):
    """Forwards tool-capable requests to an inference server and converts formats.

    All routing, credential, and prompt-prefix configuration is read from session.
    Tokens are not charged to the MoE budget when session.is_user_conn is True.
    """
    model_id   = body.get("model", "moe-orchestrator-agent")
    messages   = body.get("messages", [])
    # Claude Code sends system as a list of Anthropic content blocks (with cache_control).
    # Normalize to plain string before converting to OpenAI format.
    _system_raw = body.get("system") or ""
    if isinstance(_system_raw, list):
        system = "\n".join(
            b.get("text", "") for b in _system_raw
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        system = _system_raw
    _eff_sys_prefix = session.system_prefix
    if _eff_sys_prefix and system:
        system = f"{_eff_sys_prefix}\n\n{system}"
    elif _eff_sys_prefix:
        system = _eff_sys_prefix
    tools      = body.get("tools", [])
    do_stream  = body.get("stream", False)
    _req_max_tokens = body.get("max_tokens", TOOL_MAX_TOKENS)
    max_tokens = min(_req_max_tokens, session.tool_max_tokens) if session.tool_max_tokens else _req_max_tokens

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

    oai_messages = _anthropic_to_openai_messages(messages, system)

    # ── Tier-3 Context Index: chunk large system_prompt into ChromaDB ────────────
    # When the client ships a large system_prompt (codebase, documents, long files),
    # index it once per session so that every expert can later retrieve only the
    # semantically relevant slice — the infrastructure-level 1M+ context window.
    if CC_CONTEXT_INDEX_ENABLED and session_id and system and state.redis_client:
        try:
            from services.context_index import ensure_indexed as _ensure_ctx
            await _ensure_ctx(session_id, system, state.redis_client)
        except Exception as _cie:
            logger.debug("cc_tool: context indexing skipped: %s", _cie)

    # ── Tier-2 Semantic Memory: store evicted turns + retrieve warm context ─────
    # Evicted turns from _compress_history_responses are embedded in ChromaDB so
    # they can be retrieved semantically — giving Claude Code an effective context
    # that scales beyond the 32k LLM window (the "1M+ infrastructure" layer).
    if CC_CONTEXT_INDEX_ENABLED and session_id:
        try:
            from memory_retrieval import get_memory_store as _get_mem_store
            _mem_store = _get_mem_store()
            if _mem_store is not None:
                # Retrieve semantically relevant past turns for the current query
                _user_query = next(
                    (m.get("content", "") for m in reversed(oai_messages)
                     if m.get("role") == "user" and isinstance(m.get("content"), str)),
                    ""
                )
                if _user_query:
                    _warm_turns = await _mem_store.retrieve_relevant(
                        session_id=session_id,
                        query=_user_query[:500],
                        n_results=5,
                    )
                    if _warm_turns:
                        _warm_block = _mem_store.build_warm_context_block(_warm_turns)
                        _warm_msg = {
                            "role": "system",
                            "content": f"[SEMANTIC MEMORY — relevant past context]\n{_warm_block}\n[End of past context. Continue current task.]"
                        }
                        sys_end = next((i for i, m in enumerate(oai_messages) if m.get("role") != "system"), 0)
                        oai_messages = oai_messages[:sys_end] + [_warm_msg] + oai_messages[sys_end:]
                        logger.info("cc_tool: injected %d Tier-2 memory turns (session=%s)", len(_warm_turns), session_id[:8])
        except Exception as _me:
            logger.debug("cc_tool: Tier-2 memory retrieval skipped: %s", _me)

    # ── Tier-3 Context Retrieval: inject relevant chunks into tool-call messages ─
    # When the session has an indexed context (large system_prompt chunked into
    # ChromaDB), retrieve the semantically relevant slice for the current query
    # and inject it as a system message. This gives the CC tool model access to
    # the full codebase/document context beyond its 32k KV-cache limit.
    if CC_CONTEXT_INDEX_ENABLED and session_id and state.redis_client:
        try:
            from services.context_index import (
                is_context_indexed as _ctx_indexed_t3,
                retrieve_context_for_task as _ctx_retrieve_t3,
            )
            if await _ctx_indexed_t3(session_id, state.redis_client):
                _t3_query = next(
                    (m.get("content", "") for m in reversed(oai_messages)
                     if m.get("role") == "user" and isinstance(m.get("content"), str)),
                    ""
                )
                if _t3_query:
                    _t3_chunks = await _ctx_retrieve_t3(
                        session_id=session_id,
                        task_text=_t3_query[:500],
                        redis_client=state.redis_client,
                    )
                    if _t3_chunks:
                        _t3_msg = {
                            "role": "system",
                            "content": (
                                "[RELEVANT CODEBASE CONTEXT — retrieved for current task]\n"
                                f"{_t3_chunks}\n"
                                "[End of retrieved context. Use this to inform your next action.]"
                            ),
                        }
                        # Insert after all other system messages, just before conversation
                        _sys_end = next(
                            (i for i, m in enumerate(oai_messages) if m.get("role") != "system"), 0
                        )
                        oai_messages = oai_messages[:_sys_end] + [_t3_msg] + oai_messages[_sys_end:]
                        logger.debug(
                            "cc_tool: injected Tier-3 context chunks (%d chars, session=%s)",
                            len(_t3_chunks), session_id[:8],
                        )
        except Exception as _t3e:
            logger.debug("cc_tool: Tier-3 context retrieval skipped: %s", _t3e)

    # ── Expert-Template pre-analysis (first turn only) ────────────────────────
    # On the first user turn (no tool_results yet), fire a background planner call
    # using the expert template's planner_model. The result is stored in
    # cc:work:{session_id} and injected by _inject_cc_work_context on turn 2+.
    # This makes the expert template meaningful for Claude Code without blocking
    # the immediate tool-call response.
    if session_id and _last_msg_type == "tool_use_request" and state.redis_client:
        try:
            _work_key = f"cc:work:{session_id}"
            _existing_work = await state.redis_client.get(_work_key)
            if not _existing_work:
                # First turn: no prior work recorded → trigger expert pre-analysis
                _planner_model   = (session.planner_cfg.get("planner_model_override")   or PLANNER_MODEL   or "")
                _planner_url     = (session.planner_cfg.get("planner_url_override")     or PLANNER_URL     or "")
                _planner_token   = (session.planner_cfg.get("planner_token_override")   or PLANNER_TOKEN   or "ollama")
                _planner_prompt  = session.planner_cfg.get("planner_prompt", "")
                _user_q = next(
                    (m.get("content", "") for m in reversed(oai_messages)
                     if m.get("role") == "user" and isinstance(m.get("content"), str)),
                    "",
                )
                # Skip preanalysis if the planner runs on the same node as the CC tool
                # model — loading the planner model would evict the active CC tool model
                # from VRAM (Ollama evicts LRU on model switch, regardless of free VRAM).
                _cc_tool_base = (session.tool_url or JUDGE_URL or "").rstrip("/").removesuffix("/v1")
                _planner_base_cmp = _planner_url.rstrip("/").removesuffix("/v1")
                _same_node = bool(_planner_url and _planner_base_cmp == _cc_tool_base)
                if _same_node:
                    logger.info(
                        "cc_tool: preanalysis skipped — planner on same node as CC tool model (%s)",
                        _cc_tool_base,
                    )
                if _planner_model and _planner_url and _user_q and not _same_node:
                    # Resolve per-server model_load_delay; fall back to global default
                    _preanalysis_delay: float = float(CC_PREANALYSIS_DELAY_SECS)
                    _planner_base = _planner_url.rstrip("/").removesuffix("/v1")
                    _planner_srv = next(
                        (s for s in INFERENCE_SERVERS_LIST
                         if s.get("url", "").rstrip("/") == _planner_base),
                        None,
                    )
                    if _planner_srv and _planner_srv.get("model_load_delay") is not None:
                        _preanalysis_delay = float(_planner_srv["model_load_delay"])
                    asyncio.create_task(_cc_expert_preanalysis(
                        session_id=session_id,
                        user_query=_user_q,
                        planner_model=_planner_model,
                        planner_url=_planner_url,
                        planner_token=_planner_token,
                        planner_prompt=_planner_prompt,
                        redis_client=state.redis_client,
                        delay_s=_preanalysis_delay,
                    ))
        except Exception as _pae:
            logger.debug("cc_tool: expert pre-analysis skipped: %s", _pae)

    oai_messages = await _compress_history_responses(
        oai_messages, state.redis_client, session_id,
        threshold=CC_HISTORY_COMPRESS_THRESHOLD,
        keep_turns=CC_HISTORY_COMPRESS_KEEP_TURNS,
    )
    oai_messages = await _inject_cc_work_context(
        oai_messages, state.redis_client, session_id,
    )
    oai_tools    = _anthropic_tools_to_openai(tools) if tools else None

    effective_model = session.tool_model or JUDGE_MODEL
    effective_url   = session.tool_url   or JUDGE_URL
    effective_token = session.tool_token or JUDGE_TOKEN
    effective_node  = session.tool_endpoint or "unknown"
    _node_timeout   = float(session.tool_timeout)

    # Context budget guard: trim history and cap max_tokens so input + output ≤ ctx_window.
    # Fetched from Redis cache (TTL 3600s) — negligible overhead on warm path.
    # Use the system token for model-info lookup: user tokens often lack /v1/models access
    # on LiteLLM-backed providers (e.g. AIHUB returns 401 for user keys on that endpoint).
    _info_token = TOKEN_MAP.get(effective_node, "") or effective_token
    _tool_ctx = await _get_tool_ctx_async(
        effective_model, effective_url, _info_token, state.redis_client
    )
    # For Ollama endpoints: the loaded num_ctx may be smaller than the model's native
    # context window (e.g. 8192 default vs 32768 for qwen3-coder:30b).  Claude Code's
    # 26-tool schemas alone consume ~7 800 tokens, leaving only 1 output token at 8192.
    # Store the desired num_ctx now so (a) the budget guard uses the correct window and
    # (b) we can inject it into the Ollama payload later.
    _ollama_num_ctx: int = 0
    if API_TYPE_MAP.get(effective_node, "ollama") == "ollama":
        # Derive num_ctx from the CC profile's tool_max_tokens.
        # When JUDGE_NUM_CTX is set, cap at that value so the CC tool model and
        # the judge singleton are loaded at the same num_ctx — this prevents Ollama
        # from reloading the model between every judge call and every tool call
        # (a reload cycle that can take 2-3 min for 35B models and causes 502s).
        # Prefer the CC profile's explicit num_ctx; fall back to JUDGE_NUM_CTX so the
        # model loads with the same context window as the warmup call.  Never fall back
        # to max_tokens — that is the max OUTPUT tokens, not the context window size.
        # Using max_tokens as num_ctx would force a model reload every CC request
        # (e.g. from warmup 32768 → request 8192) which takes 60-90 s at 35 B params.
        _profile_ctx = session.tool_max_tokens or JUDGE_NUM_CTX or 32768
        _ollama_num_ctx = _profile_ctx
        if JUDGE_NUM_CTX > 0 and _ollama_num_ctx > JUDGE_NUM_CTX:
            _ollama_num_ctx = JUDGE_NUM_CTX
        # Never downgrade a warm model: if Ollama already has the model loaded with a
        # larger context window, reuse that window instead of forcing a costly reload.
        # A model loaded at 32768 can serve any request that needs ≤32768 tokens.
        _ollama_base = effective_url.rstrip("/").removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                _ps_r = await _ps_cl.get(
                    f"{_ollama_base}/api/ps",
                    headers={"Authorization": f"Bearer {effective_token}"},
                )
                for _loaded in _ps_r.json().get("models", []):
                    _lname = _loaded.get("name", "").split(":")[0]
                    _ename = effective_model.split(":")[0]
                    _loaded_ctx = _loaded.get("context_length", 0)
                    if _lname == _ename and _loaded_ctx >= _ollama_num_ctx:
                        logger.info(
                            "cc_tool: reusing warm model ctx=%d (requested %d, no reload needed, model=%s)",
                            _loaded_ctx, _ollama_num_ctx, effective_model,
                        )
                        _ollama_num_ctx = _loaded_ctx
                        break
        except Exception:
            pass  # non-fatal — fall through to the configured num_ctx
        if _ollama_num_ctx != (_tool_ctx or 0):
            logger.info(
                "cc_tool: Ollama num_ctx=%d (from CC profile tool_max_tokens, model=%s)",
                _ollama_num_ctx, effective_model,
            )
            _tool_ctx = _ollama_num_ctx
    if _tool_ctx > 0:
        _CC_SAFETY_BUFFER = CC_SAFETY_BUFFER_TOKENS
        _avail_input = _tool_ctx - max_tokens - _CC_SAFETY_BUFFER
        oai_messages, _history_trimmed = _trim_oai_to_budget(oai_messages, _avail_input)
        if _history_trimmed:
            logger.info(
                "cc_tool: trimmed message history to fit ctx_window=%d (max_out=%d, model=%s)",
                _tool_ctx, max_tokens, effective_model,
            )
        # Cap max_tokens: prevent output overflow even after trimming.
        # Use _CC_TOOL_CHARS_PER_TOKEN=3 (not the prose default of 4) because CC
        # conversations are code-heavy — tool results, JSON, shell output tokenize
        # at ~3 chars/token. Underestimating input causes num_ctx to be hit mid-response.
        _input_est_tok = sum(len(json.dumps(m)) for m in oai_messages) / _CC_TOOL_CHARS_PER_TOKEN
        _safe_max_out  = max(256, _tool_ctx - int(_input_est_tok) - _CC_SAFETY_BUFFER)
        if max_tokens > _safe_max_out:
            logger.info(
                "cc_tool: capping max_tokens %d → %d (ctx=%d, est_input=%d tok, model=%s)",
                max_tokens, _safe_max_out, _tool_ctx, int(_input_est_tok), effective_model,
            )
            max_tokens = _safe_max_out

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
            "auto" if (_has_tool_results and not tools) else (session.tool_choice or CLAUDE_CODE_TOOL_CHOICE)
        )
        payload["tool_choice"] = _effective_tool_choice
    else:
        _effective_tool_choice = "auto"

    # For Ollama nodes use the native /api/chat endpoint instead of /v1/chat/completions.
    # The OpenAI-compatible endpoint silently discards the "options" dict (including
    # num_ctx), so per-request context expansion only works on the native API.
    _use_ollama_native = _ollama_num_ctx > 0
    if _use_ollama_native:
        _ollama_base = effective_url.rstrip("/").removesuffix("/v1")
        _call_url = f"{_ollama_base}/api/chat"

        def _normalize_for_ollama(msg: dict) -> dict:
            """Normalize one OpenAI-format message for Ollama's native /api/chat.

            Key differences between OpenAI and Ollama native format:
            - content must be a string (not an array of content blocks)
            - tool messages must not carry 'tool_call_id' (unsupported field)
            - assistant tool_calls: arguments must be a dict, not a JSON string
            - assistant tool_calls: 'id' and 'type' fields should be removed
            """
            role    = msg.get("role", "")
            content = msg.get("content")
            # Flatten content arrays to plain string
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ) or ""
            out = {**msg, "content": content or ""}
            # tool messages: strip tool_call_id (unsupported in Ollama native)
            if role == "tool":
                out.pop("tool_call_id", None)
            # assistant messages: convert tool_calls to Ollama native format
            # OpenAI: [{"id": "...", "type": "function", "function": {"name": "...", "arguments": "{...}"}}]
            # Ollama: [{"function": {"name": "...", "arguments": {...}}}]  (arguments = dict, not string)
            if role == "assistant" and out.get("tool_calls"):
                _native_tcs = []
                for _tc in out["tool_calls"]:
                    _fn = _tc.get("function", {})
                    _args = _fn.get("arguments", {})
                    if isinstance(_args, str):
                        try:
                            _args = json.loads(_args)
                        except (json.JSONDecodeError, ValueError):
                            _args = {}
                    _native_tcs.append({"function": {"name": _fn.get("name", ""), "arguments": _args}})
                out["tool_calls"] = _native_tcs
            return out

        _ollama_messages = [_normalize_for_ollama(m) for m in oai_messages]
        # Use streaming when CC requests it so tokens flow back immediately,
        # preventing CC connection timeouts while the model generates.
        # Treat "stream": null (CC retry) identically to "stream": true — Ollama must
        # stream tokens so the first byte arrives after model-load (~90 s), not after
        # full generation (load + gen > 120 s httpx timeout → false timeout on retries).
        _do_ollama_stream = body.get("stream") is not False
        _call_payload: dict = {
            "model":      effective_model,
            "messages":   _ollama_messages,
            "stream":     _do_ollama_stream,
            "keep_alive": "4h",
            "options":    {"num_ctx": _ollama_num_ctx, "num_predict": max_tokens},
        }
        if oai_tools:
            _call_payload["tools"] = oai_tools
        # Use Ollama's native think:false flag (Ollama ≥0.30) to disable thinking mode.
        # The previous /no_think prefix approach was unreliable — qwen3.6:35b ignored it
        # and consumed the entire num_predict budget on thinking, returning empty content.
        # think:false is a top-level parameter (not inside options) per Ollama 0.30 API.
        if not session.stream_think:
            _call_payload["think"] = False
        logger.info(
            "cc_tool: Ollama native /api/chat — num_ctx=%d num_predict=%d model=%s think=%s",
            _ollama_num_ctx, max_tokens, effective_model, session.stream_think,
        )
    else:
        _call_url = f"{effective_url}/chat/completions"
        _call_payload = payload

    # Pre-check: if this endpoint is known to be rate-limited, fail fast instead of timing out.
    # This prevents Claude Code CLI from making 10 retry attempts and risking a DDoS ban.
    _tool_ep = CLAUDE_CODE_TOOL_ENDPOINT if (effective_url == _CLAUDE_CODE_TOOL_URL) else None
    if _tool_ep and _check_rate_limit_exhausted(_tool_ep):
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

    # ── Live-streaming path (Ollama native, CC streaming request) ─────────────
    # When Claude Code requests a streaming response AND the endpoint is Ollama
    # native, pipe tokens directly back to CC as they arrive. This prevents CC
    # from timing out while waiting for a complete non-streaming Ollama response
    # (which can take 60–120 s for complex tool-call reasoning at 35B).
    logger.info(
        "cc_tool: routing — ollama_native=%s stream=%s session=%s",
        _use_ollama_native, body.get("stream"), session_id[:8] if session_id else "?",
    )
    # CC retries send "stream": null instead of true — treat None as True for Ollama native.
    # Non-streaming Ollama calls always timeout CC anyway (full generation before first byte).
    if _use_ollama_native and body.get("stream") is not False:
        logger.info("cc_tool: entering live streaming path (model=%s stream=%s)", effective_model, body.get("stream"))
        _model_id_out = body.get("model", effective_model)

        async def _live_ollama_sse():
            _text_acc: str = ""
            _tool_calls_acc: list = []
            _sent_text_block: bool = False
            _block_idx: int = 0
            _in_tok: int = 0
            _out_tok: int = 0
            _stream_error: str = ""

            # Emit message_start immediately — CC sees the stream open and won't retry
            yield _sse_event("message_start", {
                "type": "message_start",
                "message": {
                    "id": chat_id, "type": "message", "role": "assistant",
                    "content": [], "model": _model_id_out,
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 1},
                },
            })
            yield _sse_event("ping", {"type": "ping"})

            # Bridge Ollama streaming and SSE via a queue so we can send keep-alive
            # SSE comment lines while waiting for the model to load (up to 90s for
            # 35B models). Without this, CC would see silence and disconnect.
            _q: asyncio.Queue = asyncio.Queue()

            async def _ollama_fetch() -> None:
                try:
                    async with httpx.AsyncClient(timeout=_node_timeout) as _scl:
                        async with _scl.stream(
                            "POST", _call_url, json=_call_payload,
                            headers={"Authorization": f"Bearer {effective_token}"},
                        ) as _sr:
                            if _sr.status_code == 400:
                                await _q.put(("error", f"Ollama 400: {(await _sr.aread()).decode()[:200]}"))
                            else:
                                async for _ln in _sr.aiter_lines():
                                    await _q.put(("line", _ln))
                except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError) as _te:
                    await _q.put(("error", f"timeout after {_node_timeout}s"))
                except Exception as _se:
                    await _q.put(("error", str(_se)))
                finally:
                    await _q.put(("done", None))

            # Cancel zombie task from a previous CC retry for the same session before
            # starting a new fetch. Without this, stale Ollama requests keep running and
            # queue up, blocking the next CC request from ever getting a first token.
            if session_id:
                _old_task = _cc_active_ollama_tasks.get(session_id)
                if _old_task and not _old_task.done():
                    _old_task.cancel()
                    logger.info("cc_tool: cancelled zombie Ollama task (session=%s)", session_id[:8])
            _fetch_task = asyncio.create_task(_ollama_fetch())
            if session_id:
                _cc_active_ollama_tasks[session_id] = _fetch_task
                # Compact: drop finished entries so the dict doesn't grow unboundedly
                # across days of uptime with many sessions.
                if len(_cc_active_ollama_tasks) > 50:
                    _done_keys = [k for k, t in _cc_active_ollama_tasks.items() if t.done()]
                    for _dk in _done_keys:
                        _cc_active_ollama_tasks.pop(_dk, None)

            try:
                while True:
                    try:
                        _kind, _val = await asyncio.wait_for(_q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        # Ollama still loading — send SSE comment to keep the CC connection alive
                        yield ": keep-alive\n\n"
                        continue
                    if _kind == "done":
                        break
                    if _kind == "error":
                        _stream_error = _val
                        break
                    _line = _val
                    if not _line.strip():
                        continue
                    try:
                        _chunk = json.loads(_line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    _cmsg = _chunk.get("message", {})
                    _delta = _cmsg.get("content", "") or ""
                    _chunk_tcs = _cmsg.get("tool_calls")
                    _done = _chunk.get("done", False)

                    if _delta:
                        _text_acc += _delta
                        if not _sent_text_block:
                            yield _sse_event("content_block_start", {
                                "type": "content_block_start", "index": 0,
                                "content_block": {"type": "text", "text": ""},
                            })
                            _sent_text_block = True
                        yield _sse_event("content_block_delta", {
                            "type": "content_block_delta", "index": 0,
                            "delta": {"type": "text_delta", "text": _delta},
                        })

                    if _chunk_tcs:
                        _tool_calls_acc = _chunk_tcs

                    if _done:
                        _in_tok = _chunk.get("prompt_eval_count", 0)
                        _out_tok = _chunk.get("eval_count", 0)
            except Exception as _loop_err:
                _stream_error = str(_loop_err)
                logger.warning("cc_tool stream loop error: %s", _loop_err)
            finally:
                # Only remove our own entry — a new CC retry may have already replaced it.
                if session_id and _cc_active_ollama_tasks.get(session_id) is _fetch_task:
                    _cc_active_ollama_tasks.pop(session_id, None)
                if not _fetch_task.done():
                    _fetch_task.cancel()
            if _stream_error and "timeout" in _stream_error.lower():
                PROM_TOOL_TIMEOUTS.labels(node=effective_node, model=effective_model).inc()

            if _stream_error:
                _err_txt = f"⚠️ Inference server '{effective_node}' error: {_stream_error}"
                if not _sent_text_block:
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start", "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    })
                    _sent_text_block = True
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": _err_txt},
                })

            # Close text block if one was opened
            if _sent_text_block:
                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop", "index": _block_idx,
                })
                _block_idx += 1

            # JSON-in-text fallback: detect tool call JSON in accumulated text
            if not _tool_calls_acc and _text_acc and tools and not _stream_error:
                _known = {t.get("name", "") for t in tools}
                _probe = re.sub(r"^```(?:json)?\s*|\s*```$", "", _text_acc.strip(), flags=re.DOTALL).strip()
                try:
                    _parsed = json.loads(_probe)
                    _cands = [_parsed] if isinstance(_parsed, dict) else (_parsed if isinstance(_parsed, list) else [])
                    for _c in _cands:
                        if isinstance(_c, dict):
                            _tn = _c.get("name") or _c.get("tool") or _c.get("function")
                            _ta = _c.get("arguments") or _c.get("parameters") or _c.get("input") or {}
                            if _tn and _tn in _known:
                                _tool_calls_acc.append({"function": {"name": _tn, "arguments": _ta}})
                except (json.JSONDecodeError, ValueError):
                    pass

            # Tool call blocks
            _stop_reason = "end_turn"
            for _ntc in _tool_calls_acc:
                _fn = _ntc.get("function", {})
                _args = _fn.get("arguments", {})
                _tc_id = f"call_{uuid.uuid4().hex[:12]}"
                _args_json = json.dumps(_args) if isinstance(_args, dict) else str(_args)
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": _block_idx,
                    "content_block": {
                        "type": "tool_use", "id": _tc_id,
                        "name": _fn.get("name", ""), "input": {},
                    },
                })
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": _block_idx,
                    "delta": {"type": "input_json_delta", "partial_json": _args_json},
                })
                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop", "index": _block_idx,
                })
                _block_idx += 1
                _stop_reason = "tool_use"

            # Empty response guard
            if not _sent_text_block and not _tool_calls_acc:
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""},
                })
                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop", "index": 0,
                })

            yield _sse_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": _stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": _out_tok},
            })
            yield _sse_event("message_stop", {"type": "message_stop"})

            PROM_TOOL_CALL_DURATION.labels(
                node=effective_node, model=effective_model, phase="llm_call"
            ).observe(time.monotonic() - _llm_t0)
            if _tool_calls_acc:
                PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
            if user_id != "anon":
                asyncio.create_task(_log_usage_to_db(
                    user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                    model=effective_model, moe_mode="cc_tool",
                    prompt_tokens=_in_tok, completion_tokens=_out_tok,
                    session_id=session_id,
                ))
                if not session.is_user_conn:
                    asyncio.create_task(_increment_user_budget(
                        user_id, _in_tok + _out_tok,
                        prompt_tokens=_in_tok, completion_tokens=_out_tok,
                    ))
            asyncio.create_task(_deregister_active_request(chat_id))

        _llm_t0 = time.monotonic()
        return StreamingResponse(_live_ollama_sse(), media_type="text/event-stream")

    # ── Non-streaming fallback (non-Ollama or non-streaming request) ───────────
    _llm_t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_node_timeout) as client:
            resp = await client.post(
                _call_url,
                json=_call_payload,
                headers={"Authorization": f"Bearer {effective_token}"}
            )
            # Parse and cache rate limit headers from the provider response
            if _tool_ep:
                _update_rate_limit_headers(_tool_ep, resp.headers, resp.status_code)
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
            # Ollama native /api/chat returns 400 on certain message structures
            # (e.g. complex tool schemas, unsupported fields). Fall back to the
            # OpenAI-compatible endpoint so Claude Code still receives a response.
            if resp.status_code == 400 and _use_ollama_native:
                logger.warning(
                    "cc_tool: Ollama native /api/chat 400 — falling back to /v1/chat/completions "
                    "(error: %s)", resp.text[:200],
                )
                _fallback_url = f"{effective_url}/chat/completions"
                resp = await client.post(
                    _fallback_url,
                    json=payload,                   # original OpenAI-format payload
                    headers={"Authorization": f"Bearer {effective_token}"},
                )
                _use_ollama_native = False          # parse response as OpenAI format
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
        # Capture response body (first 2000 chars) to surface provider error details in logs
        try:
            _err_body_raw = _hex.response.text[:2000]
        except Exception:
            _err_body_raw = "<unreadable>"
        logger.warning(
            "⚠️ Tool handler HTTP error %s from %s: %s | provider_body: %s",
            _status_code, effective_node, _hex, _err_body_raw,
        )
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "http_error",
            "status_code": _status_code, "elapsed_s": round(_llm_latency, 3),
            "provider_error": _err_body_raw[:500],
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

    # Normalize response: Ollama native /api/chat and OpenAI /v1/chat/completions differ.
    if _use_ollama_native:
        # Ollama native: {"message": {"role": ..., "content": ..., "tool_calls": [...]},
        #                 "prompt_eval_count": N, "eval_count": M}
        _native_msg = oai_resp.get("message", {})
        in_tok  = oai_resp.get("prompt_eval_count", 0)
        out_tok = oai_resp.get("eval_count", 0)
        # Convert Ollama tool_calls to OpenAI format (add synthetic id, stringify args)
        _native_tcs = _native_msg.get("tool_calls") or []
        _oai_tcs = []
        for _ntc in _native_tcs:
            _nfn = _ntc.get("function", {})
            _args = _nfn.get("arguments", {})
            _oai_tcs.append({
                "id":   f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name":      _nfn.get("name", "unknown"),
                    "arguments": json.dumps(_args) if isinstance(_args, dict) else str(_args),
                },
            })
        msg = {
            "role":       _native_msg.get("role", "assistant"),
            "content":    _native_msg.get("content") or "",
            "tool_calls": _oai_tcs or None,
        }
    else:
        choice  = oai_resp["choices"][0]
        msg     = choice["message"]
        usage   = oai_resp.get("usage", {})
        in_tok  = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=effective_model, moe_mode="cc_tool",
            prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
        if not session.is_user_conn:
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
            for _match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', _probe):
                try:
                    _json_candidates.append(json.loads(_match.group()))
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
    logger.info(
        "cc_tool: Ollama response — content_len=%d tool_calls=%d done=%s eval=%s",
        len(msg.get("content") or ""),
        len(msg.get("tool_calls") or []),
        oai_resp.get("done_reason", "?"),
        oai_resp.get("eval_count", "?"),
    )
    content_blocks: list = []
    if msg.get("content"):
        content_blocks.append({"type": "text", "text": msg["content"]})
    stop_reason = "end_turn"
    if msg.get("tool_calls"):
        stop_reason = "tool_use"
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", {})
            # Ollama native /api/chat returns arguments as a dict already.
            # OpenAI-compat endpoints return arguments as a JSON string.
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args) if raw_args else {}
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

    # Extract <think>...</think> from text content and surface as thinking blocks
    if session.stream_think:
        _think_blocks: list = []
        _remain_blocks: list = []
        for _blk in content_blocks:
            if _blk.get("type") == "text":
                _txt = _blk.get("text", "")
                _tm = re.search(r"<think>(.*?)</think>", _txt, re.S)
                if _tm:
                    _think_txt = _tm.group(1).strip()
                    _clean_txt = re.sub(r"<think>.*?</think>", "", _txt, flags=re.S).strip()
                    if _think_txt:
                        _think_blocks.append({"type": "thinking", "thinking": _think_txt})
                    if _clean_txt:
                        _remain_blocks.append({"type": "text", "text": _clean_txt})
                else:
                    _remain_blocks.append(_blk)
            else:
                _remain_blocks.append(_blk)
        content_blocks = _think_blocks + _remain_blocks

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


async def _anthropic_reasoning_handler(
    body: dict,
    chat_id: str,
    session: CCSession,
    user_id: str = "anon",
    api_key_id: str = "",
    session_id: str | None = None,
):
    """Text requests via reasoning expert (deepseek-r1/qwq) with <think> parsing.

    Returns responses in Anthropic Extended Thinking format. A thinking block is
    included in the response only when session.stream_think is True.
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

    # Apply system_prompt_prefix from CC profile (same logic as _anthropic_tool_handler)
    if session.system_prefix and system:
        system = f"{session.system_prefix}\n\n{system}"
    elif session.system_prefix:
        system = session.system_prefix

    oai_messages = _anthropic_to_openai_messages(messages, system)
    oai_messages = await _compress_history_responses(
        oai_messages, state.redis_client, session_id,
        threshold=CC_HISTORY_COMPRESS_THRESHOLD,
        keep_turns=CC_HISTORY_COMPRESS_KEEP_TURNS,
    )

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
            node = await _select_node_svc(reasoning_model, best.get("endpoints") or [best.get("endpoint", "")])
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
        "max_tokens": body.get("max_tokens", session.reasoning_max_tokens or REASONING_MAX_TOKENS),
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
    if thinking_text and session.stream_think:
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


async def _anthropic_moe_handler(
    body: dict,
    chat_id: str,
    session: CCSession,
    user_id: str = "anon",
    api_key_id: str = "",
    session_id: str | None = None,
):
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
    allowed_skills = session.user_perms.get("skill")
    _raw_cc_input = _anthropic_content_to_text(last_user_content)
    user_input  = await _resolve_skill_secure(_raw_cc_input, allowed_skills, user_id=user_id, session_id=session_id)
    _cc_pending_reports: List[str] = []
    if user_input != _raw_cc_input:
        _csm = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)", _raw_cc_input)
        _csname = _csm.group(1) if _csm else "?"
        _csargs = _raw_cc_input[len(_csname)+1:].strip() if _csm else ""
        _cc_pending_reports.append(
            f"🎯 Skill /{_csname} resolved (args: '{_csargs}', {len(user_input)} chars):\n{user_input}"
        )
    user_images = _extract_images(last_user_content)
    history_raw = [
        {"role": m["role"],
         "content": _anthropic_content_to_text(m.get("content", ""))}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant")
    ]
    history = _truncate_history(history_raw)
    _sm_team_ids_h: List[str] = []
    _sm_prefs_h:   dict      = {}
    _sm_enable        = session.planner_cfg.get("enable_semantic_memory", False)
    _sm_cross_session = session.planner_cfg.get("enable_cross_session_memory", False)
    _sm_scopes        = session.planner_cfg.get("cross_session_scopes", ["private"])
    if _sm_enable and user_id and user_id != "anon":
        try:
            from admin_ui.database import (
                get_user_teams        as _gut_h,
                get_user_memory_prefs as _gmp_h,
            )
            if _sm_cross_session:
                _sm_team_ids_h = await _gut_h(user_id)
            _sm_prefs_h = await _gmp_h(user_id)
        except Exception:
            pass
    history = await _apply_semantic_memory(
        history_raw, history, user_input, session_id,
        enabled=_sm_enable,
        user_id=user_id,
        team_ids=_sm_team_ids_h,
        cross_session_enabled=_sm_cross_session,
        cross_session_scopes=_sm_scopes,
        n_results=0,   # anthropic handler: use template default (passed via caller)
        ttl_hours=0,
        prefer_fresh=_sm_prefs_h.get("prefer_fresh", False),
        share_with_team=_sm_prefs_h.get("share_with_team", False),
    )

    # ── Tier-3 Context Index: chunk large system_prompt before dispatching to MoE ─
    if CC_CONTEXT_INDEX_ENABLED and session_id and system and state.redis_client:
        try:
            from services.context_index import ensure_indexed as _ensure_ctx_moe
            await _ensure_ctx_moe(session_id, system, state.redis_client)
        except Exception as _cie:
            logger.debug("moe_handler: context indexing skipped: %s", _cie)

    _pcfg = session.planner_cfg
    invoke_state = {
        "input": user_input, "response_id": chat_id,
        "mode": "agent_orchestrated" if session.mode == "moe_orchestrated" else "agent",
        "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
        "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
        "chat_history": history, "reasoning_trace": "", "system_prompt": system,
        "session_id": session_id or "",
        "behavioral_directives": session.system_prefix,
        "images": user_images,
        "user_permissions": session.user_perms,
        "user_experts": session.experts,
        "planner_prompt": _pcfg.get("planner_prompt", ""),
        "judge_prompt":   _pcfg.get("judge_prompt", ""),
        "judge_model_override":   _pcfg.get("judge_model_override", ""),
        "judge_url_override":     _pcfg.get("judge_url_override", ""),
        "judge_token_override":   _pcfg.get("judge_token_override", ""),
        "planner_model_override": _pcfg.get("planner_model_override", ""),
        "planner_url_override":   _pcfg.get("planner_url_override", ""),
        "planner_token_override": _pcfg.get("planner_token_override", ""),
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
                        if state.app_graph is None:
                            result_box["error"] = "Orchestrator graph not ready — retry in a few seconds"
                            return
                        result_box["data"] = await state.app_graph.ainvoke(invoke_state, invoke_cfg)
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
                # When stream_think: open a thinking block first, stream progress into it,
                # then close it and open the text block at the next index.
                _text_block_index = 0
                if session.stream_think:
                    _text_block_index = 1
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start", "index": 0,
                        "content_block": {"type": "thinking", "thinking": ""}
                    })
                    yield _sse_event("ping", {"type": "ping"})

                    while True:
                        try:
                            msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                            if msg is None:
                                break
                            if msg:
                                yield _sse_event("content_block_delta", {
                                    "type": "content_block_delta", "index": 0,
                                    "delta": {"type": "thinking_delta", "thinking": f"{msg}\n"}
                                })
                        except asyncio.TimeoutError:
                            yield ": keep-alive\n\n"

                    yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
                else:
                    # Drain progress queue without emitting (pipeline still uses it internally)
                    while True:
                        try:
                            msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                            if msg is None:
                                break
                        except asyncio.TimeoutError:
                            yield ": keep-alive\n\n"

                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": _text_block_index,
                    "content_block": {"type": "text", "text": ""}
                })
                if not session.stream_think:
                    yield _sse_event("ping", {"type": "ping"})

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
                        "type": "content_block_delta", "index": _text_block_index,
                        "delta": {"type": "text_delta", "text": content[i:i+SSE_CHUNK_SIZE]}
                    })
                    await asyncio.sleep(0.005)

                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": _text_block_index})
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
    if state.app_graph is None:
        return JSONResponse(status_code=503, content={"type": "error", "error": {"type": "api_error", "message": "Orchestrator graph not ready — retry in a few seconds"}})
    result  = await state.app_graph.ainvoke(invoke_state, invoke_cfg)
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
        - tools / tool_results    → judge_llm (magistral:24b)
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
    _ol_run_id = None  # lineage emitted by moe-codex when deployed

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
    _user_id    = user_ctx.get("user_id", "anon")
    _api_key_id = user_ctx.get("key_id", "")

    # ─── Resolve per-request CC session (profile, endpoint, template) ─────────
    _profile_ids = json.loads(user_ctx.get("permissions_json", "") or "{}").get("cc_profile", [])
    session = _resolve_cc_session(user_ctx, _profile_ids)

    if session.profile_not_found:
        logger.warning(
            "CC profile not found for key=%s profiles=%s — returning 422 to suppress retries",
            _api_key_id, _profile_ids,
        )
        return JSONResponse(status_code=422, content={"error": {
            "message": (
                "No active Claude Code profile matched this API key. "
                "Please check your profile configuration in the admin panel."
            ),
            "type": "invalid_request_error",
            "code": "cc_profile_not_found",
        }})

    has_tool_results = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m.get("content", []))
        for m in messages
    )

    # Live monitoring
    _cc_moe_mode = (
        "cc_tool"      if (tools or has_tool_results or session.mode == "native") else
        "cc_reasoning" if session.mode == "moe_reasoning" else
        "cc_moe"
    )
    _cc_backend_model = session.tool_model if _cc_moe_mode != "cc_moe" else "MoE"
    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=_user_id, model=model,
        moe_mode=_cc_moe_mode, req_type="streaming" if body.get("stream") else "batch",
        client_ip=request.client.host if request.client else "",
        backend_model=_cc_backend_model,
        api_key_id=_api_key_id,
    ))

    try:
        # Mode 1: Native or tool/tool_result turns → tool handler (precise function calling)
        if session.mode == "native" or tools or has_tool_results:
            _result = await _anthropic_tool_handler(
                body, chat_id, session, _user_id, _api_key_id, session_id,
            )
        # Mode 2: MoE Reasoning — reasoning expert with <think> parsing and thinking blocks
        elif session.mode == "moe_reasoning":
            _result = await _anthropic_reasoning_handler(
                body, chat_id, session, _user_id, _api_key_id, session_id,
            )
        # Mode 3 + fallback: MoE Orchestrated — full planner, all experts
        else:
            _result = await _anthropic_moe_handler(
                body, chat_id, session, _user_id, _api_key_id, session_id,
            )
        return _result
    except Exception as _exc:
        logger.error("Messages-Endpoint unhandled exception (chat_id=%s): %s", chat_id, _exc, exc_info=True)
        asyncio.create_task(_deregister_active_request(chat_id))
        return JSONResponse(status_code=500, content={"error": {
            "type": "api_error", "message": f"Internal server error: {_exc}"
        }})


# _ONTOLOGY_RUN_KEY and _ONTOLOGY_RUNS_HISTORY_KEY moved to services/healer.py




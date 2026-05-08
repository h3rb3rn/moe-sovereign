"""services/inference.py — Node selection, expert scoring, LLM fallback chain."""

import asyncio
import re
import threading
import time
import random
import os
import logging

import httpx
from langchain_openai import ChatOpenAI

import state
from config import (
    URL_MAP, TOKEN_MAP, API_TYPE_MAP, INFERENCE_SERVERS_LIST,
    EXPERT_MIN_DATAPOINTS, EXPERT_TIER_BOUNDARY_B,
    JUDGE_TIMEOUT, PLANNER_TIMEOUT,
    _FALLBACK_NODE, _FALLBACK_MODEL, _FALLBACK_MODEL_SECOND,
    _FALLBACK_ENABLED, _ENDPOINT_DEGRADED_TTL, _EXTERNAL_ENDPOINT_PATTERNS,
    MAX_EXPERT_OUTPUT_CHARS, THOMPSON_SAMPLING_ENABLED,
)
from metrics import PROM_THOMPSON
from services.routing import _server_info, _is_endpoint_error
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI  # noqa: F811 — type hints only

import main as _m

logger = logging.getLogger("MOE-SOVEREIGN")

# Module-level threading locks
# synchronous dict mutation (e.g. _endpoint_gpu_indices[k] = v) is NOT atomic
# under concurrent asyncio tasks on CPython once the GIL is released between
# byte-code instructions. threading.Lock is the correct primitive here because:
#   (a) all writers run in the same event-loop thread — so Lock.acquire() never
#       blocks the event loop longer than the locked section itself (a few ns);
#   (b) asyncio.Lock would require async with, which is heavier and unnecessary
#       for pure-synchronous dict updates.
_gpu_lock   = threading.Lock()   # guards _endpoint_gpu_indices round-robin index
_cache_lock = threading.Lock()   # guards _model_avail_cache, _ps_cache

# ---------------------------------------------------------------------------
# Model availability cache
# ---------------------------------------------------------------------------

_model_avail_cache: Dict[str, tuple] = {}  # {node: (monotonic_ts, frozenset[model_names])}
_MODEL_AVAIL_TTL = 60.0  # seconds


async def _get_available_models(node: str) -> Optional[frozenset]:
    """Queries available models of a node (60s cache).
    Returns None if the node is unreachable → request is not blocked."""
    now = time.monotonic()
    with _cache_lock:
        if node in _model_avail_cache:
            ts, models = _model_avail_cache[node]
            if now - ts < _MODEL_AVAIL_TTL:
                return models
    url = URL_MAP.get(node, "").rstrip("/")
    token = TOKEN_MAP.get(node, "ollama")
    api_type = API_TYPE_MAP.get(node, "ollama")
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as _c:
            if api_type == "ollama":
                _r = await _c.get(f"{url}/api/tags",
                                  headers={"Authorization": f"Bearer {token}"})
                models = frozenset(m["name"] for m in _r.json().get("models", [])) \
                         if _r.status_code == 200 else None
            else:  # openai-compatible
                _r = await _c.get(f"{url}/v1/models",
                                  headers={"Authorization": f"Bearer {token}"})
                models = frozenset(m["id"] for m in _r.json().get("data", [])) \
                         if _r.status_code == 200 else None
        if models is not None:
            with _cache_lock:
                _model_avail_cache[node] = (now, models)
        return models
    except Exception as _e:
        logger.debug(f"Model availability check failed for {node}: {_e}")
        return None


# ---------------------------------------------------------------------------
# Per-endpoint semaphores and GPU index assignment
# ---------------------------------------------------------------------------

_endpoint_semaphores: Dict[str, asyncio.Semaphore] = {}
_endpoint_gpu_indices: Dict[str, int] = {}

async def _init_semaphores():
    """Create per-endpoint semaphores in the event-loop context, derived from INFERENCE_SERVERS_LIST."""
    global _endpoint_semaphores, _endpoint_gpu_indices
    for s in INFERENCE_SERVERS_LIST:
        name  = s["name"]
        count = int(s.get("gpu_count", 1))
        _endpoint_semaphores[name]  = asyncio.Semaphore(count)
        _endpoint_gpu_indices[name] = 0
    logger.info(f"🎮 GPU semaphores: { {s['name']: s.get('gpu_count', 1) for s in INFERENCE_SERVERS_LIST} }")

async def assign_gpu(endpoint: str = "") -> int:
    srv   = next((s for s in INFERENCE_SERVERS_LIST if s["name"] == endpoint), None)
    count = int(srv["gpu_count"]) if srv else 1
    with _gpu_lock:
        idx = _endpoint_gpu_indices.get(endpoint, 0) % max(count, 1)
        _endpoint_gpu_indices[endpoint] = idx + 1
    return idx


# ---------------------------------------------------------------------------
# Performance key and VRAM unload
# ---------------------------------------------------------------------------

def _perf_key(model: str, category: str) -> str:
    """Valkey key for expert performance: moe:perf:{model}:{category}"""
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", model)
    return f"moe:perf:{safe}:{category}"

async def _ollama_unload(model: str, base_url: str) -> None:
    """Unloads a model immediately from Ollama VRAM via native API (keep_alive=0).
    Fire-and-forget — errors are ignored, pipeline continues."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                f"{base_url}/api/generate",
                json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
            )
        logger.debug(f"🗑️ VRAM: {model} unloaded")
    except Exception as e:
        logger.debug(f"⚠️ VRAM-Unload {model}: {e}")


# ---------------------------------------------------------------------------
# Endpoint degradation tracking
# ---------------------------------------------------------------------------

_degraded_endpoints: dict[str, float] = {}  # url → monotonic timestamp of degraded mark


def _mark_endpoint_degraded(url: str) -> None:
    _degraded_endpoints[url] = time.monotonic()
    logger.warning("⚠️ Endpoint marked degraded (5 min): %s", url)


def _endpoint_is_degraded(url: str) -> bool:
    ts = _degraded_endpoints.get(url)
    if ts is None:
        return False
    if time.monotonic() - ts > _ENDPOINT_DEGRADED_TTL:
        _degraded_endpoints.pop(url, None)
        return False
    return True


async def _get_fallback_llm(timeout: float = 120.0, model: str = "") -> "ChatOpenAI":
    """Return a ChatOpenAI pointing to the configured local fallback node.

    model: override which fallback model to use. Defaults to FALLBACK_MODEL.
           Raises RuntimeError when fallback is not configured (FALLBACK_NODE empty).
    """
    if not _FALLBACK_ENABLED:
        raise RuntimeError(
            "No local fallback configured. Set FALLBACK_NODE and "
            "FALLBACK_MODEL environment variables to enable."
        )
    url = URL_MAP.get(_FALLBACK_NODE)
    if not url:
        raise RuntimeError(
            f"Fallback node '{_FALLBACK_NODE}' is not in the configured "
            "inference servers (INFERENCE_SERVERS env var)."
        )
    token = TOKEN_MAP.get(_FALLBACK_NODE, "ollama")
    return ChatOpenAI(
        model=model or _FALLBACK_MODEL,
        base_url=url,
        api_key=token,
        timeout=timeout,
    )


def _is_external_endpoint_url(url: str) -> bool:
    """Return True when the URL points to an external (non-local) inference endpoint."""
    u = url.lower()
    return any(p and p in u for p in _EXTERNAL_ENDPOINT_PATTERNS)


# ---------------------------------------------------------------------------
# LLM invocation with fallback chain
# ---------------------------------------------------------------------------

async def _invoke_llm_with_fallback(
    primary_llm: "ChatOpenAI",
    primary_url: str,
    prompt,
    timeout: float = 120.0,
    label: str = "LLM",
) -> tuple:
    """Invoke primary_llm; on auth/quota error or empty response, retry with fallback node.

    Handles two failure modes:
    1. Exception (401, 429, connection error) — caught in except block.
    2. Silent empty body (HTTP 200 with no content) — detected after ainvoke returns.

    Returns (result, used_fallback: bool).
    """
    from config import _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY

    _on_external = _is_external_endpoint_url(primary_url)

    async def _try_fallback_node(reason: str, model: str = "") -> tuple:
        """Try the configured fallback node model. Returns (res, True) on success."""
        _mark_endpoint_degraded(primary_url)
        _fb_model = model or _FALLBACK_MODEL
        logger.warning("🔄 %s: %s — falling back to %s@%s",
                       label, reason, _fb_model, _FALLBACK_NODE)
        fb_llm = await _get_fallback_llm(timeout, model=_fb_model)
        fb_res = await fb_llm.ainvoke(prompt)
        return fb_res, True

    async def _try_fallback_chain(reason: str) -> tuple:
        """Try primary fallback model, then second-tier fallback model.

        Does nothing (re-raises) when fallback is not configured via env vars.
        """
        if not _FALLBACK_ENABLED:
            logger.warning("⚠️ %s: %s — no local fallback configured (FALLBACK_NODE/FALLBACK_MODEL not set)",
                           label, reason)
            raise RuntimeError(f"{label} failed and no fallback configured: {reason}")

        try:
            res, used = await _try_fallback_node(reason, _FALLBACK_MODEL)
            logger.info("✅ %s: Fallback (%s@%s) succeeded", label, _FALLBACK_MODEL, _FALLBACK_NODE)
            return res, used
        except Exception as fe1:
            if _FALLBACK_MODEL_SECOND:
                logger.warning("⚠️ %s: Primary fallback (%s) failed: %s — trying %s",
                               label, _FALLBACK_MODEL, str(fe1)[:60], _FALLBACK_MODEL_SECOND)
                try:
                    res2, _ = await _try_fallback_node(reason + " (2nd fallback)", _FALLBACK_MODEL_SECOND)
                    logger.info("✅ %s: Second fallback (%s) succeeded", label, _FALLBACK_MODEL_SECOND)
                    return res2, True
                except Exception as fe2:
                    logger.error("❌ %s: Both fallbacks failed. Last: %s", label, fe2)
                    raise fe2
            logger.error("❌ %s: Fallback (%s) failed, no second fallback configured: %s",
                         label, _FALLBACK_MODEL, fe1)
            raise fe1

    # ── Primary call: retry loop for external endpoints before declaring degraded ───
    if _endpoint_is_degraded(primary_url):
        return await _try_fallback_chain(f"endpoint {primary_url} is in degraded state")

    _last_exc: Exception | None = None
    for _attempt in range(_ENDPOINT_RETRY_COUNT if _on_external else 1):
        try:
            res = await primary_llm.ainvoke(prompt)
            # Silent failure: some external endpoints return HTTP 200 with empty body
            if _on_external and (not res or not getattr(res, "content", None) or not res.content.strip()):
                _last_exc = RuntimeError("Empty response")
                if _attempt < _ENDPOINT_RETRY_COUNT - 1:
                    logger.debug("⏳ %s: Empty response, retry %d/%d in %.0fs",
                                 label, _attempt + 1, _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY)
                    await asyncio.sleep(_ENDPOINT_RETRY_DELAY)
                    continue
                # Exhausted retries → fallback
                return await _try_fallback_chain("Primary endpoint returned empty response after retries")
            return res, False
        except Exception as e:
            _last_exc = e
            if _is_endpoint_error(e) or (_on_external and "empty" in str(e).lower()):
                if _attempt < _ENDPOINT_RETRY_COUNT - 1:
                    logger.debug("⏳ %s: External endpoint error, retry %d/%d in %.0fs: %s",
                                 label, _attempt + 1, _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY, str(e)[:60])
                    await asyncio.sleep(_ENDPOINT_RETRY_DELAY)
                    continue
                # Exhausted retries → fallback
                return await _try_fallback_chain(f"External endpoint error after {_ENDPOINT_RETRY_COUNT} retries: {str(e)[:60]}")
            raise  # non-retriable error — propagate immediately

    # Should not reach here but handle defensively
    if _on_external and _last_exc:
        return await _try_fallback_chain(f"Primary endpoint exhausted: {str(_last_exc)[:60]}")
    raise _last_exc


async def _invoke_judge_with_retry(
    state: "AgentState", prompt: str, max_retries: int = 3, temperature: float | None = None
):
    """Invoke the judge LLM with retry logic for empty/failed responses.
    On failure: waits 5s (model reload time), re-discovers the node, retries.
    When the primary endpoint returns 401/429, immediately falls back to the configured
    fallback node without burning retry budget on unavailable endpoints.

    temperature: when set, overrides the default judge sampling temperature.
    """
    from config import JUDGE_URL
    last_error = None
    for attempt in range(max_retries):
        try:
            llm = await _get_judge_llm(state)
            if temperature is not None:
                llm = llm.bind(temperature=temperature)
            # Determine primary URL for degradation tracking
            _j_url = (state.get("judge_url_override") or JUDGE_URL or "").rstrip("/")
            res, used_fb = await _invoke_llm_with_fallback(
                llm, _j_url, prompt, timeout=JUDGE_TIMEOUT, label="Judge"
            )
            # Check for empty/useless response
            if res and hasattr(res, 'content') and res.content and len(res.content.strip()) > 0:
                if attempt > 0:
                    logger.info(f"✅ Judge retry {attempt+1}/{max_retries} succeeded")
                return res
            logger.warning(f"⚠️ Judge returned empty/short response (attempt {attempt+1}/{max_retries})")
            last_error = "Empty response"
        except Exception as e:
            logger.warning(f"⚠️ Judge invoke failed (attempt {attempt+1}/{max_retries}): {e}")
            last_error = str(e)

        if attempt < max_retries - 1:
            wait = 5 * (attempt + 1)  # 5s, 10s, 15s
            logger.info(f"🔄 Judge retry in {wait}s (warming up model)...")
            await asyncio.sleep(wait)
            # Clear PS cache to force fresh node discovery
            with _cache_lock:
                _ps_cache.clear()

    # All retries failed — return a minimal response
    logger.error(f"❌ Judge failed after {max_retries} attempts: {last_error}")
    from types import SimpleNamespace
    return SimpleNamespace(content=f"[Judge unavailable after {max_retries} retries: {last_error}]")


async def _get_judge_llm(state: "AgentState") -> "ChatOpenAI":
    """Returns per-template judge LLM, or global judge_llm as fallback.
    Supports floating mode: if model is set but URL is empty, discovers the best node.
    When the configured endpoint is in degraded state, returns the fallback node directly."""
    m = (state.get("judge_model_override") or "").strip()
    u = (state.get("judge_url_override")   or "").strip()
    t = (state.get("judge_token_override") or "ollama").strip()
    if m and u:
        if _endpoint_is_degraded(u.rstrip("/")) and _FALLBACK_ENABLED:
            logger.info("⚡ Judge endpoint degraded — returning fallback LLM directly")
            return await _get_fallback_llm(JUDGE_TIMEOUT)
        return ChatOpenAI(model=m, base_url=u, api_key=t, timeout=JUDGE_TIMEOUT)
    if m and not u:
        # Floating judge: discover the best node for this model
        all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
        node = await _select_node(m, all_eps, user_id=state.get("user_id", ""))
        _url = node.get("url") or URL_MAP.get(node["name"], "")
        _tok = node.get("token", "ollama")
        logger.info(f"🌐 Floating judge: {m} → {node['name']}")
        return ChatOpenAI(model=m, base_url=_url, api_key=_tok, timeout=JUDGE_TIMEOUT)
    return _m.judge_llm


async def _get_planner_llm(state: "AgentState") -> "ChatOpenAI":
    """Returns per-template planner LLM, or global planner_llm as fallback.
    Supports floating mode: if model is set but URL is empty, discovers the best node.
    When the configured endpoint is in degraded state, returns the fallback node directly."""
    m = (state.get("planner_model_override") or "").strip()
    u = (state.get("planner_url_override")   or "").strip()
    t = (state.get("planner_token_override") or "ollama").strip()
    if m and u:
        if _endpoint_is_degraded(u.rstrip("/")) and _FALLBACK_ENABLED:
            logger.info("⚡ Planner endpoint degraded — returning fallback LLM directly")
            return await _get_fallback_llm(PLANNER_TIMEOUT)
        return ChatOpenAI(model=m, base_url=u, api_key=t, timeout=PLANNER_TIMEOUT)
    if m and not u:
        # Floating planner: discover the best node for this model
        all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
        node = await _select_node(m, all_eps, user_id=state.get("user_id", ""))
        _url = node.get("url") or URL_MAP.get(node["name"], "")
        _tok = node.get("token", "ollama")
        logger.info(f"🌐 Floating planner: {m} → {node['name']}")
        return ChatOpenAI(model=m, base_url=_url, api_key=_tok, timeout=PLANNER_TIMEOUT)
    return _m.planner_llm


async def _refine_expert_response(cat: str, gap_feedback: str, state: "AgentState") -> Optional[str]:
    """Re-calls the score-best expert for `cat`, enriched with judge gap feedback."""
    from config import EXPERTS, EXPERT_TIMEOUT
    from main import _get_expert_prompt

    experts_for_cat = EXPERTS.get(cat, [])
    if not experts_for_cat:
        return None
    scored = [(await _get_expert_score(e["model"], cat), e) for e in experts_for_cat]
    scored.sort(key=lambda x: -x[0])
    best_expert = scored[0][1]
    _refine_ep = best_expert.get("endpoints") or [best_expert.get("endpoint", "")]
    if not _refine_ep or _refine_ep == [""]:
        _refine_ep = [s["name"] for s in INFERENCE_SERVERS_LIST]
    node = await _select_node(best_expert["model"], _refine_ep)
    url      = node.get("url") or URL_MAP.get(node["name"])
    token    = node.get("token", "ollama")
    _timeout = float(node.get("timeout", EXPERT_TIMEOUT))
    sys_prompt = _get_expert_prompt(cat, state.get("user_experts"))
    task_text  = state["input"]
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user",   "content": (
            f"{task_text}\n\n"
            f"--- FEEDBACK DES JUDGES (Bitte gezielt verbessern) ---\n{gap_feedback}"
        )},
    ]
    llm = ChatOpenAI(model=best_expert["model"], base_url=url, api_key=token, timeout=_timeout)
    try:
        res = await llm.ainvoke(messages)
        return res.content[:MAX_EXPERT_OUTPUT_CHARS] if res.content else None
    except Exception as e:
        logger.warning(f"⚠️ Refinement Expert [{cat}]: {e}")
        return None


# ---------------------------------------------------------------------------
# PS cache and node load
# ---------------------------------------------------------------------------

_ps_cache: Dict[str, tuple] = {}
_PS_CACHE_TTL = 5.0  # seconds — Ollama state does not change faster


def _get_model_node_load(model: str) -> float:
    """Return the infrastructure load [0.0, 1.0] of the server currently hosting `model`.

    Reads the _ps_cache populated by _pick_inference_server (no additional API
    calls). The load score is running_models / gpu_count, identical to the
    load_score() computation in _pick_inference_server.

    Used by _get_expert_score to construct an infrastructure-aware Beta prior:
    busy nodes receive an inflated beta parameter, reducing their Thompson
    sample and steering expert selection toward less-loaded hardware.

    Returns 0.0 (no penalty) when the model is not found in any cached server,
    which is the safe fallback for cold-start situations.
    """
    model_base = model.split(":")[0]
    with _cache_lock:
        for srv_name, (_, running) in _ps_cache.items():
            if any(m.get("name", "").split(":")[0] == model_base for m in running):
                srv = next((s for s in INFERENCE_SERVERS_LIST if s["name"] == srv_name), None)
                if srv:
                    return min(1.0, len(running) / max(int(srv.get("gpu_count", 1)), 1))
    return 0.0


def _estimate_model_vram_gb(model_name: str) -> float:
    """Estimate VRAM requirement in GB from model name.

    Parses parameter count from name (e.g. 'llama3.3:70b' → 70) and estimates
    VRAM based on common quantization: Q4 ≈ 0.55 * params + 1.5 GB overhead.
    Returns 0 if parameter count cannot be parsed (disables VRAM filtering).
    """
    import re as _re
    # Extract parameter count: "phi4:14b", "llama3.3:70b", "gemma4:31b"
    m = _re.search(r"[:\-](\d+(?:\.\d+)?)b", model_name.lower())
    if not m:
        # Try GGUF names: "Q4_0" → no param info, return 0
        return 0.0
    params_b = float(m.group(1))
    # Q4_K_M estimate: ~0.55 bytes/param + 1.5 GB context/overhead
    return params_b * 0.55 + 1.5


async def _select_node(model_name: str, allowed_endpoints: List[str],
                       user_id: str = "", priority: str = "normal") -> dict:
    """Selects the optimal node for model_name from the allowed endpoints.

    Strategy (4 phases):
    0. VRAM filter: exclude nodes where vram_gb < estimated model requirement
    1. Sticky session: if user recently used this model on a node, prefer it
    2. Check Ollama /api/ps (with 5s cache) for warm/cold models
    3. Within warm/cold: lowest load score (running/gpu_count) wins
    Priority: 'high' = pinned templates, 'normal' = standard, 'low' = floating/batch
    OpenAI nodes: always cold, neutral load.
    """
    # Phase 0: Sticky session check (warm model affinity for same user)
    if state.redis_client and user_id:
        try:
            sticky_key = f"moe:sticky:{user_id}:{model_name.split(':')[0]}"
            sticky_node = await state.redis_client.get(sticky_key)
            if sticky_node:
                sticky_name = sticky_node if isinstance(sticky_node, str) else sticky_node.decode()
                if sticky_name in allowed_endpoints:
                    srv = _server_info(sticky_name)
                    if srv:
                        # VRAM guard: verify sticky node can actually fit the model
                        est = _estimate_model_vram_gb(model_name)
                        node_vram = float(srv.get("vram_gb", 0))
                        if est > 0 and node_vram > 0 and node_vram < est:
                            await state.redis_client.delete(sticky_key)
                            logger.warning(f"🔒 Sticky override: {sticky_name} has {node_vram}GB but {model_name} needs ~{est:.1f}GB — re-routing")
                        else:
                            logger.debug(f"📌 Sticky session: {sticky_name} for {model_name}")
                            return srv
        except Exception:
            pass
    # Dynamic server exclusions stored in Redis (survive container restarts without rebuild)
    _blocked: set = set()
    _float_disabled: set = set()
    if state.redis_client is not None:
        try:
            _blocked       = {(v if isinstance(v, str) else v.decode()) for v in await state.redis_client.smembers("moe:blocked_servers")}
            _float_disabled = {(v if isinstance(v, str) else v.decode()) for v in await state.redis_client.smembers("moe:floating_disabled_servers")}
        except Exception:
            pass
    # Hard block: remove from every pool regardless of pinning
    _effective = [ep for ep in allowed_endpoints if ep not in _blocked]
    # Floating-disable: only applies when multiple endpoints are in the pool
    # (single-endpoint = explicit @node pin — always honoured)
    if len(_effective) > 1:
        _effective = [ep for ep in _effective if ep not in _float_disabled]
    candidates = [s for s in INFERENCE_SERVERS_LIST if s["name"] in _effective]
    if not candidates:
        # Fall back to the first non-blocked endpoint (preserves liveness)
        fallback_name = _effective[0] if _effective else (allowed_endpoints[0] if allowed_endpoints else "")
        return _server_info(fallback_name) or {"name": fallback_name, "url": URL_MAP.get(fallback_name, ""), "token": "ollama", "api_type": "ollama"}

    # Phase 0: VRAM filter — exclude nodes that cannot fit this model
    est_vram = _estimate_model_vram_gb(model_name)
    if est_vram > 0:
        vram_ok = [s for s in candidates if float(s.get("vram_gb", 0)) >= est_vram]
        if vram_ok:
            if len(vram_ok) < len(candidates):
                excluded = [s["name"] for s in candidates if s not in vram_ok]
                logger.info(f"🔒 VRAM filter: {model_name} needs ~{est_vram:.1f}GB — excluded {excluded}")
            candidates = vram_ok
        else:
            # Hard filter: only keep nodes WITHOUT a vram_gb limit (cloud/external)
            no_limit = [s for s in candidates if not s.get("vram_gb")]
            if no_limit:
                logger.warning(f"⚠️ No local node has enough VRAM for {model_name} (~{est_vram:.1f}GB) — using cloud/external nodes only")
                candidates = no_limit
            else:
                logger.error(f"🚫 VRAM hard block: {model_name} (~{est_vram:.1f}GB) exceeds ALL nodes — routing to largest available")
                candidates = sorted(candidates, key=lambda s: float(s.get("vram_gb", 0)), reverse=True)[:1]

    if len(candidates) == 1:
        return candidates[0]

    async def _get_ps(srv: dict) -> tuple:
        """Returns (srv, running_models_list, model_is_warm). Uses 5s cache."""
        if srv.get("api_type", "ollama") != "ollama":
            return srv, [], False
        now = time.monotonic()
        with _cache_lock:
            cached = _ps_cache.get(srv["name"])
        if cached and (now - cached[0]) < _PS_CACHE_TTL:
            running = cached[1]
        else:
            base = srv["url"].rstrip("/").removesuffix("/v1")
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    r = await client.get(f"{base}/api/ps")
                    running = r.json().get("models", []) if r.status_code == 200 else []
            except Exception:
                running = []
            with _cache_lock:
                _ps_cache[srv["name"]] = (now, running)
        is_warm = any(
            m.get("name", "").split(":")[0] == model_name.split(":")[0]
            and (not model_name.count(":") or m.get("name") == model_name)
            for m in running
        )
        return srv, running, is_warm

    def load_score(srv: dict, running: list) -> float:
        """Lower score = better candidate. Factors in GPU count AND cost_factor.
        cost_factor acts as a speed/priority weight: higher = faster/preferred.
        RTX (1.0) is preferred over Tesla M10 (0.8) at equal load."""
        raw_load = len(running) / max(int(srv.get("gpu_count", 1)), 1)
        speed = float(srv.get("cost_factor", 1.0))  # higher = faster GPU
        return raw_load / max(speed, 0.1)  # divide by speed: fast nodes get lower scores

    # Select best candidate: warm preferred, then idle, then lowest load
    ps_results = await asyncio.gather(*[_get_ps(s) for s in candidates])
    warm = [(srv, running) for srv, running, is_warm in ps_results if is_warm]
    cold = [(srv, running) for srv, running, is_warm in ps_results if not is_warm]

    # Priority order: 1) warm + idle, 2) warm + busy, 3) cold + idle, 4) cold + busy
    warm_idle = [(s, r) for s, r in warm if load_score(s, r) < 0.5]
    cold_idle = [(s, r) for s, r in cold if load_score(s, r) < 0.5]

    pool = warm_idle or warm or cold_idle or cold
    best = min(pool, key=lambda x: load_score(x[0], x[1]))

    status = "🔥 warm" if warm else "❄️ cold"
    busy = "idle" if load_score(best[0], best[1]) < 0.5 else "busy"
    logger.debug(f"{status}/{busy} Node-Select: {best[0]['name']} for {model_name}")

    # Set sticky session for future requests from this user
    if state.redis_client and user_id:
        try:
            sticky_key = f"moe:sticky:{user_id}:{model_name.split(':')[0]}"
            asyncio.create_task(
                state.redis_client.setex(sticky_key, 300, best[0]["name"])  # 5 min TTL
            )
        except Exception:
            pass

    return best[0]


# ---------------------------------------------------------------------------
# Expert scoring (Thompson sampling)
# ---------------------------------------------------------------------------

# THOMPSON_SAMPLING_ENABLED imported from config.py


async def _get_expert_score(model: str, category: str) -> float:
    """Performance score 0-1 for a model in a category.

    When ``THOMPSON_SAMPLING_ENABLED`` is true, draws from Beta(α, β) instead
    of the deterministic Laplace point estimate.  This provides natural
    exploration: experts with fewer observations have wider variance and
    occasionally score higher than their point estimate, giving them a chance
    to prove themselves.
    """
    if state.redis_client is None:
        return 0.5
    try:
        key = _perf_key(model, category)
        data = await state.redis_client.hgetall(key)
        total = int(data.get("total", 0))
        if total < EXPERT_MIN_DATAPOINTS:
            return 0.5
        positive = int(data.get("positive", 0))
        if THOMPSON_SAMPLING_ENABLED:
            import random
            alpha = positive + 1
            beta  = (total - positive) + 1
            # Infrastructure-adaptive prior (Bayesian maximum-entropy principle):
            # Inflate beta by node load so busy servers draw lower Thompson
            # samples, steering selection toward less-loaded hardware.
            # At load=0.0: beta unchanged (idle node, no penalty).
            # At load=1.0: beta *= (1 + _LOAD_PENALTY) — e.g. 3× at penalty=2.
            # The Beta distribution remains well-defined for all positive (α, β).
            _LOAD_PENALTY = float(os.getenv("THOMPSON_LOAD_PENALTY", "2.0"))
            _node_load    = _get_model_node_load(model)
            beta          = beta * (1.0 + _LOAD_PENALTY * _node_load)
            score = random.betavariate(alpha, beta)
            PROM_THOMPSON.observe(score)
            return score
        return (positive + 1) / (total + 2)  # Laplace fallback
    except Exception:
        return 0.5

async def _record_expert_outcome(model: str, category: str, positive: bool) -> None:
    """Increments total and optionally positive counter for a model/category pair."""
    if state.redis_client is None:
        return
    try:
        key = _perf_key(model, category)
        pipe = state.redis_client.pipeline()
        pipe.hincrby(key, "total", 1)
        if positive:
            pipe.hincrby(key, "positive", 1)
        else:
            pipe.hincrby(key, "negative", 1)
        await pipe.execute()
    except Exception as e:
        logger.warning(f"Expert score update failed: {e}")

# _extract_usage, _extract_json, _parse_expert_confidence,
# _parse_expert_gaps, _expert_category — see parsing.py

def _infer_tier(model_name: str) -> int:
    """Derives expert tier from model size in the name. T1 ≤20B, T2 >20B.
    Kept for backward compatibility with raw EXPERTS env var entries that lack
    an explicit '_tier' field. New code uses explicit role→_tier mapping in templates."""
    m = re.search(r':(\d+(?:\.\d+)?)b', model_name, re.I)
    if not m:
        return 1
    return 1 if float(m.group(1)) <= EXPERT_TIER_BOUNDARY_B else 2

"""CC-Session resolution — per-request configuration for Claude Code profile routing.

Extracts all CC-profile resolution logic from the anthropic_messages() endpoint into
a single, testable, side-effect-free function that returns a fully resolved CCSession.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Optional

from config import (
    CLAUDE_CODE_MODE,
    CLAUDE_CODE_TOOL_MODEL,
    CLAUDE_CODE_TOOL_ENDPOINT,
    _CLAUDE_CODE_TOOL_URL,
    _CLAUDE_CODE_TOOL_TOKEN,
    URL_MAP,
    TOKEN_MAP,
    API_TYPE_MAP,
    JUDGE_TIMEOUT,
    AGENT_CACHE_ENABLED,
    AGENT_GRAPHRAG_ENABLED,
    AGENT_INGEST_ENABLED,
)
from services.templates import _read_cc_profiles
from services.routing import _resolve_user_experts, _resolve_template_prompts, _server_info

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CCSession:
    """Fully resolved configuration for a single Claude Code request.

    Built once per request by _resolve_cc_session() and passed to all three
    handlers instead of a flat list of 15+ parameters.
    """

    # ── Routing ──────────────────────────────────────────────────────────────
    mode: str = "moe_orchestrated"   # "native" | "moe_reasoning" | "moe_orchestrated"

    # ── Tool model (used in all modes for tool-call turns) ───────────────────
    tool_model: str = ""
    tool_endpoint: str = ""
    tool_url: str = ""
    tool_token: str = ""
    tool_api_type: str = "openai"   # "ollama" | "openai" | "anthropic"
    tool_timeout: int = 120
    tool_max_tokens: int = 0
    context_window: int = 0
    template_num_ctx: int = 0
    tool_choice: str = "auto"
    is_user_conn: bool = False

    # ── Reasoning (moe_reasoning mode) ───────────────────────────────────────
    reasoning_max_tokens: int = 0

    # ── Prompting ────────────────────────────────────────────────────────────
    system_prefix: str = ""
    # System prompt of the template's tool_agent expert. Consumed ONLY by
    # _anthropic_tool_handler — must never leak into reasoning/MoE turns.
    tool_system_prefix: str = ""
    stream_think: bool = False

    # ── Resolved from user permissions + expert template ─────────────────────
    user_perms: dict = dataclasses.field(default_factory=dict)
    experts: dict = dataclasses.field(default_factory=dict)
    planner_cfg: dict = dataclasses.field(default_factory=dict)
    # Expert template linked via the CC profile's expert_template_id (used for
    # tool_model auto-derivation in Phase 5.5). Exposed here so callers can
    # surface it in live-monitoring registration — previously the "Template"
    # column in the admin "Laufende API-Anfragen" table was always blank for
    # Claude Code sessions even when a template was actively driving the
    # tool model, because anthropic_messages() had no way to see this ID.
    expert_template_id: str = ""

    # ── Long-term memory tiers (Tier 2-4): can be disabled per CC profile ──────
    long_memory: bool = True   # False disables Tier-2 ChromaDB warm, Tier-3 context-index, Tier-4 Neo4j

    # ── Augmented Tool Path (opt-in cache/GraphRAG/ingestion for agentic turns) ─
    # All default to the global AGENT_*_ENABLED flags (config.py), overridable
    # per CC profile. agent_graphrag is additionally forced off when long_memory
    # is off (same kill-switch semantics as the existing Tier 2-4 memory path).
    agent_cache: bool = False
    agent_graphrag: bool = False
    agent_ingest: bool = False

    # ── Sentinel: set True when profile_ids were given but no profile resolved ─
    profile_not_found: bool = False


def _resolve_cc_session(user_ctx: dict, profile_ids: list) -> CCSession:
    """Build a CCSession from user context and assigned CC-profile IDs.

    Consolidates the 3-phase resolution previously scattered across
    anthropic_messages(): profile lookup → endpoint/token resolution →
    template override. Template resolution happens exactly once.

    Args:
        user_ctx:    Decoded API-key context dict from _validate_api_key().
        profile_ids: List of CC-profile IDs from user permissions.

    Returns:
        Fully resolved CCSession. Check .profile_not_found to detect the
        "profiles assigned but none matched" case (→ 422 in the endpoint).
    """
    permissions_json    = user_ctx.get("permissions_json", "")
    user_templates_json = user_ctx.get("user_templates_json", "{}")
    user_conns_json     = user_ctx.get("user_connections_json", "{}")
    user_perms          = json.loads(permissions_json or "{}")

    # ── Phase 1: Parse user CC-profile map ───────────────────────────────────
    user_cc_map: dict = {}
    try:
        user_cc_map = json.loads(user_ctx.get("user_cc_profiles_json", "") or "{}")
    except Exception:
        pass

    # ── Phase 2: Resolve the active profile (4-level priority) ───────────────
    profile: Optional[dict] = None
    if profile_ids:
        def _pick(pid: str) -> Optional[dict]:
            if not pid:
                return None
            return user_cc_map.get(pid) or next(
                (p for p in _read_cc_profiles() if p.get("id") == pid), None
            )

        key_pid     = user_ctx.get("key_cc_profile_id", "") or ""
        default_pid = user_ctx.get("default_cc_profile_id", "") or ""
        profile = (
            _pick(key_pid)
            or _pick(default_pid)
            or next((v for pid in profile_ids for v in [user_cc_map.get(pid)] if v), None)
            or next(
                (p for p in _read_cc_profiles()
                 if p.get("id") in profile_ids and p.get("enabled", True)),
                None,
            )
        )

        if profile is None:
            # Profiles were assigned but none resolved — caller must return 422.
            return CCSession(profile_not_found=True, user_perms=user_perms)

    # ── Phase 3: Endpoint / URL / token resolution ────────────────────────────
    mode           = (profile.get("moe_mode", CLAUDE_CODE_MODE) if profile else CLAUDE_CODE_MODE)
    # Empty tool_model → auto-derive from expert template's tool_agent after Phase 5.
    tool_model     = (profile.get("tool_model") or "").strip().rstrip("*") if profile else CLAUDE_CODE_TOOL_MODEL
    tool_endpoint  = (profile.get("tool_endpoint", CLAUDE_CODE_TOOL_ENDPOINT)
                      if profile else CLAUDE_CODE_TOOL_ENDPOINT)
    tool_url       = URL_MAP.get(tool_endpoint) if tool_endpoint else None
    tool_token     = TOKEN_MAP.get(tool_endpoint, "ollama")
    tool_api_type  = API_TYPE_MAP.get(tool_endpoint, "ollama") if tool_endpoint else "openai"
    is_user_conn   = False
    # System prompt injected from the template's tool_agent expert (see below).
    _tool_agent_system_prompt: str = ""

    # Template-backed tool model: tool_model = "template:<template_id>"
    # The tool_agent expert within that template handles all tool-call turns.
    # Its LLM + endpoint replace the native tool_model/url/token; its system
    # prompt is prepended so the model knows how to handle tool_header content.
    if tool_model.startswith("template:"):
        _tmpl_tool_id = tool_model.removeprefix("template:").strip()
        _tmpl_experts = _resolve_user_experts(
            permissions_json,
            override_tmpl_id=_tmpl_tool_id,
            user_templates_json=user_templates_json,
            admin_override=True,
            user_connections_json=user_conns_json,
        )
        _agentic_exp = None
        if _tmpl_experts:
            # Prefer tool_agent, then code, then first available expert with a model.
            for _cat in ("tool_agent", "code", "general"):
                _exp_list = _tmpl_experts.get(_cat)
                if _exp_list and isinstance(_exp_list, list):
                    _cand = next((e for e in _exp_list if e.get("model") and e.get("url")), None)
                    if _cand:
                        _agentic_exp = _cand
                        break
            if not _agentic_exp:
                # Last-resort: any expert with a model and URL
                for _exp_list in _tmpl_experts.values():
                    if isinstance(_exp_list, list):
                        _cand = next((e for e in _exp_list if e.get("model") and e.get("url")), None)
                        if _cand:
                            _agentic_exp = _cand
                            break
        if _agentic_exp:
            tool_model    = _agentic_exp["model"]
            tool_url      = _agentic_exp["url"]
            tool_token    = _agentic_exp.get("token", "ollama")
            tool_endpoint = _agentic_exp.get("endpoint", "")
            # Ollama endpoints need api_type "ollama" so the num_ctx injection in
            # _anthropic_tool_handler fires — otherwise the model loads at its
            # default context (8192) and CC's tool schemas alone overflow it.
            tool_api_type = API_TYPE_MAP.get(tool_endpoint, "openai")
            is_user_conn  = False
            _tool_agent_system_prompt = (_agentic_exp.get("_system_prompt") or "").strip()
        else:
            logger.warning(
                "CC profile: template '%s' has no resolvable tool_agent expert — "
                "falling back to global tool model.",
                _tmpl_tool_id,
            )
            tool_model    = CLAUDE_CODE_TOOL_MODEL
            tool_url      = _CLAUDE_CODE_TOOL_URL
            tool_token    = _CLAUDE_CODE_TOOL_TOKEN
            tool_api_type = "openai"

    if tool_model and not tool_url:
        # Only resolve connection/fallback URL when tool_model is explicitly set.
        # If empty, we defer URL resolution until after Phase 5 (auto-derive from template).
        user_conns: dict = {}
        try:
            user_conns = json.loads(user_conns_json or "{}")
        except Exception:
            pass
        uc = user_conns.get(tool_endpoint)
        if uc:
            tool_url      = uc["url"]
            tool_token    = uc.get("api_key") or "ollama"
            tool_api_type = uc.get("api_type", "openai")  # preserve user-conn api_type
            is_user_conn  = True
        else:
            tool_url      = _CLAUDE_CODE_TOOL_URL
            tool_token    = _CLAUDE_CODE_TOOL_TOKEN
            tool_api_type = "openai"

    # ── Phase 4: Timeout (node config, then profile override) ─────────────────
    node_cfg     = _server_info(tool_endpoint) if tool_endpoint else {}
    tool_timeout = int(node_cfg.get("timeout", JUDGE_TIMEOUT))
    if profile and profile.get("tool_timeout"):
        tool_timeout = int(profile["tool_timeout"])

    # ── Phase 5: Template resolution — exactly once ───────────────────────────
    # Determine the template to use: CC-profile override takes precedence over
    # the user's own template assignment.
    cc_tmpl_id = (profile.get("expert_template_id") or None) if profile else None

    if cc_tmpl_id:
        experts = _resolve_user_experts(
            permissions_json,
            override_tmpl_id=cc_tmpl_id,
            user_templates_json=user_templates_json,
            admin_override=True,
            user_connections_json=user_conns_json,
        )
        if experts is None:
            logger.warning(
                "CC profile '%s' references template '%s' which does not exist — "
                "falling back to user permissions.",
                profile.get("name", "?") if profile else "?",
                cc_tmpl_id,
            )
            # Fall back to user's own template (no override)
            experts = _resolve_user_experts(
                permissions_json,
                user_templates_json=user_templates_json,
                user_connections_json=user_conns_json,
            ) or {}
            planner_cfg = _resolve_template_prompts(
                permissions_json,
                user_templates_json=user_templates_json,
                user_connections_json=user_conns_json,
            )
        else:
            planner_cfg = _resolve_template_prompts(
                permissions_json,
                override_tmpl_id=cc_tmpl_id,
                user_templates_json=user_templates_json,
                admin_override=True,
                user_connections_json=user_conns_json,
            )
    else:
        # No CC-profile template override — use user's own assignment
        experts = _resolve_user_experts(
            permissions_json,
            user_templates_json=user_templates_json,
            user_connections_json=user_conns_json,
        ) or {}
        planner_cfg = _resolve_template_prompts(
            permissions_json,
            user_templates_json=user_templates_json,
            user_connections_json=user_conns_json,
        )

    # ── Phase 5.5: Auto-derive tool model from expert template's tool_agent ─────
    # When tool_model is empty and the CC profile has an expert_template_id with a
    # tool_agent expert, use that expert's LLM instead of requiring a separate entry.
    if not tool_model and cc_tmpl_id and experts:
        _ta_list = experts.get("tool_agent")
        if _ta_list and isinstance(_ta_list, list):
            _auto_exp = next((e for e in _ta_list if e.get("model") and e.get("url")), None)
            if _auto_exp:
                tool_model    = _auto_exp["model"]
                tool_url      = _auto_exp["url"]
                tool_token    = _auto_exp.get("token", "ollama")
                tool_endpoint = _auto_exp.get("endpoint", "")
                # See Phase 3: api_type must reflect the actual endpoint so the
                # Ollama num_ctx injection fires for template-backed tool models.
                tool_api_type = API_TYPE_MAP.get(tool_endpoint, "openai")
                is_user_conn  = False
                _tool_agent_system_prompt = (_auto_exp.get("_system_prompt") or "").strip()
                # Phase 4 computed the timeout from the profile's original (empty)
                # endpoint — recompute for the derived endpoint unless the profile
                # explicitly overrides it.
                if not (profile and profile.get("tool_timeout")):
                    _auto_node_cfg = _server_info(tool_endpoint) if tool_endpoint else {}
                    tool_timeout   = int(_auto_node_cfg.get("timeout", JUDGE_TIMEOUT))
                logger.debug(
                    "CC profile: auto-derived tool model '%s' from expert template '%s' tool_agent.",
                    tool_model, cc_tmpl_id,
                )

    # Final fallback when tool_model is still empty (no template, no explicit value)
    if not tool_model:
        tool_model    = CLAUDE_CODE_TOOL_MODEL
        tool_url      = _CLAUDE_CODE_TOOL_URL
        tool_token    = _CLAUDE_CODE_TOOL_TOKEN
        tool_api_type = "openai"

    # ── Phase 6: Profile scalar overrides ────────────────────────────────────
    long_memory   = bool(profile.get("long_memory", True))              if profile else True
    _prof_prefix  = (profile.get("system_prompt_prefix") or "").strip() if profile else ""
    system_prefix = _prof_prefix
    # tool_agent prompt is kept separate — only the tool handler prepends it.
    tool_system_prefix = _tool_agent_system_prompt
    tool_max_tokens      = int(profile.get("tool_max_tokens") or 0)             if profile else 0
    reasoning_max_tokens = int(profile.get("reasoning_max_tokens") or 0)        if profile else 0
    tool_choice          = (profile.get("tool_choice") or "").strip()           if profile else ""
    stream_think         = bool(profile.get("stream_think", False))             if profile else False
    context_window       = int(profile.get("context_window") or 0)              if profile else 0

    agent_cache    = bool(profile.get("agent_cache", AGENT_CACHE_ENABLED))       if profile else AGENT_CACHE_ENABLED
    agent_graphrag = bool(profile.get("agent_graphrag", AGENT_GRAPHRAG_ENABLED)) if profile else AGENT_GRAPHRAG_ENABLED
    agent_ingest   = bool(profile.get("agent_ingest", AGENT_INGEST_ENABLED))     if profile else AGENT_INGEST_ENABLED
    if not long_memory:
        # Same kill-switch as the existing Tier 2-4 memory path: no long-term
        # memory means no GraphRAG context injection for agentic turns either.
        agent_graphrag = False

    # Enforce template context_window as the source of truth for token limits.
    # Only applies when the CC profile explicitly sets expert_template_id (i.e. mode is
    # moe_orchestrated AND a template is configured on the profile itself). User-default
    # templates must not silently cap a profile's values when no explicit override exists.
    _tmpl_ctx = 0
    _has_explicit_tmpl = bool(profile and cc_tmpl_id)
    if _has_explicit_tmpl:
        _tmpl_ctx = planner_cfg.get("judge_num_ctx", 0) if planner_cfg else 0
        if _tmpl_ctx <= 0:
            _tmpl_ctx = max(
                (m["context_window"] for v in experts.values() if isinstance(v, list)
                 for m in v if isinstance(m, dict) and m.get("context_window")),
                default=0,
            )
    if _has_explicit_tmpl and mode == "moe_orchestrated":
        _profile_name = profile.get("name", "?") if profile else "?"
        if tool_max_tokens and tool_max_tokens > _tmpl_ctx:
            logger.warning(
                "CC profile '%s': tool_max_tokens=%d exceeds template context_window=%d"
                " — capping to template value. Update tool_max_tokens in the profile to silence this.",
                _profile_name, tool_max_tokens, _tmpl_ctx,
            )
            tool_max_tokens = _tmpl_ctx
        if context_window and context_window > _tmpl_ctx:
            logger.warning(
                "CC profile '%s': context_window=%d exceeds template context_window=%d"
                " — capping to template value. Update context_window in the profile to silence this.",
                _profile_name, context_window, _tmpl_ctx,
            )
            context_window = _tmpl_ctx
        if reasoning_max_tokens and reasoning_max_tokens > _tmpl_ctx:
            logger.warning(
                "CC profile '%s': reasoning_max_tokens=%d exceeds template context_window=%d"
                " — capping to template value. Update reasoning_max_tokens in the profile to silence this.",
                _profile_name, reasoning_max_tokens, _tmpl_ctx,
            )
            reasoning_max_tokens = _tmpl_ctx

    return CCSession(
        mode=mode,
        tool_model=tool_model,
        tool_endpoint=tool_endpoint,
        tool_url=tool_url,
        tool_token=tool_token,
        tool_api_type=tool_api_type,
        tool_timeout=tool_timeout,
        tool_max_tokens=tool_max_tokens,
        context_window=context_window,
        template_num_ctx=_tmpl_ctx,
        tool_choice=tool_choice,
        is_user_conn=is_user_conn,
        long_memory=long_memory,
        agent_cache=agent_cache,
        agent_graphrag=agent_graphrag,
        agent_ingest=agent_ingest,
        expert_template_id=cc_tmpl_id or "",
        reasoning_max_tokens=reasoning_max_tokens,
        system_prefix=system_prefix,
        tool_system_prefix=tool_system_prefix,
        stream_think=stream_think,
        user_perms=user_perms,
        experts=experts,
        planner_cfg=planner_cfg,
    )

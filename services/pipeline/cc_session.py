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
    JUDGE_TIMEOUT,
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
    tool_timeout: int = 120
    tool_max_tokens: int = 0
    tool_choice: str = "auto"
    is_user_conn: bool = False

    # ── Reasoning (moe_reasoning mode) ───────────────────────────────────────
    reasoning_max_tokens: int = 0

    # ── Prompting ────────────────────────────────────────────────────────────
    system_prefix: str = ""
    stream_think: bool = False

    # ── Resolved from user permissions + expert template ─────────────────────
    user_perms: dict = dataclasses.field(default_factory=dict)
    experts: dict = dataclasses.field(default_factory=dict)
    planner_cfg: dict = dataclasses.field(default_factory=dict)

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
    tool_model     = (profile.get("tool_model", CLAUDE_CODE_TOOL_MODEL).strip().rstrip("*")
                      if profile else CLAUDE_CODE_TOOL_MODEL)
    tool_endpoint  = (profile.get("tool_endpoint", CLAUDE_CODE_TOOL_ENDPOINT)
                      if profile else CLAUDE_CODE_TOOL_ENDPOINT)
    tool_url       = URL_MAP.get(tool_endpoint) if tool_endpoint else None
    tool_token     = TOKEN_MAP.get(tool_endpoint, "ollama")
    is_user_conn   = False

    if not tool_url:
        # Fallback: resolve as a user-owned private connection
        user_conns: dict = {}
        try:
            user_conns = json.loads(user_conns_json or "{}")
        except Exception:
            pass
        uc = user_conns.get(tool_endpoint)
        if uc:
            tool_url     = uc["url"]
            tool_token   = uc.get("api_key") or "ollama"
            is_user_conn = True
        else:
            tool_url   = _CLAUDE_CODE_TOOL_URL
            tool_token = _CLAUDE_CODE_TOOL_TOKEN

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

    # ── Phase 6: Profile scalar overrides ────────────────────────────────────
    system_prefix        = (profile.get("system_prompt_prefix") or "").strip() if profile else ""
    tool_max_tokens      = int(profile.get("tool_max_tokens") or 0)             if profile else 0
    reasoning_max_tokens = int(profile.get("reasoning_max_tokens") or 0)        if profile else 0
    tool_choice          = (profile.get("tool_choice") or "").strip()           if profile else ""
    stream_think         = bool(profile.get("stream_think", False))             if profile else False

    # Enforce template context_window as the source of truth for token limits.
    # Profile values must not exceed the template constraint — if they do, the session
    # would silently deliver less output than advertised, with no operator feedback.
    _tmpl_ctx = min(
        (v["context_window"] for v in experts.values()
         if isinstance(v, dict) and v.get("context_window")),
        default=0,
    )
    if _tmpl_ctx > 0:
        _profile_name = profile.get("name", "?") if profile else "?"
        if tool_max_tokens and tool_max_tokens > _tmpl_ctx:
            logger.warning(
                "CC profile '%s': tool_max_tokens=%d exceeds template context_window=%d"
                " — capping to template value. Update tool_max_tokens in the profile to silence this.",
                _profile_name, tool_max_tokens, _tmpl_ctx,
            )
            tool_max_tokens = _tmpl_ctx
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
        tool_timeout=tool_timeout,
        tool_max_tokens=tool_max_tokens,
        tool_choice=tool_choice,
        is_user_conn=is_user_conn,
        reasoning_max_tokens=reasoning_max_tokens,
        system_prefix=system_prefix,
        stream_think=stream_think,
        user_perms=user_perms,
        experts=experts,
        planner_cfg=planner_cfg,
    )

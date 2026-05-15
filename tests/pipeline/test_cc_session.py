"""
Tests for services/pipeline/cc_session.py

Covers:
  - CCSession field presence (refactoring guard)
  - _resolve_cc_session() with no profile assigned
  - _resolve_cc_session() with profile (mode, stream_think, system_prefix overrides)
  - Profile priority: key-specific > user-default > first-available
  - Template override via expert_template_id (resolved exactly once)
  - User-connection fallback when endpoint not in URL_MAP
  - Error cases: missing template falls back gracefully; bad JSON is safe
  - profile_not_found sentinel when profile_ids given but no match
"""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from services.pipeline.cc_session import CCSession, _resolve_cc_session


# ── Constants ─────────────────────────────────────────────────────────────────

REQUIRED_FIELDS = {
    "mode", "tool_model", "tool_url", "tool_token", "tool_endpoint",
    "tool_timeout", "tool_max_tokens", "reasoning_max_tokens",
    "tool_choice", "system_prefix", "stream_think", "is_user_conn",
    "user_perms", "experts", "planner_cfg", "profile_not_found",
}

_FAKE_PROFILE = {
    "id": "prof-001",
    "name": "Test Profile",
    "moe_mode": "moe_reasoning",
    "tool_model": "qwen3-8b",
    "tool_endpoint": "RTX",
    "tool_max_tokens": 8192,
    "reasoning_max_tokens": 16384,
    "tool_choice": "required",
    "system_prompt_prefix": "Be concise.",
    "stream_think": True,
    "enabled": True,
}

_FAKE_EXPERTS = {"coding": [{"model": "qwen3-8b", "endpoint": "RTX"}]}
_FAKE_PLANNER = {"planner_prompt": "Plan well.", "judge_prompt": "Judge fairly."}

_EMPTY_USER_CTX = {
    "permissions_json": "{}",
    "user_templates_json": "{}",
    "user_connections_json": "{}",
    "user_cc_profiles_json": "{}",
    "key_cc_profile_id": "",
    "default_cc_profile_id": "",
}


def _make_ctx(**overrides) -> dict:
    ctx = dict(_EMPTY_USER_CTX)
    ctx.update(overrides)
    return ctx


# ── A) Field presence ─────────────────────────────────────────────────────────


def test_cc_session_has_all_required_fields():
    hints = CCSession.__dataclass_fields__
    missing = REQUIRED_FIELDS - set(hints.keys())
    assert not missing, f"CCSession missing fields: {sorted(missing)}"


def test_cc_session_defaults_are_safe():
    s = CCSession()
    assert s.mode == "moe_orchestrated"
    assert s.stream_think is False
    assert s.profile_not_found is False
    assert s.experts == {}
    assert s.planner_cfg == {}
    assert s.user_perms == {}


# ── B) No profile assigned ────────────────────────────────────────────────────


@patch("services.pipeline.cc_session._server_info", return_value={"timeout": 60})
@patch("services.pipeline.cc_session._resolve_template_prompts", return_value=_FAKE_PLANNER)
@patch("services.pipeline.cc_session._resolve_user_experts", return_value=_FAKE_EXPERTS)
@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[])
def test_resolve_no_profile_uses_defaults(mock_profiles, mock_experts, mock_prompts, mock_srv):
    session = _resolve_cc_session(_make_ctx(), profile_ids=[])

    assert session.profile_not_found is False
    assert session.stream_think is False
    assert session.system_prefix == ""
    assert session.experts == _FAKE_EXPERTS
    assert session.planner_cfg == _FAKE_PLANNER
    # Template resolution called exactly once, without override
    mock_experts.assert_called_once()
    assert mock_experts.call_args.kwargs.get("override_tmpl_id") is None
    mock_prompts.assert_called_once()


# ── C) Profile overrides ──────────────────────────────────────────────────────


@patch("services.pipeline.cc_session._server_info", return_value={"timeout": 60})
@patch("services.pipeline.cc_session._resolve_template_prompts", return_value=_FAKE_PLANNER)
@patch("services.pipeline.cc_session._resolve_user_experts", return_value=_FAKE_EXPERTS)
@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[_FAKE_PROFILE])
def test_resolve_with_profile_overrides_mode(mock_profiles, mock_experts, mock_prompts, mock_srv):
    ctx = _make_ctx(
        user_cc_profiles_json=json.dumps({"prof-001": _FAKE_PROFILE}),
    )
    session = _resolve_cc_session(ctx, profile_ids=["prof-001"])

    assert session.mode == "moe_reasoning"
    assert session.tool_model == "qwen3-8b"
    assert session.tool_max_tokens == 8192
    assert session.reasoning_max_tokens == 16384
    assert session.tool_choice == "required"
    assert session.system_prefix == "Be concise."
    assert session.stream_think is True
    assert session.profile_not_found is False


# ── D) Profile priority ───────────────────────────────────────────────────────


@patch("services.pipeline.cc_session._server_info", return_value={})
@patch("services.pipeline.cc_session._resolve_template_prompts", return_value={})
@patch("services.pipeline.cc_session._resolve_user_experts", return_value={})
@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[])
def test_key_profile_beats_default(mock_profiles, mock_experts, mock_prompts, mock_srv):
    key_profile   = {**_FAKE_PROFILE, "id": "key-prof",     "moe_mode": "native"}
    default_profile = {**_FAKE_PROFILE, "id": "default-prof", "moe_mode": "moe_orchestrated"}
    ctx = _make_ctx(
        user_cc_profiles_json=json.dumps({
            "key-prof":     key_profile,
            "default-prof": default_profile,
        }),
        key_cc_profile_id="key-prof",
        default_cc_profile_id="default-prof",
    )
    session = _resolve_cc_session(ctx, profile_ids=["key-prof", "default-prof"])
    assert session.mode == "native"


@patch("services.pipeline.cc_session._server_info", return_value={})
@patch("services.pipeline.cc_session._resolve_template_prompts", return_value={})
@patch("services.pipeline.cc_session._resolve_user_experts", return_value={})
@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[])
def test_default_beats_first_available(mock_profiles, mock_experts, mock_prompts, mock_srv):
    default_profile  = {**_FAKE_PROFILE, "id": "default-prof", "moe_mode": "moe_reasoning"}
    fallback_profile = {**_FAKE_PROFILE, "id": "other-prof",   "moe_mode": "native"}
    ctx = _make_ctx(
        user_cc_profiles_json=json.dumps({
            "default-prof": default_profile,
            "other-prof":   fallback_profile,
        }),
        default_cc_profile_id="default-prof",
    )
    session = _resolve_cc_session(ctx, profile_ids=["other-prof", "default-prof"])
    assert session.mode == "moe_reasoning"


# ── E) Template override via expert_template_id ───────────────────────────────


@patch("services.pipeline.cc_session._server_info", return_value={})
@patch("services.pipeline.cc_session._resolve_template_prompts", return_value=_FAKE_PLANNER)
@patch("services.pipeline.cc_session._resolve_user_experts", return_value=_FAKE_EXPERTS)
@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[])
def test_template_resolved_exactly_once_with_override(mock_profiles, mock_experts, mock_prompts, mock_srv):
    profile_with_tmpl = {**_FAKE_PROFILE, "id": "prof-tmpl", "expert_template_id": "tmpl-xyz"}
    ctx = _make_ctx(
        user_cc_profiles_json=json.dumps({"prof-tmpl": profile_with_tmpl}),
    )
    _resolve_cc_session(ctx, profile_ids=["prof-tmpl"])

    # Both calls must use the override and admin_override=True
    assert mock_experts.call_count == 1
    assert mock_experts.call_args.kwargs["override_tmpl_id"] == "tmpl-xyz"
    assert mock_experts.call_args.kwargs["admin_override"] is True
    assert mock_prompts.call_count == 1
    assert mock_prompts.call_args.kwargs["override_tmpl_id"] == "tmpl-xyz"


@patch("services.pipeline.cc_session._server_info", return_value={})
@patch("services.pipeline.cc_session._resolve_template_prompts", return_value=_FAKE_PLANNER)
@patch("services.pipeline.cc_session._resolve_user_experts", side_effect=[None, _FAKE_EXPERTS])
@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[])
def test_missing_template_falls_back_gracefully(mock_profiles, mock_experts, mock_prompts, mock_srv):
    profile_with_tmpl = {**_FAKE_PROFILE, "id": "prof-bad", "expert_template_id": "tmpl-missing"}
    ctx = _make_ctx(
        user_cc_profiles_json=json.dumps({"prof-bad": profile_with_tmpl}),
    )
    session = _resolve_cc_session(ctx, profile_ids=["prof-bad"])

    # Must not crash; experts resolved via fallback (second call without override)
    assert session.profile_not_found is False
    assert session.experts == _FAKE_EXPERTS
    assert mock_experts.call_count == 2


# ── F) User-connection fallback ───────────────────────────────────────────────


@patch("services.pipeline.cc_session._server_info", return_value={})
@patch("services.pipeline.cc_session._resolve_template_prompts", return_value={})
@patch("services.pipeline.cc_session._resolve_user_experts", return_value={})
@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[])
@patch("services.pipeline.cc_session.URL_MAP", {})
def test_user_connection_fallback(mock_profiles, mock_experts, mock_prompts, mock_srv):
    profile = {**_FAKE_PROFILE, "id": "prof-conn", "tool_endpoint": "MY-PRIVATE-NODE"}
    user_conn = {"url": "http://private:11434", "api_key": "secret-tok"}
    ctx = _make_ctx(
        user_cc_profiles_json=json.dumps({"prof-conn": profile}),
        user_connections_json=json.dumps({"MY-PRIVATE-NODE": user_conn}),
    )
    session = _resolve_cc_session(ctx, profile_ids=["prof-conn"])

    assert session.tool_url == "http://private:11434"
    assert session.tool_token == "secret-tok"
    assert session.is_user_conn is True


# ── G) Error / sentinel cases ─────────────────────────────────────────────────


@patch("services.pipeline.cc_session._read_cc_profiles", return_value=[])
def test_profile_not_found_sentinel(mock_profiles):
    ctx = _make_ctx()
    session = _resolve_cc_session(ctx, profile_ids=["nonexistent-prof"])

    assert session.profile_not_found is True


def test_bad_json_in_user_cc_profiles_json_is_safe():
    ctx = _make_ctx(user_cc_profiles_json="not-valid-json{{{")
    with patch("services.pipeline.cc_session._read_cc_profiles", return_value=[]), \
         patch("services.pipeline.cc_session._resolve_user_experts", return_value={}), \
         patch("services.pipeline.cc_session._resolve_template_prompts", return_value={}), \
         patch("services.pipeline.cc_session._server_info", return_value={}):
        session = _resolve_cc_session(ctx, profile_ids=[])

    assert session.profile_not_found is False  # no crash, safe fallback

"""Tests for services/sovereignty.py — the local_only egress guard.

TASK-53: local_only_routing was previously read from AgentState but never
written onto it, so the guard never actually fired in production. These
tests cover the guard's own logic (host classification, precedence) in
isolation from the graph — the dispatch-site wiring is covered by
tests/test_jmoe_debate_judge.py's local_only tests, which exercise the real
expert_worker() entry point.
"""

import pytest

from services.sovereignty import (
    EgressDenied,
    assert_egress_allowed,
    resolve_local_only,
    _host_is_local,
)


def test_host_is_local_private_and_loopback():
    assert _host_is_local("127.0.0.1") is True
    assert _host_is_local("localhost") is True
    assert _host_is_local("192.168.155.224") is True
    assert _host_is_local("10.0.0.5") is True


def test_host_is_local_public_ip_is_not_local():
    # Literal IPs resolve without a real DNS lookup (getaddrinfo short-circuits
    # numeric addresses), so this is deterministic and network-independent.
    assert _host_is_local("1.1.1.1") is False


def test_host_is_local_allowlist_override(monkeypatch):
    monkeypatch.setenv("MOE_LOCAL_ALLOW_HOSTS", "trusted.example.com,other.example.com")
    assert _host_is_local("trusted.example.com") is True
    assert _host_is_local("untrusted.example.com") is False


def test_assert_egress_allowed_noop_when_not_local_only():
    # Must not even attempt host classification when local_only is False.
    assert_egress_allowed("https://openrouter.ai/api/v1", False)


def test_assert_egress_allowed_permits_private_host():
    assert_egress_allowed("http://192.168.155.224:11434", True)


def test_assert_egress_allowed_blocks_public_host():
    with pytest.raises(EgressDenied):
        assert_egress_allowed("https://1.1.1.1/api/v1", True)


def test_assert_egress_allowed_blocks_known_cloud_domain():
    with pytest.raises(EgressDenied):
        assert_egress_allowed("https://openrouter.ai/api/v1", True)


def test_resolve_local_only_permission_flag_wins():
    user_perms = {"moe_mode": ["moe-auto:local-only"]}
    user_ctx = {"local_only_routing": "0"}
    assert resolve_local_only(user_perms, user_ctx) is True


def test_resolve_local_only_key_flag():
    assert resolve_local_only({}, {"local_only_routing": "1"}) is True
    assert resolve_local_only({}, {"local_only_routing": "0"}) is False
    assert resolve_local_only({}, {}) is False


def test_resolve_local_only_global_env_fallback(monkeypatch):
    monkeypatch.setenv("LOCAL_ONLY_COMPLIANCE", "true")
    assert resolve_local_only({}, {}) is True
    monkeypatch.setenv("LOCAL_ONLY_COMPLIANCE", "false")
    assert resolve_local_only({}, {}) is False


def test_resolve_local_only_none_inputs_default_false():
    assert resolve_local_only(None, None) is False

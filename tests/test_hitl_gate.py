"""Tests for services/hitl_gate.py (TASK-14)."""
import pytest
from unittest.mock import MagicMock, patch


def _mock_redis(data_store: dict):
    """Create a fake Redis client backed by a dict."""
    client = MagicMock()

    def _setex(key, ttl, value):
        data_store[key] = (ttl, value)

    def _get(key):
        entry = data_store.get(key)
        return entry[1] if entry else None

    def _ttl(key):
        entry = data_store.get(key)
        return entry[0] if entry else -1

    client.setex.side_effect = _setex
    client.get.side_effect   = _get
    client.ttl.side_effect   = _ttl
    return client


@patch("services.hitl_gate._valkey")
def test_create_gate_returns_id(mock_vk):
    store = {}
    mock_vk.return_value = _mock_redis(store)
    from services.hitl_gate import create_gate
    gate_id = create_gate("req-1", "trust low", "draft response")
    assert gate_id is not None
    assert len(store) == 1


@patch("services.hitl_gate._valkey")
def test_get_gate_returns_data(mock_vk):
    store = {}
    mock_vk.return_value = _mock_redis(store)
    from services.hitl_gate import create_gate, get_gate
    gate_id = create_gate("req-2", "reason", "draft")
    gate = get_gate(gate_id)
    assert gate is not None
    assert gate["status"] == "pending"
    assert gate["reason"] == "reason"


@patch("services.hitl_gate._valkey")
def test_approve_gate(mock_vk):
    store = {}
    mock_vk.return_value = _mock_redis(store)
    from services.hitl_gate import create_gate, approve_gate, get_gate
    gate_id = create_gate("req-3", "reason", "draft")
    ok = approve_gate(gate_id, approved_by="admin")
    assert ok is True
    gate = get_gate(gate_id)
    assert gate["status"] == "approved"


@patch("services.hitl_gate._valkey")
def test_reject_gate(mock_vk):
    store = {}
    mock_vk.return_value = _mock_redis(store)
    from services.hitl_gate import create_gate, reject_gate, get_gate
    gate_id = create_gate("req-4", "reason", "draft")
    ok = reject_gate(gate_id, rejected_by="user-1")
    assert ok is True
    gate = get_gate(gate_id)
    assert gate["status"] == "rejected"


@patch("services.hitl_gate._valkey")
def test_double_decide_is_noop(mock_vk):
    store = {}
    mock_vk.return_value = _mock_redis(store)
    from services.hitl_gate import create_gate, approve_gate, reject_gate
    gate_id = create_gate("req-5", "reason", "draft")
    approve_gate(gate_id)
    # Second decision should fail (already decided)
    ok = reject_gate(gate_id)
    assert ok is False


@patch("services.hitl_gate._valkey")
def test_get_nonexistent_gate(mock_vk):
    mock_vk.return_value = _mock_redis({})
    from services.hitl_gate import get_gate
    assert get_gate("nonexistent-id") is None


@patch("services.hitl_gate._valkey")
def test_fail_open_on_valkey_unavailable(mock_vk):
    mock_vk.return_value = None
    from services.hitl_gate import create_gate
    # Should not raise, returns None
    result = create_gate("req-6", "reason", "draft")
    assert result is None

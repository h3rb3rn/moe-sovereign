"""Tests for services/handover.py (TASK-18)."""
import json
import pytest
from unittest.mock import MagicMock, patch


def _mock_redis():
    store = {}
    client = MagicMock()

    def _setex(key, ttl, value):
        store[key] = value

    def _get(key):
        return store.get(key)

    client.setex.side_effect = _setex
    client.get.side_effect   = _get
    return client, store


@patch("services.handover._valkey")
def test_create_returns_id(mock_vk):
    client, store = _mock_redis()
    mock_vk.return_value = client

    from services.handover import create_handover
    state = {"input": "Hello", "plan": [{"task": "t", "category": "code"}], "mode": "default"}
    handover_id = create_handover(state, reason="STUCK_LOOP")
    assert handover_id is not None
    assert len(store) == 1


@patch("services.handover._valkey")
def test_restore_round_trip(mock_vk):
    client, store = _mock_redis()
    mock_vk.return_value = client

    from services.handover import create_handover, restore_handover
    state = {
        "input": "test query",
        "plan": [{"task": "t", "category": "research"}],
        "trust_verdict": "PROCEED_WITH_ASSUMPTION",
        "agentic_iteration": 2,
    }
    handover_id = create_handover(state, "test reason")
    restored = restore_handover(handover_id)

    assert restored is not None
    assert restored["input"] == "test query"
    assert restored["trust_verdict"] == "PROCEED_WITH_ASSUMPTION"
    assert restored["agentic_iteration"] == 2


@patch("services.handover._valkey")
def test_restore_nonexistent_returns_none(mock_vk):
    mock_vk.return_value = _mock_redis()[0]
    from services.handover import restore_handover
    assert restore_handover("does-not-exist") is None


@patch("services.handover._valkey")
def test_fail_open_on_valkey_unavailable(mock_vk):
    mock_vk.return_value = None
    from services.handover import create_handover
    result = create_handover({"input": "x"}, reason="stuck")
    assert result is None


@patch("services.handover._valkey")
def test_only_preserved_keys_saved(mock_vk):
    client, store = _mock_redis()
    mock_vk.return_value = client

    from services.handover import create_handover
    state = {
        "input": "q",
        "plan": [],
        "final_response": "secret response",  # NOT in _PRESERVED_KEYS
        "mode": "default",
    }
    handover_id = create_handover(state, "test")
    raw = list(store.values())[0]
    payload = json.loads(raw)
    assert "final_response" not in payload["snapshot"]
    assert "input" in payload["snapshot"]

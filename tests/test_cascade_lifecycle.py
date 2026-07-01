"""Tests for TASK-16: Cascade Event Resolution Tracking."""
import json
import pytest
from unittest.mock import MagicMock, patch
from services.cascade import (
    CascadeEvent, CascadeType, classify_gap,
    emit_cascade, resolve_cascade, list_open_cascades,
)


def _mock_redis():
    store = {}
    client = MagicMock()

    def _setex(key, ttl, value):
        store[key] = value

    def _get(key):
        return store.get(key)

    def _ttl(key):
        return 86000

    client.setex.side_effect = _setex
    client.get.side_effect   = _get
    client.ttl.side_effect   = _ttl
    return client, store


def test_classify_gap_returns_context_gap_by_default():
    ev = classify_gap("Need more information about this topic")
    assert ev.cascade_type == CascadeType.CONTEXT_GAP


def test_classify_gap_detects_expert_failure():
    ev = classify_gap("cannot access the internet to retrieve data")
    assert ev.cascade_type == CascadeType.EXPERT_FAILURE


def test_classify_gap_detects_contradiction():
    ev = classify_gap("experts contradict each other on this")
    assert ev.cascade_type == CascadeType.CONTRADICTION


def test_classify_gap_complete():
    ev = classify_gap("COMPLETE")
    assert ev.cascade_type == CascadeType.COMPLETE


@patch("services.cascade._valkey")
def test_emit_cascade_stores_in_valkey(mock_vk):
    client, store = _mock_redis()
    mock_vk.return_value = client

    ev = CascadeEvent(CascadeType.CONTEXT_GAP, "missing data", "try GraphRAG")
    emit_cascade(ev, request_id="req-abc")

    assert "cascade_events:req-abc" in store
    events = json.loads(store["cascade_events:req-abc"])
    assert len(events) == 1
    assert events[0]["resolved"] is False


@patch("services.cascade._valkey")
def test_resolve_cascade_updates_valkey(mock_vk):
    client, store = _mock_redis()
    mock_vk.return_value = client

    ev = CascadeEvent(CascadeType.EXPERT_FAILURE, "tool failed", "retry")
    emit_cascade(ev, request_id="req-def")
    resolve_cascade(ev)

    events = json.loads(store["cascade_events:req-def"])
    assert events[0]["resolved"] is True
    assert events[0]["resolved_at"] is not None


@patch("services.cascade._valkey")
def test_list_open_cascades_filters_resolved(mock_vk):
    client, store = _mock_redis()
    mock_vk.return_value = client

    ev1 = CascadeEvent(CascadeType.CONTEXT_GAP, "gap1", "hint1")
    ev2 = CascadeEvent(CascadeType.TOOL_FAILURE, "gap2", "hint2")
    emit_cascade(ev1, request_id="req-xyz")
    emit_cascade(ev2, request_id="req-xyz")
    resolve_cascade(ev1)

    open_events = list_open_cascades("req-xyz")
    assert len(open_events) == 1
    assert open_events[0]["cascade_type"] == "TOOL_FAILURE"


@patch("services.cascade._valkey")
def test_list_open_cascades_fail_open(mock_vk):
    mock_vk.return_value = None
    result = list_open_cascades("req-no-valkey")
    assert result == []

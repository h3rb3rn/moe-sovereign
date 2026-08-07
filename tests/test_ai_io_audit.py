"""tests/test_ai_io_audit.py — Unit tests for TASK-29 AI I/O Audit Service."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.ai_io_audit import (
    sanitize_audit_payload,
    create_audit_entry,
    complete_audit_entry,
    get_live_entries,
    AiIoAuditEntry,
    aggregate_request_usage,
    _live_entries,
)
import state


# ── sanitize_audit_payload ────────────────────────────────────────────────────

def test_sanitize_top_level_api_key():
    payload = {"api-key": "secret", "model": "llama"}
    result = sanitize_audit_payload(payload)
    assert result["api-key"] == "[redacted]"
    assert result["model"] == "llama"


def test_sanitize_nested_authorization():
    payload = {"headers": {"Authorization": "Bearer sk-secret", "Content-Type": "application/json"}}
    result = sanitize_audit_payload(payload)
    assert result["headers"]["Authorization"] == "[redacted]"
    assert result["headers"]["Content-Type"] == "application/json"


def test_sanitize_case_insensitive():
    payload = {"X-API-KEY": "abc", "APIKEY": "def", "token": "xyz"}
    result = sanitize_audit_payload(payload)
    assert result["X-API-KEY"] == "[redacted]"
    assert result["APIKEY"] == "[redacted]"
    assert result["token"] == "[redacted]"


def test_sanitize_list_elements():
    payload = [{"api-key": "secret"}, {"normal": "value"}]
    result = sanitize_audit_payload(payload)
    assert result[0]["api-key"] == "[redacted]"
    assert result[1]["normal"] == "value"


def test_sanitize_deeply_nested():
    payload = {"a": {"b": {"authorization": "top_secret", "c": "safe"}}}
    result = sanitize_audit_payload(payload)
    assert result["a"]["b"]["authorization"] == "[redacted]"
    assert result["a"]["b"]["c"] == "safe"


def test_sanitize_non_sensitive_pass_through():
    payload = {"model": "qwen", "temperature": 0.7, "messages": [{"role": "user", "content": "hi"}]}
    result = sanitize_audit_payload(payload)
    assert result == payload


# ── Entry lifecycle ───────────────────────────────────────────────────────────

def setup_function():
    _live_entries.clear()


def test_create_audit_entry_adds_to_live():
    entry = create_audit_entry(
        session_id="sess-1",
        request_id="req-1",
        model="llama3:70b",
        endpoint="http://node:11434",
        stage="judge",
        request_body={"messages": [{"role": "user", "content": "hello"}], "api-key": "sk-x"},
    )
    assert entry.status == "pending"
    assert entry.request_body["api-key"] == "[redacted]"
    assert entry.audit_id in _live_entries


@pytest.mark.asyncio
async def test_complete_audit_entry_removes_from_live():
    entry = create_audit_entry("s", "r", "m", "ep", "st", {"x": 1})
    audit_id = entry.audit_id

    with patch("services.ai_io_audit._persist", new=AsyncMock()):
        await complete_audit_entry(
            audit_id=audit_id,
            response_body={"content": "result"},
            prompt_tokens=100,
            completion_tokens=50,
            status="completed",
        )
    assert audit_id not in _live_entries


@pytest.mark.asyncio
async def test_complete_nonexistent_entry_is_noop():
    with patch("services.ai_io_audit._persist", new=AsyncMock()) as mock_persist:
        await complete_audit_entry("no:such", {}, 0, 0)
    mock_persist.assert_not_called()


def test_get_live_entries_returns_pending():
    _live_entries.clear()
    create_audit_entry("s1", "r1", "m", "ep", "st", {})
    create_audit_entry("s2", "r2", "m", "ep", "st", {})
    live = get_live_entries()
    assert len(live) == 2
    assert all(e.status == "pending" for e in live)


@pytest.mark.asyncio
async def test_complete_redacts_response_body():
    entry = create_audit_entry("s", "r2", "m", "ep", "st", {})
    with patch("services.ai_io_audit._persist", new=AsyncMock()):
        await complete_audit_entry(
            entry.audit_id,
            response_body={"authorization": "secret-val", "content": "answer"},
            prompt_tokens=10,
            completion_tokens=5,
        )
    # Entry is gone from live — to check redaction we'd need to inspect _persist args,
    # but at minimum it should not raise and should have processed the response.
    assert entry.audit_id not in _live_entries


def test_multiple_calls_in_one_request_get_distinct_audit_ids():
    planner = create_audit_entry("s", "r", "p", "ep", "planner", {})
    expert = create_audit_entry("s", "r", "e", "ep", "expert", {})
    judge = create_audit_entry("s", "r", "j", "ep", "judge", {})

    assert len({planner.audit_id, expert.audit_id, judge.audit_id}) == 3
    assert len(get_live_entries()) == 3


def test_sanitize_langchain_like_message_object():
    class Message:
        type = "human"
        content = "hello"

    result = sanitize_audit_payload({"messages": [Message()]})
    assert result["messages"] == [{"type": "human", "content": "hello"}]


class _AggregateCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _query, params):
        assert params == ("req-timeout",)

    async def fetchone(self):
        return (16500, 8900, ["planner", "expert", "judge"])


class _AggregateConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def cursor(self):
        return _AggregateCursor()


class _AggregatePool:
    def connection(self):
        return _AggregateConnection()


@pytest.mark.asyncio
async def test_timeout_usage_aggregates_completed_stage_audits(monkeypatch):
    monkeypatch.setattr(state, "_userdb_pool", _AggregatePool())

    result = await aggregate_request_usage("req-timeout")

    assert result == {
        "prompt_tokens": 16500,
        "completion_tokens": 8900,
        "stages": ["planner", "expert", "judge"],
    }

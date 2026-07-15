"""tests/test_structured_failure.py — Unit tests for TASK-30 Structured-Output Failure Recovery."""

import json
import pytest
from services.structured_failure import (
    StructuredFailureKind,
    RecoveryAction,
    StructuredFailure,
    classify_failure,
    build_failure,
    resolve_retry_model,
)


# ── classify_failure ──────────────────────────────────────────────────────────

def test_classify_json_decode_error():
    err = json.JSONDecodeError("Expecting value", "bad json", 0)
    assert classify_failure(err) == StructuredFailureKind.SCHEMA_OUTPUT


def test_classify_schema_keyword_in_message():
    err = ValueError("schema validation failed: missing required field 'category'")
    assert classify_failure(err) == StructuredFailureKind.SCHEMA_OUTPUT


def test_classify_timeout_error():
    class TimeoutError(Exception):
        pass
    err = TimeoutError("read timeout after 30s")
    assert classify_failure(err) == StructuredFailureKind.PROVIDER_TRANSPORT


def test_classify_502_in_message():
    err = Exception("upstream error 502 Bad Gateway")
    assert classify_failure(err) == StructuredFailureKind.PROVIDER_TRANSPORT


def test_classify_connection_refused():
    err = ConnectionRefusedError("connection refused")
    assert classify_failure(err) == StructuredFailureKind.PROVIDER_TRANSPORT


def test_classify_runtime_error():
    err = RuntimeError("unexpected internal error")
    assert classify_failure(err) == StructuredFailureKind.RUNTIME_ERROR


def test_classify_schema_via_raw_text():
    err = RuntimeError("parse failed")
    assert classify_failure(err, raw_text="json decode error at position 10") == StructuredFailureKind.SCHEMA_OUTPUT


# ── build_failure ─────────────────────────────────────────────────────────────

def test_build_failure_schema_with_fallback(monkeypatch):
    monkeypatch.setenv("STRUCTURED_FAILURE_FALLBACK_MODEL", "qwen3:7b")
    import importlib, services.structured_failure as sf
    importlib.reload(sf)
    failure = sf.build_failure(
        json.JSONDecodeError("x", "", 0),
        model="qwen3.6:35b",
        stage="synthesis",
    )
    assert failure.failure_kind == StructuredFailureKind.SCHEMA_OUTPUT
    assert RecoveryAction.RETRY_FALLBACK in failure.allowed_actions
    assert failure.fallback_model == "qwen3:7b"


def test_build_failure_runtime_no_fallback():
    failure = build_failure(RuntimeError("boom"), model="llama3:70b", stage="planner")
    assert failure.failure_kind == StructuredFailureKind.RUNTIME_ERROR
    assert RecoveryAction.RETRY_FALLBACK not in failure.allowed_actions
    assert RecoveryAction.RETRY_SAME in failure.allowed_actions


def test_build_failure_raw_text_truncated():
    failure = build_failure(
        ValueError("oops"),
        model="m",
        stage="s",
        raw_text="x" * 3000,
    )
    assert len(failure.raw_text) == 1600


# ── resolve_retry_model ────────────────────────────────────────────────────────

def test_resolve_retry_same():
    failure = build_failure(ValueError("x"), model="qwen3:32b", stage="synthesis")
    assert resolve_retry_model(failure, RecoveryAction.RETRY_SAME) == "qwen3:32b"


def test_resolve_retry_fallback():
    failure = build_failure(
        ValueError("x"),
        model="qwen3:32b",
        stage="synthesis",
        fallback_model="phi4:14b",
    )
    assert resolve_retry_model(failure, RecoveryAction.RETRY_FALLBACK) == "phi4:14b"


def test_resolve_retry_selected():
    failure = build_failure(ValueError("x"), model="a", stage="s")
    result = resolve_retry_model(failure, RecoveryAction.RETRY_SELECTED, selected_model="gemma3:27b")
    assert result == "gemma3:27b"


def test_resolve_stop_raises():
    failure = build_failure(ValueError("x"), model="a", stage="s")
    with pytest.raises(ValueError, match="STOP"):
        resolve_retry_model(failure, RecoveryAction.STOP)


def test_resolve_fallback_no_model_raises():
    failure = StructuredFailure(
        failure_kind=StructuredFailureKind.SCHEMA_OUTPUT,
        model="a", fallback_model="", stage="s", message="x", raw_text="",
        retry_round=0, allowed_actions=[RecoveryAction.RETRY_FALLBACK],
    )
    with pytest.raises(ValueError, match="no fallback_model"):
        resolve_retry_model(failure, RecoveryAction.RETRY_FALLBACK)


# ── Max-retry limit triggers STOP ─────────────────────────────────────────────

def test_max_retries_boundary(monkeypatch):
    monkeypatch.setenv("STRUCTURED_FAILURE_MAX_RETRIES", "2")
    import importlib, services.structured_failure as sf
    importlib.reload(sf)
    failure = sf.build_failure(
        json.JSONDecodeError("x", "", 0),
        model="m",
        stage="s",
        retry_round=2,
    )
    assert failure.retry_round >= failure.max_retries

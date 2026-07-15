"""Tests for services/decision_log.py (TASK-12)."""
import json
import os
import tempfile

import pytest

from services.decision_log import DecisionType, log_decision


def test_empty_rationale_raises():
    with pytest.raises(ValueError, match="rationale"):
        log_decision(DecisionType.CONSTITUTION_BLOCK, "req-1", rationale="")


def test_whitespace_rationale_raises():
    with pytest.raises(ValueError, match="rationale"):
        log_decision(DecisionType.DOR_FAIL, "req-2", rationale="   ")


def test_writes_to_fallback_jsonl(tmp_path, monkeypatch):
    log_path = str(tmp_path / "decisions.jsonl")
    monkeypatch.setenv("DECISION_LOG_PATH", log_path)
    # Reload module-level constant
    import importlib
    import services.decision_log as dl
    monkeypatch.setattr(dl, "_FALLBACK_LOG_PATH", log_path)

    log_decision(DecisionType.REPLAN, "req-3", rationale="Replan because context gap", metadata={"round": 2})

    with open(log_path) as f:
        entry = json.loads(f.read().strip())

    assert entry["decision_type"] == "REPLAN"
    assert entry["request_id"] == "req-3"
    assert "context gap" in entry["rationale"]
    assert entry["round"] == 2
    assert "ts" in entry


def test_all_decision_types_valid(tmp_path, monkeypatch):
    log_path = str(tmp_path / "all_types.jsonl")
    import services.decision_log as dl
    monkeypatch.setattr(dl, "_FALLBACK_LOG_PATH", log_path)

    for dtype in DecisionType:
        log_decision(dtype, f"req-{dtype}", rationale=f"Test rationale for {dtype}")

    with open(log_path) as f:
        lines = f.readlines()

    assert len(lines) == len(DecisionType)


def test_metadata_included(tmp_path, monkeypatch):
    log_path = str(tmp_path / "meta.jsonl")
    import services.decision_log as dl
    monkeypatch.setattr(dl, "_FALLBACK_LOG_PATH", log_path)

    log_decision(
        DecisionType.CONSTITUTION_BLOCK,
        "req-5",
        rationale="Blocked by rule no_external_egress",
        metadata={"rule_id": "no_external_egress", "score": 0.0},
    )

    with open(log_path) as f:
        entry = json.loads(f.read().strip())

    assert entry["rule_id"] == "no_external_egress"
    assert entry["score"] == 0.0

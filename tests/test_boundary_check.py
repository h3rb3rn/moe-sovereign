"""Tests for services/boundary_check.py (TASK-13)."""
import pytest
from unittest.mock import patch
from services.boundary_check import (
    BoundaryConfigurationError,
    check_boundary,
    validate_boundary_contracts,
)


def test_planner_to_expert_valid():
    payload = {"task": "Explain GraphQL", "category": "research", "search_query": "graphql tutorial"}
    violations = check_boundary("planner_to_expert", payload)
    assert violations == []


def test_planner_to_expert_missing_task():
    payload = {"category": "research"}
    violations = check_boundary("planner_to_expert", payload)
    assert any("task" in v for v in violations)


def test_planner_to_expert_missing_category():
    payload = {"task": "Do something"}
    violations = check_boundary("planner_to_expert", payload)
    assert any("category" in v for v in violations)


def test_planner_to_expert_empty_string_field():
    payload = {"task": "   ", "category": "code"}
    violations = check_boundary("planner_to_expert", payload)
    assert any("task" in v for v in violations)


def test_expert_to_judge_valid():
    payload = {"content": "[RESEARCH / llama3]: GraphQL is a query language.", "category": "research"}
    violations = check_boundary("expert_to_judge", payload)
    assert violations == []


def test_expert_to_judge_missing_content():
    payload = {"category": "research"}
    violations = check_boundary("expert_to_judge", payload)
    assert any("content" in v for v in violations)


def test_unknown_stage_fails_closed():
    with pytest.raises(BoundaryConfigurationError, match="No mandatory boundary"):
        check_boundary("nonexistent_stage", {"any": "data"})


def test_bad_payload_type_is_a_boundary_violation():
    violations = check_boundary("planner_to_expert", None)  # type: ignore
    assert any("Invalid payload type" in violation for violation in violations)


def test_invalid_contract_document_fails_closed(tmp_path, monkeypatch):
    contract_path = tmp_path / "boundary_contracts.yaml"
    contract_path.write_text("stages: []\n", encoding="utf-8")
    monkeypatch.setattr("services.boundary_check._CONTRACTS_PATH", str(contract_path))

    with pytest.raises(BoundaryConfigurationError, match="non-empty 'stages'"):
        validate_boundary_contracts(force_reload=True)


def test_missing_contract_document_fails_closed(tmp_path, monkeypatch):
    missing_path = tmp_path / "missing.yaml"
    monkeypatch.setattr("services.boundary_check._CONTRACTS_PATH", str(missing_path))

    with pytest.raises(BoundaryConfigurationError, match="Failed to load mandatory"):
        validate_boundary_contracts(force_reload=True)


def test_boundary_violation_emits_persisted_cascade_with_request_id():
    with patch("services.cascade.emit_cascade") as emit:
        violations = check_boundary(
            "planner_to_expert",
            {"category": "research"},
            request_id="req-boundary",
        )

    assert violations
    event = emit.call_args.args[0]
    assert event.cascade_type.value == "SPEC_GAP"
    assert emit.call_args.kwargs["request_id"] == "req-boundary"

"""Tests for services/boundary_check.py (TASK-13)."""
import pytest
from services.boundary_check import check_boundary


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


def test_unknown_stage_is_noop():
    violations = check_boundary("nonexistent_stage", {"any": "data"})
    assert violations == []


def test_fail_open_on_bad_input():
    # Should not raise even with completely wrong types
    violations = check_boundary("planner_to_expert", None)  # type: ignore
    assert isinstance(violations, list)

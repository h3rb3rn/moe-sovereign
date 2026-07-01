"""Tests for services/scope_guard.py (TASK-17)."""
import pytest
from services.scope_guard import check_scope, ScopeViolation


def test_same_category_allowed():
    task = {"task": "solve math problem", "category": "math"}
    assert check_scope(task, "math") is None


def test_different_category_blocked():
    task = {"task": "write code", "category": "code"}
    violation = check_scope(task, "math")
    assert violation is not None
    assert isinstance(violation, ScopeViolation)
    assert violation.requested_domain == "math"
    assert "code" in violation.allowed_domains


def test_allowed_domains_whitelist_passes():
    task = {"task": "multi-domain", "category": "code", "allowed_domains": ["code", "math", "research"]}
    assert check_scope(task, "math") is None
    assert check_scope(task, "research") is None


def test_allowed_domains_whitelist_blocks():
    task = {"task": "limited scope", "category": "code", "allowed_domains": ["code"]}
    violation = check_scope(task, "medical_consult")
    assert violation is not None


def test_universal_categories_always_pass():
    task = {"task": "anything", "category": "code"}
    assert check_scope(task, "general") is None
    assert check_scope(task, "precision_tools") is None


def test_general_category_task_allows_any():
    task = {"task": "generic task", "category": "general"}
    # general is universal, so no violation
    assert check_scope(task, "general") is None


def test_violation_message_contains_details():
    task = {"task": "write a poem", "category": "creative"}
    violation = check_scope(task, "legal_advisor")
    assert "legal_advisor" in violation.message
    assert "creative" in violation.message

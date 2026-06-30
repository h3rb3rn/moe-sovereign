"""Tests for services/cynefin.py (TASK-15)."""
import pytest
from services.cynefin import CynefinDomain, classify_cynefin


def _state(**kwargs):
    base = {
        "trust_verdict": "PROCEED",
        "complexity_level": "trivial",
        "plan": [],
        "enable_graphrag": False,
        "input": "Hello",
    }
    base.update(kwargs)
    return base


def test_clear_for_trivial_single_domain():
    state = _state(complexity_level="trivial", plan=[{"category": "research", "task": "t"}])
    assert classify_cynefin(state) == CynefinDomain.CLEAR


def test_complicated_for_moderate():
    state = _state(complexity_level="moderate", plan=[{"category": "code", "task": "t"}])
    assert classify_cynefin(state) == CynefinDomain.COMPLICATED


def test_complicated_for_graphrag_enabled():
    state = _state(complexity_level="trivial", enable_graphrag=True,
                   plan=[{"category": "research", "task": "t"}])
    assert classify_cynefin(state) == CynefinDomain.COMPLICATED


def test_complex_for_complex_level():
    state = _state(complexity_level="complex", plan=[{"category": "code", "task": "t"}])
    assert classify_cynefin(state) == CynefinDomain.COMPLEX


def test_complex_for_many_domains():
    plan = [{"category": cat, "task": f"t{i}"} for i, cat in
            enumerate(["code", "research", "math", "legal_advisor"])]
    state = _state(complexity_level="moderate", plan=plan)
    assert classify_cynefin(state) == CynefinDomain.COMPLEX


def test_chaotic_on_block():
    state = _state(trust_verdict="BLOCK", complexity_level="trivial")
    assert classify_cynefin(state) == CynefinDomain.CHAOTIC


def test_chaotic_overrides_complexity():
    state = _state(trust_verdict="BLOCK", complexity_level="trivial",
                   plan=[{"category": "code", "task": "t"}])
    assert classify_cynefin(state) == CynefinDomain.CHAOTIC


def test_clear_empty_plan():
    assert classify_cynefin(_state()) == CynefinDomain.CLEAR

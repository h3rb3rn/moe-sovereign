"""Tests for TASK-22: Strategy Review Node."""
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_llm_response(content: str):
    resp = MagicMock()
    resp.content = content
    return resp


def _state(**kwargs):
    base = {
        "trust_verdict": "PROCEED_WITH_ASSUMPTION",
        "cynefin_domain": "COMPLEX",
        "expert_results": ["[CODE / model]: def solve(): return 42"],
        "plan": [{"task": "implement solution", "category": "code"}],
        "input": "How to implement a ring buffer for audio?",
        "trust_score": 0.45,
        "strategy_feedback": "",
    }
    base.update(kwargs)
    return base


# ── Activation logic ──────────────────────────────────────────────────────────

def test_node_inactive_without_env(monkeypatch):
    monkeypatch.delenv("STRATEGY_REVIEW_ENABLED", raising=False)
    from graph.strategy_review_node import _should_activate
    assert _should_activate(_state()) is False


def test_node_active_with_env(monkeypatch):
    monkeypatch.setenv("STRATEGY_REVIEW_ENABLED", "true")
    from graph.strategy_review_node import _should_activate
    assert _should_activate(_state(trust_verdict="PROCEED_WITH_ASSUMPTION")) is True


def test_node_inactive_on_proceed(monkeypatch):
    monkeypatch.setenv("STRATEGY_REVIEW_ENABLED", "true")
    from graph.strategy_review_node import _should_activate
    assert _should_activate(_state(trust_verdict="PROCEED", cynefin_domain="CLEAR")) is False


def test_node_active_on_chaotic(monkeypatch):
    monkeypatch.setenv("STRATEGY_REVIEW_ENABLED", "true")
    from graph.strategy_review_node import _should_activate
    assert _should_activate(_state(trust_verdict="PROCEED", cynefin_domain="CHAOTIC")) is True


# ── Abstraction content boundary ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reviewer_prompt_has_no_expert_content():
    """Core invariant: reviewer LLM is never called with domain content."""
    from services.strategy_review import review_strategy, StrategyAbstract

    received_prompts = []

    async def capture_invoke(prompt):
        received_prompts.append(prompt)
        return _make_llm_response(json.dumps({
            "structural_gaps": [],
            "alternative_approaches": [],
            "confidence_adjustment": 0.0,
            "summary": "ok"
        }))

    reviewer = MagicMock()
    reviewer.ainvoke = capture_invoke

    abstract = StrategyAbstract(
        problem_class="audio-buffer-management",
        solution_approach="ring-buffer with emulator write-path",
        assumptions=["emulator writes at fixed rate"],
        uncertainties=["buffer overflow handling unclear"],
    )
    await review_strategy(abstract, reviewer)

    assert len(received_prompts) == 1
    prompt = received_prompts[0]
    # Expert content must not appear in reviewer prompt
    assert "def solve()" not in prompt
    assert "[CODE / model]" not in prompt
    # Abstract fields must appear
    assert "audio-buffer-management" in prompt
    assert "ring-buffer" in prompt


# ── Confidence adjustment clamping ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_confidence_adjustment_clamped():
    from services.strategy_review import review_strategy, StrategyAbstract

    async def extreme_llm(prompt):
        return _make_llm_response(json.dumps({
            "structural_gaps": [],
            "alternative_approaches": [],
            "confidence_adjustment": 99.9,  # Out-of-range
            "summary": "extreme"
        }))

    reviewer = MagicMock()
    reviewer.ainvoke = extreme_llm

    abstract = StrategyAbstract("test", "approach", [], [])
    feedback = await review_strategy(abstract, reviewer)
    assert -0.2 <= feedback.confidence_adjustment <= 0.2


# ── Reviewer endpoint routing ─────────────────────────────────────────────────

def test_reviewer_uses_judge_when_no_url(monkeypatch):
    monkeypatch.delenv("STRATEGY_REVIEWER_URL", raising=False)
    from services.strategy_review import build_reviewer_llm

    # _get_judge_llm is imported lazily inside the function from graph.synthesis
    with patch("graph.synthesis._get_judge_llm") as mock_judge:
        mock_judge.return_value = MagicMock()
        build_reviewer_llm({})
        mock_judge.assert_called_once()


def test_reviewer_uses_custom_url(monkeypatch):
    monkeypatch.setenv("STRATEGY_REVIEWER_URL", "https://api.openai.com")
    monkeypatch.setenv("STRATEGY_REVIEWER_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("STRATEGY_REVIEWER_TOKEN", "sk-test")
    from services.strategy_review import build_reviewer_llm

    # ChatOpenAI is imported lazily inside the function from langchain_openai
    with patch("langchain_openai.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        build_reviewer_llm({})
        MockLLM.assert_called_once()
        call_kwargs = MockLLM.call_args[1]
        assert "openai.com" in call_kwargs["base_url"]
        assert call_kwargs["model"] == "gpt-4o-mini"

"""The planner fast-path must remain narrower than precision/retrieval routes."""

from pathlib import Path

import pytest

from graph.planner import _trivial_fast_path_eligible
from services.trivial_fast_path import is_moe_auto_preflight_eligible


@pytest.mark.parametrize(
    "query",
    [
        "Antworte ausschließlich mit: OK",
        "Say hello in one sentence",
        "Fasse diesen kurzen Satz knapp zusammen",
    ],
)
def test_unambiguous_one_shot_prompt_is_eligible(query):
    assert _trivial_fast_path_eligible(
        {"input": query, "mode": "default"},
        "trivial",
    )


@pytest.mark.parametrize(
    "query",
    [
        "Was ist 2+2?",
        "Berechne 15 Prozent von 80",
        "Was bedeutet § 242 BGB?",
        "Wie ist das Wetter heute?",
        "Suche das neueste Paper zu GraphRAG",
        "Analysiere die angehängte PDF-Datei",
        "Welche Version ist aktuell?",
        "Subnetz für 10.0.0.0/24 berechnen",
        "Welcher Wochentag ist der 29.07.2026?",
        "What is the GCD of 391 and 299?",
    ],
)
def test_precision_current_legal_and_file_prompts_are_not_eligible(query):
    assert not _trivial_fast_path_eligible(
        {"input": query, "mode": "default"},
        "trivial",
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"chat_history": [{"role": "user", "content": "Earlier"}]},
        {"images": ["data:image/png;base64,abc"]},
        {"attachments": [{"name": "a.txt"}]},
        {"system_prompt": "Use this project context"},
        {"tools": [{"type": "function"}]},
        {"user_experts": {"legal_advisor": {}}},
        {"mode": "code"},
    ],
)
def test_contextual_or_specialized_requests_are_not_eligible(extra):
    state = {"input": "Antworte mit OK", "mode": "default", **extra}
    assert not _trivial_fast_path_eligible(state, "trivial")


def test_only_trivial_complexity_is_eligible():
    assert not _trivial_fast_path_eligible(
        {"input": "Antworte mit OK", "mode": "default"},
        "moderate",
    )


def test_moe_auto_preflight_matches_graph_gate_for_safe_prompt():
    assert is_moe_auto_preflight_eligible(
        "Antworte ausschließlich mit: OK",
        "trivial",
        mode="auto",
        has_history=False,
        has_multimodal=False,
        system_prompt="",
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"has_history": True},
        {"has_multimodal": True},
        {"system_prompt": "Project context"},
        {"tools": [{"type": "function"}]},
        {"files": [{"id": "file-1"}]},
        {"mode": "agent_orchestrated"},
        {"mode": "research"},
    ],
)
def test_moe_auto_preflight_keeps_contextual_requests_on_full_path(extra):
    kwargs = {
        "mode": "default",
        "has_history": False,
        "has_multimodal": False,
        "system_prompt": "",
        **extra,
    }
    assert not is_moe_auto_preflight_eligible(
        "Antworte ausschließlich mit: OK",
        "trivial",
        **kwargs,
    )


def test_chat_pipeline_consumes_preflight_before_dynamic_and_template_resolution():
    source = (
        Path(__file__).parents[1] / "services" / "pipeline" / "chat.py"
    ).read_text(encoding="utf-8")
    preflight = source.index("_moe_auto_trivial_preflight = is_moe_auto_preflight_eligible(")
    dynamic = source.index("dynamic_tmpl = await get_dynamic_template(")
    template_resolution = source.index("if _moe_auto_trivial_preflight:", dynamic)
    assert preflight < dynamic < template_resolution
    assert "and not _moe_auto_trivial_preflight" in source[preflight:dynamic]
    assert "user_experts = {}" in source[template_resolution:]


def test_planner_fast_path_propagates_all_downstream_skip_flags():
    source = (
        Path(__file__).parents[1] / "graph" / "planner.py"
    ).read_text(encoding="utf-8")
    state_update = source.index("_complexity_state_update = {")
    fast_path = source.index("# ── Trivial fast-path", state_update)
    fast_return = source.index("**_complexity_state_update", fast_path)
    assert '"skip_research":' in source[state_update:fast_path]
    assert '"skip_graph":' in source[state_update:fast_path]
    assert '"skip_thinking":' in source[state_update:fast_path]
    assert state_update < fast_path < fast_return
    assert '"trivial_fast_path": True' in source[fast_return:]


def test_merger_does_not_rejudge_short_trivial_direct_answer():
    source = (
        Path(__file__).parents[1] / "graph" / "synthesis.py"
    ).read_text(encoding="utf-8")
    direct_gate = source.index("_is_trivial_direct = (")
    refinement = source.index("if _max_refine > 0 and expert_results:", direct_gate)
    response_gate = source.index("_single_expert_modes =", refinement)
    assert "len(ensemble_results) == 1" in source[direct_gate:refinement]
    assert "expert_results = [ensemble_results[0]]" in source[direct_gate:refinement]
    assert "_max_refine = 0" in source[direct_gate:refinement]
    assert '"auto"' in source[response_gate:response_gate + 100]
    assert "or _is_trivial_direct" in source[response_gate:]
    assert '"verified_single_expert"' in source[response_gate:]
    assert "_enforce_direct_response(" in source[response_gate:]
    assert "**_trust_state" in source[response_gate:]

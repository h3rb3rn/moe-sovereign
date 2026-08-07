import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import json
from types import SimpleNamespace

from graph.expert import expert_worker
from graph.synthesis import resolve_conflicts_node

# A dummy response object mimicking ChatOpenAI/AIMessage behavior
class DummyAIMessage:
    def __init__(self, content, prompt_tokens=10, completion_tokens=20):
        self.content = content
        self.usage_metadata = {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens
        }

@pytest.mark.asyncio
async def test_jmoe_debate_enabled_flow():
    """Verify that when JMOE_DEBATE_ENABLED is True and multiple experts are available,
    the debate flow runs and registers a conflict if they diverge."""
    state = {
        "plan": [
            {"category": "medical_consult", "task": "Diagnose chest pain"}
        ],
        "user_experts": {
            "medical_consult": [
                {"model": "gpt-4o", "endpoint": "TESLA", "enabled": True},
                {"model": "gpt-3.5-turbo", "endpoint": "TESLA", "enabled": True}
            ]
        },
        "chat_history": [],
        "mode": "default",
        "conflict_registry": []
    }

    # Proponent initial response, skeptic critique, proponent final response
    responses = [
        DummyAIMessage("CONFIDENCE: high\nDiagnose: patient has heartburn. Avoid coffee."),
        DummyAIMessage("CONFIDENCE: high\nCritique: This is dangerous. Chest pain can be a myocardial infarction. Recommend immediate ECG!"),
        DummyAIMessage("CONFIDENCE: high\nRefined: Accepting critique. Check ECG first to rule out myocardial infarction, otherwise avoid coffee.")
    ]
    
    mock_invoke = AsyncMock()
    # Mock calls sequentially
    mock_invoke.side_effect = [(res, False) for res in responses]

    with patch("config.JMOE_DEBATE_ENABLED", True), \
         patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "TESLA", "url": "http://test/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)
        
        # Verify debate transcript is returned
        assert "expert_results" in result
        expert_res = result["expert_results"]
        assert len(expert_res) == 1
        content = expert_res[0]
        assert "[DEBATE] Proponent:" in content
        assert "### Proponent Initial Answer:" in content
        assert "### Skeptic Critique:" in content
        assert "### Proponent Final Rebutted Answer:" in content
        
        # Check conflict registry has registered a conflict due to high improvement/divergence ratio
        assert "conflict_registry" in result
        conflicts = result["conflict_registry"]
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict["category"] == "medical_consult"
        assert conflict["divergence_score"] >= 0.35
        assert conflict["resolution"] == "pending"


@pytest.mark.asyncio
async def test_micro_debate_budget_is_shared_across_parallel_planner_tasks():
    """A three-call policy must not become three calls per planner task."""

    state = {
        "input": "Review two independent concerns.",
        "plan": [
            {"id": "a", "category": "code_reviewer", "task": "Review code"},
            {"id": "b", "category": "data_analyst", "task": "Review data"},
        ],
        "user_experts": {
            category: [
                {"model": f"{category}-a", "endpoint": "TESLA", "enabled": True},
                {"model": f"{category}-b", "endpoint": "TESLA", "enabled": True},
            ]
            for category in ("code_reviewer", "data_analyst")
        },
        "chat_history": [],
        "mode": "default",
        "complexity_level": "complex",
        "cynefin_domain": "COMPLEX",
        "conflict_registry": [],
        "deliberation_policy": {
            "activation": "required",
            "mode": "micro",
            "max_model_calls": 3,
        },
    }
    mock_invoke = AsyncMock(
        return_value=(DummyAIMessage("CONFIDENCE: high\nBounded result."), False)
    )

    with patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "TESLA", "url": "http://test/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)

    assert sum("[DEBATE]" in item for item in result["expert_results"]) == 1
    assert mock_invoke.await_count == 5  # 3 debate calls + 2 standard fallback calls
    assert any(
        event["event"] == "micro_debate_budget_exhausted"
        for event in result["deliberation_events"]
    )

@pytest.mark.asyncio
async def test_jmoe_debate_disabled_flow():
    """Verify that when JMOE_DEBATE_ENABLED is False, standard parallel or sequential execution runs instead of debate."""
    state = {
        "plan": [
            {"category": "medical_consult", "task": "Diagnose chest pain"}
        ],
        "user_experts": {
            "medical_consult": [
                {"model": "gpt-4o", "endpoint": "TESLA", "enabled": True, "_tier": 1},
                {"model": "gpt-3.5-turbo", "endpoint": "TESLA", "enabled": True, "_tier": 2}
            ]
        },
        "chat_history": [],
        "mode": "default",
        "conflict_registry": []
    }

    mock_invoke = AsyncMock(return_value=(DummyAIMessage("CONFIDENCE: high\nDiagnose: patient has heartburn."), False))

    with patch("config.JMOE_DEBATE_ENABLED", False), \
         patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "TESLA", "url": "http://test/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)
        # Should not have debate text
        assert "expert_results" in result
        expert_res = result["expert_results"]
        # Only the primary/tier-1 should run
        assert len(expert_res) == 1
        assert "[DEBATE]" not in expert_res[0]
        assert "conflict_registry" in result
        assert len(result["conflict_registry"]) == 0


@pytest.mark.asyncio
async def test_explicit_template_policy_disables_global_micro_debate():
    state = {
        "input": "Diagnose chest pain",
        "plan": [{"id": "a", "category": "medical_consult", "task": "Diagnose chest pain"}],
        "user_experts": {
            "medical_consult": [
                {"model": "gpt-4o", "endpoint": "TESLA", "enabled": True, "_tier": 1},
                {"model": "gpt-3.5-turbo", "endpoint": "TESLA", "enabled": True, "_tier": 1},
            ]
        },
        "chat_history": [],
        "mode": "default",
        "complexity_level": "complex",
        "cynefin_domain": "COMPLEX",
        "conflict_registry": [],
        "deliberation_policy": {"activation": "disabled"},
    }
    mock_invoke = AsyncMock(
        return_value=(DummyAIMessage("CONFIDENCE: high\nStandard answer."), False)
    )

    with patch("config.JMOE_DEBATE_ENABLED", True), \
         patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "TESLA", "url": "http://test/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)

    assert all("[DEBATE]" not in item for item in result["expert_results"])
    assert result["deliberation_capacity"]["active"] is False
    assert result["deliberation_capacity"]["activation_reason"] == "template_disabled"


@pytest.mark.asyncio
async def test_moderated_deliberation_uses_adaptive_capacity_and_early_consensus():
    state = {
        "input": "Evaluate a regulated medical software design.",
        "plan": [
            {"id": "a", "category": "medical_consult", "task": "Assess clinical risk"},
            {"id": "b", "category": "code_reviewer", "task": "Audit implementation", "depends_on": ["a"]},
        ],
        "user_experts": {
            "medical_consult": [
                {"model": "medical-model", "endpoint": "TESLA", "enabled": True, "_tier": 1}
            ],
            "code_reviewer": [
                {"model": "code-model", "endpoint": "TESLA", "enabled": True, "_tier": 1}
            ],
        },
        "chat_history": [],
        "mode": "default",
        "complexity_level": "complex",
        "cynefin_domain": "COMPLEX",
        "conflict_registry": [],
        "deliberation_policy": {
            "activation": "required",
            "mode": "moderated",
            "initial_agent_cap": 5,
            "reserve_agents": 0,
            "absolute_max_agents": 5,
            "initial_round_cap": 1,
            "reserve_rounds": 0,
            "absolute_max_rounds": 1,
            "max_model_calls": 6,
        },
    }
    turn_responses = [
        DummyAIMessage(f"CONFIDENCE: high\nPosition {index}: distinct evidence-aware analysis.")
        for index in range(1, 5)
    ]
    mock_invoke = AsyncMock(side_effect=[(item, False) for item in turn_responses])
    moderator = DummyAIMessage(json.dumps({
        "status": "CONSENSUS",
        "reason": "All required dimensions are covered.",
        "correction": "",
        "direction": "Synthesize with limitations.",
        "convergence_score": 0.9,
        "unresolved_conflicts": 0,
        "missing_perspectives": [],
    }))

    with patch("langchain_openai.ChatOpenAI") as mock_llm_cls, \
         patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._invoke_judge_with_retry", AsyncMock(return_value=moderator)) as mock_moderator, \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "TESLA", "url": "http://test/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)

    assert len(result["expert_results"]) == 1
    assert "[MODERATED DELIBERATION]" in result["expert_results"][0]
    assert result["deliberation_capacity"]["initial_agents"] == 4
    assert mock_invoke.await_count == 4
    assert all(
        call.kwargs["model_kwargs"]["max_tokens"] == 768
        for call in mock_llm_cls.call_args_list
    )
    assert mock_moderator.await_count == 1
    assert any(event["event"] == "early_consensus" for event in result["deliberation_events"])


@pytest.mark.asyncio
async def test_moderated_deliberation_activates_agent_and_round_reserve():
    state = {
        "input": "Evaluate a contested architecture decision.",
        "plan": [
            {"id": "a", "category": "code_reviewer", "task": "Assess architecture"},
            {"id": "b", "category": "data_analyst", "task": "Assess evidence"},
        ],
        "user_experts": {
            "code_reviewer": [
                {"model": "model-a", "endpoint": "TESLA", "enabled": True, "_tier": 1}
            ],
            "data_analyst": [
                {"model": "model-b", "endpoint": "TESLA", "enabled": True, "_tier": 1}
            ],
        },
        "chat_history": [],
        "mode": "default",
        "complexity_level": "complex",
        "cynefin_domain": "COMPLEX",
        "conflict_registry": [],
        "deliberation_policy": {
            "activation": "required",
            "mode": "moderated",
            "min_agents": 2,
            "initial_agent_cap": 2,
            "reserve_agents": 1,
            "absolute_max_agents": 3,
            "min_rounds": 1,
            "initial_round_cap": 1,
            "reserve_rounds": 1,
            "absolute_max_rounds": 2,
            "max_model_calls": 10,
        },
    }
    turn_responses = [
        DummyAIMessage(f"CONFIDENCE: high\nDistinct turn {index} with argument {index}.")
        for index in range(1, 6)
    ]
    mock_invoke = AsyncMock(side_effect=[(item, False) for item in turn_responses])
    moderator_responses = [
        DummyAIMessage(json.dumps({
            "status": "CORRECTION",
            "reason": "Evidence review is missing.",
            "correction": "Add an independent evidence review.",
            "direction": "Test the disputed assumptions.",
            "convergence_score": 0.4,
            "unresolved_conflicts": 1,
            "missing_perspectives": ["evidence_reviewer"],
        })),
        DummyAIMessage(json.dumps({
            "status": "CONSENSUS",
            "reason": "The correction resolved the critical gap.",
            "correction": "",
            "direction": "Synthesize.",
            "convergence_score": 0.9,
            "unresolved_conflicts": 0,
            "missing_perspectives": [],
        })),
    ]

    with patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._invoke_judge_with_retry", AsyncMock(side_effect=moderator_responses)), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "TESLA", "url": "http://test/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)

    events = result["deliberation_events"]
    assert mock_invoke.await_count == 5
    assert any(event["event"] == "reserve_agents_activated" for event in events)
    assert any(event["event"] == "reserve_round_activated" for event in events)
    completed = next(event for event in events if event["event"] == "moderated_debate_completed")
    assert completed["reserve_agents_used"] == 1
    assert completed["reserve_rounds_used"] == 1


@pytest.mark.asyncio
async def test_moderator_schema_repair_cannot_exceed_hard_call_budget():
    state = {
        "input": "Review a bounded design decision.",
        "plan": [
            {"id": "a", "category": "code_reviewer", "task": "Assess design"},
        ],
        "user_experts": {
            "code_reviewer": [
                {"model": "model-a", "endpoint": "TESLA", "enabled": True},
                {"model": "model-b", "endpoint": "TESLA", "enabled": True},
            ],
        },
        "chat_history": [],
        "mode": "default",
        "complexity_level": "complex",
        "cynefin_domain": "COMPLEX",
        "conflict_registry": [],
        "deliberation_policy": {
            "activation": "required",
            "mode": "moderated",
            "min_agents": 2,
            "initial_agent_cap": 2,
            "reserve_agents": 0,
            "absolute_max_agents": 2,
            "min_rounds": 1,
            "initial_round_cap": 1,
            "reserve_rounds": 0,
            "absolute_max_rounds": 1,
            "max_model_calls": 3,
            "moderator_interval": 8,
            "fallback": "standard",
        },
    }
    mock_invoke = AsyncMock(
        return_value=(DummyAIMessage("CONFIDENCE: high\nDistinct position."), False)
    )
    mock_moderator = AsyncMock(return_value=DummyAIMessage("not valid JSON"))

    with patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._invoke_judge_with_retry", mock_moderator), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "TESLA", "url": "http://test/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)

    assert mock_invoke.await_count == 2
    assert mock_moderator.await_count == 1
    completed = next(
        event
        for event in result["deliberation_events"]
        if event["event"] == "moderated_debate_completed"
    )
    assert completed["model_calls"] == 3
    assert completed["model_calls"] <= result["deliberation_capacity"]["model_call_budget"]

@pytest.mark.asyncio
async def test_paraconsistent_judge_arbitration():
    """Verify that the judge arbitrates safety-critical conflicts using Belnap-Dunn logic
    and parses the conflict_map JSON structure correctly."""
    state = {
        "conflict_registry": [
            {
                "category": "medical_consult",
                "proposition_a": "Patient has heartburn.",
                "proposition_b": "Patient has heart attack.",
                "divergence_score": 0.8,
                "resolution": "pending",
                "resolved_by": ""
            }
        ]
    }

    judge_response = DummyAIMessage(
        "<conflict_map>\n"
        "{\n"
        '  "points_of_dispute": [\n'
        '    {"point": "Cause of chest pain", "evidence_a": "acid reflux symptoms", "evidence_b": "radiating pain", "bilattice_value": "I"}\n'
        "  ]\n"
        "}\n"
        "</conflict_map>\n"
        "VERDICT: SYNTHESIS - Treat as heart attack first to ensure safety."
    )

    mock_judge = AsyncMock(return_value=judge_response)

    with patch("graph.synthesis._invoke_judge_with_retry", mock_judge):
        result = await resolve_conflicts_node(state)
        
        assert "conflict_registry" in result
        conflicts = result["conflict_registry"]
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict["resolution"] == "resolved"
        assert "judge_arbitration: VERDICT: SYNTHESIS" in conflict["resolved_by"]
        assert "conflict_map" in conflict
        assert conflict["conflict_map"] == {
            "points_of_dispute": [
                {
                    "point": "Cause of chest pain",
                    "evidence_a": "acid reflux symptoms",
                    "evidence_b": "radiating pain",
                    "bilattice_value": "I"
                }
            ]
        }


# ── TASK-53: local_only_routing must block every outbound expert/debate call ──

@pytest.mark.asyncio
async def test_local_only_routing_blocks_single_expert_cloud_dispatch():
    """A local_only_routing=True request must never reach a non-local expert
    endpoint, even when the candidate's symbolic `endpoint` name (e.g. a cloud
    gateway node like "openrouterai") doesn't itself look like a URL and so
    isn't caught by the candidate-list pre-filter — the hard guard in
    run_single() must catch it at the point the resolved URL is dispatched."""
    state = {
        "plan": [
            {"category": "medical_consult", "task": "Diagnose chest pain"}
        ],
        "user_experts": {
            "medical_consult": [
                {"model": "gpt-4o", "endpoint": "openrouterai", "enabled": True, "_tier": 1},
            ]
        },
        "chat_history": [],
        "mode": "default",
        "conflict_registry": [],
        "local_only_routing": True,
    }

    mock_invoke = AsyncMock(
        return_value=(DummyAIMessage("should never be reached"), False)
    )

    with patch("config.JMOE_DEBATE_ENABLED", False), \
         patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             # A public, non-private IP — resolves without a real DNS lookup
             # (numeric addresses short-circuit getaddrinfo), so the guard's
             # classification is deterministic and network-independent here.
             "name": "openrouterai", "url": "https://1.1.1.1/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)

    mock_invoke.assert_not_awaited()
    assert len(result["expert_results"]) == 1
    assert "ERROR" in result["expert_results"][0]
    assert "local_only" in result["expert_results"][0]


@pytest.mark.asyncio
async def test_local_only_routing_excludes_cloud_candidates_from_debate_panel():
    """The moderated-debate candidate pool must exclude cloud-only categories
    under local_only_routing, matching the TASK-51 incident shape (a category
    whose only configured model routes through a cloud gateway)."""
    state = {
        "input": "Evaluate a regulated medical software design.",
        "plan": [
            {"id": "a", "category": "medical_consult", "task": "Assess clinical risk"},
        ],
        "user_experts": {
            "medical_consult": [
                {"model": "frontier-model", "endpoint": "openrouterai", "enabled": True},
            ],
        },
        "chat_history": [],
        "mode": "default",
        "complexity_level": "complex",
        "cynefin_domain": "COMPLEX",
        "conflict_registry": [],
        "local_only_routing": True,
        "deliberation_policy": {
            "activation": "required",
            "mode": "moderated",
            "fallback": "standard",
            "initial_agent_cap": 5,
            "reserve_agents": 0,
            "absolute_max_agents": 5,
            "initial_round_cap": 1,
            "reserve_rounds": 0,
            "absolute_max_rounds": 1,
            "max_model_calls": 6,
        },
    }
    mock_invoke = AsyncMock(
        return_value=(DummyAIMessage("should never be reached"), False)
    )
    mock_moderator = AsyncMock(return_value=DummyAIMessage("should never be reached"))

    with patch("config.URL_MAP", {"openrouterai": "https://1.1.1.1/v1"}), \
         patch("graph.expert._invoke_llm_with_fallback", mock_invoke), \
         patch("graph.expert._invoke_judge_with_retry", mock_moderator), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "openrouterai", "url": "https://1.1.1.1/v1", "token": "test",
             "api_type": "openai", "timeout": 1,
         })):
        result = await asyncio.wait_for(expert_worker(state), timeout=2.0)

    # No debate turn and no moderator call ever dispatched — the only
    # candidate was excluded from the panel before any network call.
    mock_invoke.assert_not_awaited()
    mock_moderator.assert_not_awaited()
    assert any(
        event["event"] == "moderated_debate_unavailable"
        for event in result["deliberation_events"]
    )

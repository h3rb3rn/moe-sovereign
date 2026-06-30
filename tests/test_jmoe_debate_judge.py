import pytest
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
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)):
        
        result = await expert_worker(state)
        
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
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)):
        
        result = await expert_worker(state)
        # Should not have debate text
        assert "expert_results" in result
        expert_res = result["expert_results"]
        # Only the primary/tier-1 should run
        assert len(expert_res) == 1
        assert "[DEBATE]" not in expert_res[0]
        assert "conflict_registry" in result
        assert len(result["conflict_registry"]) == 0

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

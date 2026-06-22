import pytest
import os
import json
from services.causal_healer import CausalInterventionSandbox, diagnose_and_map_errors, CAUSAL_DATASET_PATH


@pytest.mark.asyncio
async def test_causal_intervention_sandbox_tool_failure():
    state_data = {
        "input": "Write a Python quicksort implementation and compute the time complexity.",
        "plan": [
            {"id": "task1", "category": "precision_tools", "mcp_tool": "calculate", "depends_on": []},
            {"id": "task2", "category": "code_reviewer", "depends_on": ["task1"]}
        ],
        "tool_calls_log": [
            {"tool": "calculate", "status": "error", "error": "Invalid expression syntax"}
        ],
        "expert_results": [
            "[CODE_REVIEWER ERROR]: Tool call failed"
        ],
        "working_memory": {},
        "expert_models_used": ["qwen2.5-coder:7b::code_reviewer"]
    }
    
    sandbox = CausalInterventionSandbox(state_data)
    assert len(sandbox.observed_failures) == 2
    
    # Simulating do(calculate = Fail)
    status, prob = sandbox.simulate_do_intervention({"do": "tool_fail", "target": "calculate"})
    assert status == "fail"
    assert prob == 0.9
    
    # Simulating baseline do(none = Fail)
    status_base, prob_base = sandbox.simulate_do_intervention({"do": "tool_fail", "target": "none"})
    assert status_base == "ok"
    assert prob_base == 0.15
    
    # Verify Average Causal Effect (ACE)
    ace = sandbox.calculate_average_causal_effect(
        variable="Tool_calculate",
        intervention={"do": "tool_fail", "target": "calculate"},
        baseline={"do": "tool_fail", "target": "none"}
    )
    assert ace == 0.75  # 0.9 - 0.15


@pytest.mark.asyncio
async def test_causal_intervention_sandbox_context_limit():
    state_data = {
        "input": "A very long prompt containing lots of code snippets " * 100,  # ~5000 chars
        "plan": [
            {"id": "task1", "category": "creative_writer", "depends_on": []}
        ],
        "tool_calls_log": [],
        "expert_results": ["[CREATIVE_WRITER ERROR]: Timeout"],
        "working_memory": {"some_key": "some_value" * 500},  # ~5000 chars
        "expert_models_used": ["llama3:70b::creative_writer"]
    }
    
    sandbox = CausalInterventionSandbox(state_data)
    
    # Clamping context to 1000 tokens (which is smaller than estimated 2500+ tokens)
    status, prob = sandbox.simulate_do_intervention({"do": "context_clamp", "limit": 1000})
    assert status == "fail"
    assert prob == 0.95


@pytest.mark.asyncio
async def test_diagnose_and_map_errors():
    state_data = {
        "response_id": "test-resp-123",
        "input": "Calculate the average of [10, 20, 30]",
        "plan": [
            {"id": "task1", "category": "precision_tools", "mcp_tool": "calculate"}
        ],
        "tool_calls_log": [
            {"tool": "calculate", "status": "error", "error": "Math division error"}
        ],
        "expert_results": ["[MATH ERROR]: division by zero"],
        "working_memory": {},
        "expert_models_used": ["llama3.1-8b::math"],
        "complexity_level": "easy",
        "judge_num_ctx": 4096
    }
    
    # Clean up file first if exists
    if os.path.exists(CAUSAL_DATASET_PATH):
        try:
            os.remove(CAUSAL_DATASET_PATH)
        except Exception:
            pass
            
    result = await diagnose_and_map_errors(state_data)
    
    assert result["response_id"] == "test-resp-123"
    assert "Input_Complexity" in result["nodes"]
    assert "Context_Limit" in result["nodes"]
    assert "Tool_calculate" in result["nodes"]
    assert "Model_math" in result["nodes"]
    assert "Synthesis_Arbitration" in result["nodes"]
    
    # Check edges exist
    assert len(result["edges"]) > 0
    
    # Check root causes were computed and sorted
    assert len(result["root_causes"]) > 0
    assert result["root_causes"][0]["node"] == "Tool_calculate"
    
    # Check that training dataset was exported
    assert os.path.exists(CAUSAL_DATASET_PATH)
    with open(CAUSAL_DATASET_PATH, "r", encoding="utf-8") as f:
        line = f.readline()
        data = json.loads(line)
        assert data["response_id"] == "test-resp-123"
        assert data["recommendation"] == "Tool_calculate"

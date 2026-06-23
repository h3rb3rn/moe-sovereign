import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
import numpy as np

from services.dynamic_router import (
    get_complexity,
    get_gates,
    _match_existing_template,
    _score_and_allocate_model,
    get_dynamic_template
)

# Test prompt classifications
def test_heuristic_get_complexity():
    assert get_complexity("What is 2+2? Answer in one word.") == "trivial"
    assert get_complexity("What did I mention earlier about the database port?") == "memory_recall"
    assert get_complexity("Analyze the difference between TCP and UDP protocols step by step.") == "complex"
    assert get_complexity("Write a python class to handle postgres database connections.") == "moderate"

def test_heuristic_get_gates():
    # Trivial / recall has no gates active
    assert get_gates("2+2", "trivial") == (0.0, 0.0)
    assert get_gates("what did I say", "memory_recall") == (0.0, 0.0)
    
    # Complex always has web active
    assert get_gates("analyze papers", "complex") == (1.0, 1.0)
    
    # Moderate only if keywords present
    assert get_gates("standard code support", "moderate") == (0.0, 1.0)
    assert get_gates("what are the latest news today?", "moderate") == (1.0, 1.0)

# Test ChromaDB cache matching
@pytest.mark.asyncio
async def test_match_existing_template():
    mock_coll = MagicMock()
    # Cache hit scenario (distance < 0.18)
    mock_coll.query.return_value = {
        "ids": [["moe-dyn-12345"]],
        "distances": [[0.12]],
        "metadatas": [[{"name": "Dynamic Template 12345"}]]
    }
    
    with patch("services.dynamic_router._template_collection", mock_coll):
        tmpl_id, name = await _match_existing_template("test query")
        assert tmpl_id == "moe-dyn-12345"
        assert name == "Dynamic Template 12345"
        
    # Cache miss scenario (distance >= 0.18)
    mock_coll.query.return_value = {
        "ids": [["moe-dyn-12345"]],
        "distances": [[0.22]],
        "metadatas": [[{"name": "Dynamic Template 12345"}]]
    }
    
    with patch("services.dynamic_router._template_collection", mock_coll):
        tmpl_id, name = await _match_existing_template("test query")
        assert tmpl_id is None
        assert name is None

# Test model scoring and allocation
@pytest.mark.asyncio
async def test_score_and_allocate_model():
    models = [
        {"model_id": "qwen3.6:35b@N04-RTX", "model_name": "qwen3.6:35b", "endpoint": "N04-RTX", "is_warmed": True, "is_local": True},
        {"model_id": "llama3.3:70b@cloud", "model_name": "llama3.3:70b", "endpoint": "cloud", "is_warmed": False, "is_local": False}
    ]
    
    metadata = {
        "qwen3.6:35b@N04-RTX": {"context_window": 32768, "benchmark_scores": {"mmlu": 90.0}},
        "llama3.3:70b@cloud": {"context_window": 131072, "benchmark_scores": {"mmlu": 85.0}}
    }
    
    # 1. Normal mode (allows cloud)
    with patch("services.dynamic_router._get_thompson_score", AsyncMock(return_value=0.5)), \
         patch("random.random", return_value=1.0):
        allocated = await _score_and_allocate_model("general", models, metadata, local_only=False)
        assert len(allocated) == 2
        # RTX is primary since it has warmed and local priority bonuses
        assert allocated[0]["model_id"] == "qwen3.6:35b@N04-RTX"
        assert allocated[1]["model_id"] == "llama3.3:70b@cloud"
        
    # 2. Local-only mode (strictly blocks cloud)
    with patch("services.dynamic_router._get_thompson_score", AsyncMock(return_value=0.5)), \
         patch("random.random", return_value=1.0):
        allocated_local = await _score_and_allocate_model("general", models, metadata, local_only=True)
        assert len(allocated_local) == 1
        assert allocated_local[0]["model_id"] == "qwen3.6:35b@N04-RTX"

# Test full dynamic template compilation
@pytest.mark.asyncio
async def test_get_dynamic_template_integration():
    mock_session = MagicMock()
    # Mock ONNX outputs: experts, complexity, gates
    # Index 4 is 'general'
    mock_experts = np.zeros((1, 14), dtype=np.float32)
    mock_experts[0, 4] = 2.0  # logit for 'general' -> prob > 0.5
    
    # Index 1 is 'moderate'
    mock_complexity = np.zeros((1, 4), dtype=np.float32)
    mock_complexity[0, 1] = 5.0
    
    # Index 0 is web_research, Index 1 is graphrag
    mock_gates = np.zeros((1, 2), dtype=np.float32)
    mock_gates[0, 0] = -1.0 # web off
    mock_gates[0, 1] = 2.0  # graphrag on
    
    mock_session.run.return_value = [mock_experts, mock_complexity, mock_gates]
    
    models = [
        {"model_id": "qwen3.6:35b@N04-RTX", "model_name": "qwen3.6:35b", "endpoint": "N04-RTX", "is_warmed": True, "is_local": True}
    ]
    
    # Mock database connections and telemetry polls
    with patch("services.dynamic_router._onnx_session", mock_session), \
         patch("services.dynamic_router._embedding_function", MagicMock(return_value=[[0.0]*384])), \
         patch("services.dynamic_router._match_existing_template", AsyncMock(return_value=(None, None))), \
         patch("services.dynamic_router._get_cluster_state", AsyncMock(return_value=models)), \
         patch("services.dynamic_router._save_template_to_db_and_cache", AsyncMock(return_value="moe-dyn-test")), \
         patch("services.dynamic_router._get_pool", MagicMock()) as mock_db_pool:
         
        # Mock connection cursor return
        mock_conn = AsyncMock()
        mock_conn.cursor = MagicMock()
        mock_cur = AsyncMock()
        mock_cur.fetchall.return_value = [
            ("qwen3.6:35b@N04-RTX", 32768, '{"mmlu": 80.0}')
        ]
        mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
        mock_db_pool.return_value.connection.return_value.__aenter__.return_value = mock_conn
        
        tmpl = await get_dynamic_template("Please assist with my request.", local_only=True)
        
        assert tmpl is not None
        assert tmpl["planner_model"] == "qwen3.6:35b@N04-RTX"
        assert tmpl["judge_model"] == "qwen3.6:35b@N04-RTX"
        assert tmpl["enable_graphrag"] is True
        assert tmpl["enable_web_research"] is False
        assert "general" in tmpl["experts"]


@pytest.mark.asyncio
async def test_get_dynamic_template_connection_combining():
    mock_session = MagicMock()
    # Mock ONNX outputs
    mock_experts = np.zeros((1, 14), dtype=np.float32)
    mock_experts[0, 4] = 2.0  # general
    mock_complexity = np.zeros((1, 4), dtype=np.float32)
    mock_complexity[0, 1] = 5.0
    mock_gates = np.zeros((1, 2), dtype=np.float32)
    mock_session.run.return_value = [mock_experts, mock_complexity, mock_gates]

    global_models = [
        {"model_id": "llama-global@G01", "model_name": "llama-global", "endpoint": "G01", "is_warmed": True, "is_local": True}
    ]

    user_connections = {
        "U01": {
            "url": "http://localhost:11434",
            "models_cache": [
                {"id": "qwen-user@U01"}
            ]
        }
    }

    with patch("services.dynamic_router._onnx_session", mock_session), \
         patch("services.dynamic_router._embedding_function", MagicMock(return_value=[[0.0]*384])), \
         patch("services.dynamic_router._match_existing_template", AsyncMock(return_value=(None, None))), \
         patch("services.dynamic_router._get_cluster_state", AsyncMock(return_value=global_models)), \
         patch("services.dynamic_router._save_template_to_db_and_cache", AsyncMock(return_value="moe-dyn-test")), \
         patch("services.dynamic_router._get_pool", MagicMock()) as mock_db_pool:
         
        # Mock database connections
        mock_conn = AsyncMock()
        mock_conn.cursor = MagicMock()
        mock_cur = AsyncMock()
        mock_cur.fetchall.return_value = [
            ("llama-global@G01", 32768, '{"mmlu": 80.0}'),
            ("qwen-user@U01", 32768, '{"mmlu": 80.0}')
        ]
        mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
        mock_db_pool.return_value.connection.return_value.__aenter__.return_value = mock_conn

        # Case 1: Combined (default) -> both global and user connections are used
        tmpl_combined = await get_dynamic_template("test", user_connections=user_connections)
        assert tmpl_combined is not None
        assert "llama-global@G01" in str(tmpl_combined)
        assert "qwen-user" in str(tmpl_combined)
        assert "U01" in str(tmpl_combined)

        # Case 2: Global-only -> user connections are ignored
        tmpl_global = await get_dynamic_template("test", user_connections=user_connections, global_only=True)
        assert tmpl_global is not None
        assert "llama-global@G01" in str(tmpl_global)
        assert "qwen-user" not in str(tmpl_global)

        # Case 3: User-connections-only -> global connections are ignored
        tmpl_user = await get_dynamic_template("test", user_connections=user_connections, user_conns_only=True)
        assert tmer_or_not_none(tmpl_user)
        assert "qwen-user@U01" in str(tmpl_user)
        assert "llama-global@G01" not in str(tmpl_user)


@pytest.mark.asyncio
async def test_generate_fallback_structured_prompts():
    from services.dynamic_router import _generate_fallback_structured_prompts
    
    # Test English and step hint
    res = _generate_fallback_structured_prompts("Explain in English step by step", ["math"])
    assert "Prefer generating responses and plans in English." in res["planner_prompt"]
    assert "Break down the reasoning into clear, numbered steps." in res["planner_prompt"]
    assert "math" in res["experts"]
    assert "Prefer generating responses and plans in English." in res["experts"]["math"]["system_prompt"]
    
    # Test German hint
    res_de = _generate_fallback_structured_prompts("Bitte auf Deutsch", ["code_reviewer"])
    assert "Prefer generating responses and plans in German." in res_de["planner_prompt"]
    assert "code_reviewer" in res_de["experts"]
    assert "Prefer generating responses and plans in German." in res_de["experts"]["code_reviewer"]["system_prompt"]


@pytest.mark.asyncio
async def test_generate_prompt_specific_prompts_llm():
    from services.dynamic_router import _generate_prompt_specific_prompts
    import os
    
    mock_res = MagicMock()
    mock_res.content = json.dumps({
        "planner_prompt": "LLM custom planner prompt",
        "judge_prompt": "LLM custom judge prompt",
        "experts": {
            "math": {
                "system_prompt": "LLM custom math expert prompt"
            }
        }
    })
    
    # Mock LLM instance and env variable
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_res
    
    with patch.dict(os.environ, {"DYNAMIC_SYSTEM_PROMPTS_LLM_ENABLED": "true"}), \
         patch("services.llm_instances.planner_llm", mock_llm):
        res = await _generate_prompt_specific_prompts("custom query", ["math"])
        
        assert res["planner_prompt"] == "LLM custom planner prompt"
        assert res["judge_prompt"] == "LLM custom judge prompt"
        assert res["experts"]["math"]["system_prompt"] == "LLM custom math expert prompt"
        mock_llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_score_and_allocate_model_intervention():
    models = [
        {"model_id": "model_A@node", "model_name": "model_A", "endpoint": "node", "is_warmed": True, "is_local": True},
        {"model_id": "model_B@node", "model_name": "model_B", "endpoint": "node", "is_warmed": True, "is_local": True}
    ]
    
    metadata = {
        "model_A@node": {"context_window": 4096, "benchmark_scores": {"mmlu": 90.0}},
        "model_B@node": {"context_window": 4096, "benchmark_scores": {"mmlu": 80.0}}
    }
    
    interventions = []
    # Force random.random to return 0.01 so that intervention is triggered
    with patch("services.dynamic_router._get_thompson_score", AsyncMock(return_value=0.5)), \
         patch("random.random", return_value=0.01):
        allocated = await _score_and_allocate_model("general", models, metadata, local_only=False, interventions=interventions)
        # Check that swap occurred: primary model should be model_B instead of model_A
        assert len(allocated) == 2
        assert allocated[0]["model_id"] == "model_B@node"
        assert allocated[1]["model_id"] == "model_A@node"
        assert len(interventions) == 1
        assert interventions[0]["is_intervention"] is True
        assert interventions[0]["intervened"] == "model_B@node"
        assert interventions[0]["default"] == "model_A@node"
        assert interventions[0]["expert"] == "general"



def tmer_or_not_none(val):
    assert val is not None
    return True


@pytest.mark.asyncio
async def test_score_and_allocate_model_force_weak():
    models = [
        {"model_id": "model_large:70b@node", "model_name": "model_large:70b", "endpoint": "node", "is_warmed": True, "is_local": True},
        {"model_id": "model_small:8b@node", "model_name": "model_small:8b", "endpoint": "node", "is_warmed": True, "is_local": True}
    ]
    
    metadata = {
        "model_large:70b@node": {"context_window": 4096, "benchmark_scores": {"mmlu": 90.0}, "parameter_size_b": 70.0},
        "model_small:8b@node": {"context_window": 4096, "benchmark_scores": {"mmlu": 70.0}, "parameter_size_b": 8.0}
    }
    
    # 1. Without force_weak -> large model has higher score and is allocated first
    with patch("services.dynamic_router._get_thompson_score", AsyncMock(return_value=0.5)), \
         patch("random.random", return_value=1.0):
        allocated = await _score_and_allocate_model("general", models, metadata, local_only=False, force_weak=False)
        assert allocated[0]["model_id"] == "model_large:70b@node"

    # 2. With force_weak -> large model is filtered out (since 70B > EXPERT_TIER_BOUNDARY_B=20)
    with patch("services.dynamic_router._get_thompson_score", AsyncMock(return_value=0.5)), \
         patch("random.random", return_value=1.0):
        allocated_weak = await _score_and_allocate_model("general", models, metadata, local_only=False, force_weak=True)
        assert len(allocated_weak) == 1
        assert allocated_weak[0]["model_id"] == "model_small:8b@node"


@pytest.mark.asyncio
async def test_routellm_template_routing_integration():
    mock_session = MagicMock()
    # Mock ONNX outputs (complexity='moderate', gates off)
    mock_experts = np.zeros((1, 14), dtype=np.float32)
    mock_experts[0, 4] = 2.0  # general
    mock_complexity = np.zeros((1, 4), dtype=np.float32)
    mock_complexity[0, 1] = 5.0
    mock_gates = np.zeros((1, 2), dtype=np.float32)
    mock_session.run.return_value = [mock_experts, mock_complexity, mock_gates]

    models = [
        {"model_id": "model_large:70b@node", "model_name": "model_large:70b", "endpoint": "node", "is_warmed": True, "is_local": True},
        {"model_id": "model_small:8b@node", "model_name": "model_small:8b", "endpoint": "node", "is_warmed": True, "is_local": True}
    ]

    # Mock RouteLLM weights (w is 1024-dim vector, b is scalar)
    w_mock = np.ones((1024,), dtype=np.float32) * -0.5
    b_mock = -1.0
    
    # We query with mock embedding
    mock_embed = np.ones((1024,), dtype=np.float32) * 0.1 # np.dot(0.1, -0.5*1024) + b = -51.2 - 1 = -52.2 -> sigmoid is 0.0 -> weak model path
    
    with patch("services.dynamic_router._onnx_session", mock_session), \
         patch("services.dynamic_router._embedding_function", MagicMock(return_value=[[0.0]*384])), \
         patch("services.dynamic_router._match_existing_template", AsyncMock(return_value=(None, None))), \
         patch("services.dynamic_router._get_cluster_state", AsyncMock(return_value=models)), \
         patch("services.dynamic_router._save_template_to_db_and_cache", AsyncMock(return_value="moe-dyn-test")), \
         patch("services.dynamic_router._get_pool", MagicMock()) as mock_db_pool, \
         patch("services.dynamic_router._routellm_w", w_mock), \
         patch("services.dynamic_router._routellm_b", b_mock), \
         patch("services.dynamic_router.ROUTELLM_ENABLED", True), \
         patch("services.dynamic_router.get_bge_embedding", AsyncMock(return_value=mock_embed)):
         
        # Mock database connection cursor
        mock_conn = AsyncMock()
        mock_conn.cursor = MagicMock()
        mock_cur = AsyncMock()
        mock_cur.fetchall.return_value = [
            ("model_large:70b@node", 4096, '{"mmlu": 90.0}', 70.0, 'other', '[]'),
            ("model_small:8b@node", 4096, '{"mmlu": 70.0}', 8.0, 'other', '[]')
        ]
        mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
        mock_db_pool.return_value.connection.return_value.__aenter__.return_value = mock_conn

        tmpl = await get_dynamic_template("Test RouteLLM query")
        
        # RouteLLM probability should be very low, so force_weak=True, meaning it should select model_small
        assert tmpl is not None
        assert "RouteLLM_Score" in tmpl["reasoning_trace"]
        assert "path=weak" in tmpl["reasoning_trace"]
        assert tmpl["experts"]["general"]["models"][0]["model"] == "model_small:8b"



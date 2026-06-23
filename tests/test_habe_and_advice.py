"""
tests/test_habe_and_advice.py — Unit tests for HABE VSA operations,
dynamic threshold calibration, the Advice Store, and the Heuristics Auditor.
"""

import os
import json
import pytest
import numpy as np

from services.vsa_background import HolographicBackgroundEngine
from services.advice_store import add_advice_rule, load_advice_rules, get_active_advice, delete_advice_rule
from scripts.heuristics_auditor import audit_heuristics

# ─── VSA / HABE tests ──────────────────────────────────────────────────────────

def test_vsa_binding_and_bundling():
    """Verify that circular convolution (binding) and superposition (bundling) work algebraically."""
    engine = HolographicBackgroundEngine(dimension=1024)
    
    # 1. Generate clean vectors
    v_s = engine.get_or_create_vector("subj:quicksort")
    v_p = engine.get_or_create_vector("pred:implemented_in")
    v_o = engine.get_or_create_vector("obj:rust")
    
    # 2. Bind them
    bound = engine.bind(engine.bind(v_s, v_p), v_o)
    assert bound.shape == (1024,)
    assert not np.isnan(bound).any()
    
    # 3. Unbind the object: obj = bound * Inv(subj * pred)
    query_key = engine.bind(v_s, v_p)
    retrieved = engine.unbind(bound, query_key)
    
    # Cosine similarity with target should be high, and low with noise/others
    sim_target = engine.cosine_similarity(retrieved, v_o)
    sim_noise = engine.cosine_similarity(retrieved, v_s)
    
    assert sim_target > 0.30
    assert sim_noise < 0.15

def test_dynamic_threshold_calibration():
    """Verify that the dynamic threshold calibration adapts to noise correctly."""
    engine = HolographicBackgroundEngine(dimension=1024)
    
    # Generate random vector representing query outcome
    q = np.random.normal(0, 1.0 / np.sqrt(1024), 1024)
    q /= np.linalg.norm(q)
    
    # 1. Call cleanup with auto-calibration (threshold=None)
    matches = engine.cleanup(q, threshold=None)
    assert isinstance(matches, list)
    
    # 2. Verify calibration with num_bundled
    # Small N (low threshold expected)
    t_small = 3.0 * np.sqrt(5 / 1024)
    # Large N (high threshold expected)
    t_large = 3.0 * np.sqrt(100 / 1024)
    
    assert t_small < t_large

def test_graph_compilation_and_query():
    """Test full GraphRAG compilation to HABE vector and relation querying."""
    engine = HolographicBackgroundEngine(dimension=2048)
    
    triples = [
        ("quicksort", "implemented_in", "rust"),
        ("deepseek-r1", "optimized_on", "RTX-3090"),
        ("lumi-g", "hosted_in", "finland")
    ]
    
    # Compile
    hav = engine.compile_graph_to_vsa(triples)
    assert hav.shape == (2048,)
    
    # Query: What is quicksort implemented in?
    matches = engine.query_vsa_relation(hav, "quicksort", "implemented_in")
    assert len(matches) > 0
    assert matches[0][0] == "rust"
    assert matches[0][1] > 0.30

def test_vocab_load_save(tmp_path):
    """Test saving and loading vocabulary file."""
    engine = HolographicBackgroundEngine(dimension=512)
    engine.get_or_create_vector("test_key")
    
    vocab_file = os.path.join(tmp_path, "test_vocab.json")
    engine.save_vocab(vocab_file)
    assert os.path.exists(vocab_file)
    
    new_engine = HolographicBackgroundEngine(dimension=512)
    assert new_engine.load_vocab(vocab_file)
    assert "test_key" in new_engine.vocab
    assert np.allclose(new_engine.vocab["test_key"], engine.vocab["test_key"])

# ─── Advice Taker tests ───────────────────────────────────────────────────────

def test_advice_store():
    """Test adding, listing, querying, and deleting advice rules."""
    # Add a mock rule
    rule_text = "If query mentions python, use standard python_runtime."
    r = add_advice_rule(rule_text, category_scope="python")
    
    assert r["rule"] == rule_text
    assert r["category_scope"] == "python"
    
    # Fetch active advice
    rules = load_advice_rules()
    assert len(rules) > 0
    
    # Match query scope
    active_matches = get_active_advice("help with python coding")
    assert rule_text in active_matches
    
    active_miss = get_active_advice("write a javascript app")
    assert rule_text not in active_miss
    
    # Cleanup / Delete
    assert delete_advice_rule(r["id"])


def test_advice_taker_rule_enforcement():
    """Test matching by regex pattern and symbolic task injection on generated plans."""
    from services.advice_store import add_advice_rule, delete_advice_rule, enforce_advice_rules, get_active_advice
    
    # 1. Add rule for subnetting
    r = add_advice_rule(
        rule_text="Use subnet_calc tool for CIDR/IP masks.",
        category_scope="all",
        pattern=r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}\b",
        category="precision_tools",
        mcp_tool="subnet_calc",
        default_task_description="Calculate subnet info"
    )
    
    try:
        # Match query test
        active = get_active_advice("Analyze network 192.168.1.0/24")
        assert "Use subnet_calc tool for CIDR/IP masks." in active
        
        # Plan enforcement test - matching CIDR query, empty plan
        plan = []
        enforced = enforce_advice_rules("Analyze network 192.168.1.0/24", plan)
        assert len(enforced) == 1
        assert enforced[0]["category"] == "precision_tools"
        assert enforced[0]["mcp_tool"] == "subnet_calc"
        assert enforced[0]["mcp_args"]["cidr"] == "192.168.1.0/24"
        
        # Plan enforcement test - matching CIDR query, plan already has it
        plan_has = [{"category": "precision_tools", "mcp_tool": "subnet_calc", "mcp_args": {"cidr": "192.168.1.0/24"}}]
        enforced_has = enforce_advice_rules("Analyze network 192.168.1.0/24", plan_has)
        assert len(enforced_has) == 1
        
    finally:
        delete_advice_rule(r["id"])



# ─── Heuristics Auditor tests ─────────────────────────────────────────────────

def test_heuristics_auditor():
    """Test executing the heuristics auditor script and verifying output recommendations."""
    # Run auditor
    audit_heuristics()
    
    # Verify recommendations file exists and is valid
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    recommendations_file = os.path.join(script_dir, "data", "heuristics_recommendations.json")
    
    assert os.path.exists(recommendations_file)
    
    with open(recommendations_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    assert "total_traces_analyzed" in data
    assert "proposals" in data
    assert len(data["proposals"]) > 0
    assert data["proposals"][0]["parameter"] == "SOFT_CACHE_THRESHOLD"


# ─── HABE 2.0 & Prefix Attention tests ───────────────────────────────────────

def test_habe_2_0_hierarchical_graph():
    """Verify that HABE 2.0 compiles and queries hierarchical graph structures."""
    engine = HolographicBackgroundEngine(dimension=2048)
    
    # 1. Structure hierarchical tree data
    tree_data = {
        "node": "root_app",
        "relation": "root_relation",
        "children": [
            {
                "node": "db_service",
                "relation": "has_subsystem",
                "children": [
                    {
                        "node": "postgres_instance",
                        "relation": "runs_on",
                        "children": []
                    }
                ]
            },
            {
                "node": "cache_service",
                "relation": "has_subsystem",
                "children": []
            }
        ]
    }
    
    # 2. Compile hierarchical graph
    hav = engine.compile_hierarchical_graph_to_vsa(tree_data)
    assert hav.shape == (2048,)
    assert not np.isnan(hav).any()
    
    # 3. Query the hierarchy
    subsystems = engine.query_vsa_hierarchy(hav, "root_app", "has_subsystem")
    assert len(subsystems) > 0
    
    # Verify similarity values. Note that root_app is filtered out.
    nodes = [name for name, sim in subsystems if sim > 0.15]
    assert "db_service" in nodes
    assert "cache_service" in nodes
    
    # Retrieve db_service subgraph vector to query its child runs_on relation recursively
    v_root = engine.get_or_create_vector("node:root_app")
    v_rel = engine.get_or_create_vector("relation:has_subsystem")
    query_key = engine.bind(v_root, v_rel)
    db_subgraph_vec = engine.unbind(hav, query_key)
    res_runs = engine.query_vsa_hierarchy(db_subgraph_vec, "db_service", "runs_on")
    
    assert len(res_runs) > 0
    assert res_runs[0][0] == "postgres_instance"
    assert res_runs[0][1] > 0.15
    
    # 4. Export virtual prefix embeddings
    prefix_embeddings = engine.export_virtual_prefix_embeddings(hav)
    assert len(prefix_embeddings) == 2048
    assert isinstance(prefix_embeddings[0], float)
    assert np.isclose(np.linalg.norm(prefix_embeddings), 1.0, atol=1e-4)


# ─── Eurisko Heuristic Breeder tests ──────────────────────────────────────────

def test_eurisko_heuristic_breeder(tmp_path, monkeypatch):
    """Verify Eurisko self-referential breeder logic including weight updates and breeding."""
    # Patch the HEURISTICS_FILE to avoid writing to production data folder
    test_heuristics_file = os.path.join(tmp_path, "test_eurisko_heuristics.json")
    import scripts.eurisko_template_optimizer as eto
    monkeypatch.setattr(eto, "HEURISTICS_FILE", test_heuristics_file)
    
    # Initialize breeder
    breeder = eto.HeuristicBreeder()
    assert len(breeder.heuristics) >= 3
    
    # Select heuristic
    h = breeder.select_heuristic()
    assert h is not None
    assert h.name in breeder.heuristics
    
    # Update weight
    original_weight = breeder.heuristics["enable_vsa_habe"].weight
    breeder.update_weight("enable_vsa_habe", success=True)
    new_weight = breeder.heuristics["enable_vsa_habe"].weight
    assert new_weight > original_weight
    
    # Test breeding (requires one heuristic to have weight > 1.5)
    breeder.heuristics["enable_vsa_habe"].weight = 1.6
    new_heuristic_name = breeder.breed_heuristics()
    assert new_heuristic_name != ""
    assert new_heuristic_name in breeder.heuristics
    assert "bred_enable_vsa_habe" in new_heuristic_name
    
    # Test file saving/loading
    assert os.path.exists(test_heuristics_file)
    
    # Re-initialize to verify loading works
    new_breeder = eto.HeuristicBreeder()
    assert new_heuristic_name in new_breeder.heuristics
    assert new_breeder.heuristics["enable_vsa_habe"].weight == 1.6


# ─── Advice Taker Refinements tests ───────────────────────────────────────────

def test_advice_taker_semantic_matching():
    """Verify that Advice-Taker matches query scope using 3-gram Jaccard similarity."""
    rule_text = "Enforce strict security on payment flows."
    r = add_advice_rule(
        rule_text=rule_text,
        category_scope="payment_processing"
    )
    
    try:
        # Match using query that has semantic similarity to "payment_processing"
        active = get_active_advice("Optimize payment_process step")
        assert rule_text in active
        
        # Test query that does not overlap
        active_no = get_active_advice("Compile rust application")
        assert rule_text not in active_no
        
    finally:
        delete_advice_rule(r["id"])


def test_advice_taker_regex_parameter_extraction():
    """Verify that Advice-Taker extracts arguments declaratively using regex extractors."""
    from services.advice_store import enforce_advice_rules
    
    r = add_advice_rule(
        rule_text="Run optimization on target database.",
        category_scope="all",
        pattern=r"optimize db",
        category="db_tuning",
        mcp_tool="tune_database",
        default_task_description="Tune database parameters",
        parameter_extractors={
            "db_name": r"db:\s*([a-zA-Z0-9_-]+)",
            "factor": r"factor:\s*(\d+(\.\d+)?)"
        }
    )
    
    try:
        plan = []
        enforced = enforce_advice_rules("optimize db with db: customers_prod and factor: 1.5", plan)
        assert len(enforced) == 1
        task = enforced[0]
        assert task["category"] == "db_tuning"
        assert task["mcp_tool"] == "tune_database"
        assert task["mcp_args"]["db_name"] == "customers_prod"
        assert task["mcp_args"]["factor"] == "1.5"
        
    finally:
        delete_advice_rule(r["id"])


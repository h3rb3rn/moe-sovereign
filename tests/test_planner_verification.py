import pytest
from graph.planner import validate_dag_kahn, verify_cot_step_z3

def test_validate_dag_kahn_valid():
    """Test that a valid cycle-free DAG returns True."""
    dag = {
        'a': ['b', 'c'],
        'b': ['d'],
        'c': ['d'],
        'd': []
    }
    assert validate_dag_kahn(dag) is True

def test_validate_dag_kahn_cycle():
    """Test that a DAG with a cycle returns False."""
    dag = {
        'a': ['b'],
        'b': ['c'],
        'c': ['a']
    }
    assert validate_dag_kahn(dag) is False

def test_validate_dag_kahn_empty():
    """Test that an empty DAG returns True."""
    assert validate_dag_kahn({}) is True

def test_validate_dag_kahn_single_node():
    """Test that a single-node DAG returns True."""
    assert validate_dag_kahn({'a': []}) is True

def test_verify_cot_step_z3_valid():
    """Test that a valid CoT step returns is_valid=True."""
    context = "The sky is blue because of Rayleigh scattering."
    deduction = "Therefore, Rayleigh scattering causes the blue sky."
    result = verify_cot_step_z3(context, deduction)
    assert result['is_valid'] is True
    assert result['diagnostic_error'] is None
    assert result['step'] == deduction

def test_verify_cot_step_z3_invalid_contradiction():
    """Test that a contradictory CoT step returns is_valid=False."""
    context = "The system supports parallel processing."
    deduction = "Therefore, it does not support parallel execution."
    result = verify_cot_step_z3(context, deduction)
    assert result['is_valid'] is False
    assert result['diagnostic_error'] is not None

def test_verify_cot_step_z3_invalid_no_reference():
    """Test that a deduction lacking context reference returns is_valid=False."""
    context = "The database uses PostgreSQL."
    deduction = "The weather is nice today."
    result = verify_cot_step_z3(context, deduction)
    assert result['is_valid'] is False

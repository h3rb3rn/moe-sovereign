import pytest
from services.quality_gate import evaluate_program_sketch, run_dspy_teleprompter_gate

def test_sketch_valid_with_bounds():
    sketch_data = {
        'holes': {'h1': None, 'h2': None},
        'smt_bounds': {
            'h1': {'type': 'int', 'min': 10, 'max': 20},
            'h2': {'type': 'enum', 'enum': ['A', 'B']}
        }
    }
    result = evaluate_program_sketch(sketch_data)
    assert result['sketch_valid'] is True
    assert result['filled_holes']['h1'] == 10
    assert result['filled_holes']['h2'] == 'A'
    assert result['unsat_core'] is None

def test_sketch_unsat_bounds():
    sketch_data = {
        'holes': {'h1': None},
        'smt_bounds': {
            'h1': {'type': 'int', 'min': 20, 'max': 10}
        }
    }
    result = evaluate_program_sketch(sketch_data)
    assert result['sketch_valid'] is False
    assert result['unsat_core'] == ['h1']

def test_sketch_no_bounds():
    sketch_data = {
        'holes': {'h1': None, 'h2': None},
        'smt_bounds': {}
    }
    result = evaluate_program_sketch(sketch_data)
    assert result['sketch_valid'] is True
    assert result['filled_holes']['h1'] is None
    assert result['filled_holes']['h2'] is None

def test_trace_passed_all_tiers():
    trace = {
        'egress_local_only': True,
        'canonical_json_hash': 'abc123hash',
        'trust_verdict': 'PROCEED'
    }
    result = run_dspy_teleprompter_gate(trace)
    assert result['passed'] is True
    assert result['tier_failed'] is None

def test_trace_failed_tier1():
    trace = {
        'egress_local_only': False,
        'canonical_json_hash': 'abc123hash',
        'trust_verdict': 'PROCEED'
    }
    result = run_dspy_teleprompter_gate(trace)
    assert result['passed'] is False
    assert result['tier_failed'] == 1

def test_trace_failed_tier2():
    trace = {
        'egress_local_only': True,
        'canonical_json_hash': '',
        'trust_verdict': 'PROCEED'
    }
    result = run_dspy_teleprompter_gate(trace)
    assert result['passed'] is False
    assert result['tier_failed'] == 2

def test_trace_failed_tier3():
    trace = {
        'egress_local_only': True,
        'canonical_json_hash': 'abc123hash',
        'trust_verdict': 'BLOCK'
    }
    result = run_dspy_teleprompter_gate(trace)
    assert result['passed'] is False
    assert result['tier_failed'] == 3

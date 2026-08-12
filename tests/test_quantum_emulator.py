"""Unit tests for the MoE Sovereign Hybrid Quantum Emulator module."""

import pytest
from services.quantum_emulator import QuantumStatevectorSimulator, solve_qaoa_dag_scheduling


def test_quantum_statevector_simulator_initialization():
    """Verify 4-qubit quantum statevector initialization to |0000>."""
    sim = QuantumStatevectorSimulator(num_qubits=4)
    assert sim.num_qubits == 4
    assert sim.dim == 16
    probs = sim.get_probabilities()
    assert len(probs) == 16
    assert probs[0] == pytest.approx(1.0)
    assert sum(probs) == pytest.approx(1.0)


def test_hadamard_gate_superposition():
    """Verify Hadamard gate creates uniform 50/50 superposition."""
    sim = QuantumStatevectorSimulator(num_qubits=1)
    sim.apply_hadamard(target_qubit=0)
    probs = sim.get_probabilities()
    assert probs[0] == pytest.approx(0.5)
    assert probs[1] == pytest.approx(0.5)


def test_solve_qaoa_dag_scheduling():
    """Verify QAOA DAG scheduling solver produces OpenQASM export and optimal assignment."""
    tasks = [{"id": "task_a"}, {"id": "task_b"}]
    res = solve_qaoa_dag_scheduling(tasks, gamma=0.5, beta=0.25)
    assert res["status"] == "EMULATED_QAOA_OPTIMAL"
    assert "OPENQASM 2.0" in res["openqasm"]
    assert len(res["optimal_binary_assignment"]) == 2

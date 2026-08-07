"""TASK-48 exact bounded probability and combinatorics contract."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from mcp_server.server import (
    InvokeRequest,
    build_tools_catalog,
    exact_probability,
    execute_tool,
)
from services.pipeline.contracts import (
    build_direct_precision_plan,
    detect_required_precision_intents,
    is_fully_covered_precision_request,
)
from services.precision_response import render_precision_evidence


def _facts(raw: str) -> dict:
    return json.loads(raw)


@pytest.mark.parametrize(
    "args,fraction",
    [
        (("fraction", None, None, 2, 4, None, None), "1/2"),
        (("combination", 10, 3, None, None, None, None), "120/1"),
        (("permutation", 10, 3, None, None, None, None), "720/1"),
        (("binomial_probability", 10, 3, 1, 2, None, None), "15/128"),
        (("binomial_probability", 5, 0, 0, 1, None, None), "1/1"),
        (("binomial_probability", 5, 5, 1, 1, None, None), "1/1"),
    ],
)
def test_probability_exact_fraction_core(args, fraction):
    assert _facts(exact_probability(*args))["fraction"] == fraction


def test_probability_decimal_is_only_explicit_projection():
    facts = _facts(exact_probability(
        "binomial_probability", 10, 3, 1, 2, 6, "half_even"
    ))
    assert facts["fraction"] == "15/128"
    assert facts["decimal"] == "0.117188"
    assert facts["decimal_scale"] == 6


@pytest.mark.parametrize(
    "args,error",
    [
        (("fraction", None, None, 1, 0, None, None), "denominator"),
        (("combination", 3, 4, None, None, None, None), "k_must"),
        (("combination", -1, 0, None, None, None, None), "n_out"),
        (("binomial_probability", 4, 2, 3, 2, None, None), "between_zero_and_one"),
        (("binomial_probability", 4096, 2, 1, 2**20, None, None), "cost_limit"),
        (("fraction", None, None, 1, 3, 4, None), "supplied_together"),
    ],
)
def test_probability_invalid_or_expensive_inputs_fail_before_computation(args, error):
    with pytest.raises(ValueError, match=error):
        exact_probability(*args)


@pytest.mark.asyncio
async def test_probability_invoke_contract_and_schema_errors():
    good = await execute_tool(InvokeRequest(
        tool="exact_probability",
        args={
            "operation": "binomial_probability", "n": 10, "k": 3,
            "numerator": 1, "denominator": 2,
            "decimal_scale": 6, "rounding": "half_even",
        },
    ))
    bad_type = await execute_tool(InvokeRequest(
        tool="exact_probability",
        args={"operation": "combination", "n": "10", "k": 3},
    ))
    too_large = await execute_tool(InvokeRequest(
        tool="exact_probability",
        args={"operation": "combination", "n": 4097, "k": 3},
    ))
    structured = good["structured_result"]
    assert structured["facts"]["fraction"] == "15/128"
    assert structured["source"]["name"] == "fractions-math-decimal"
    assert bad_type["error_code"] == "input_schema_invalid"
    assert too_large["error_code"] == "input_schema_invalid"


@pytest.mark.parametrize(
    "query,operation",
    [
        ("Berechne die Kombination C(10,3) exakt.", "combination"),
        ("Calculate the permutation P(10,3) exactly.", "permutation"),
        (
            "Berechne die Binomialwahrscheinlichkeit für n=10, k=3, p=1/2 "
            "als Dezimalzahl mit Scale 6 und Rundung half_even.",
            "binomial_probability",
        ),
    ],
)
def test_probability_intents_are_explicit_and_direct(query, operation):
    intents = detect_required_precision_intents(query)
    assert len(intents) == 1
    assert intents[0].tool == "exact_probability"
    assert intents[0].args["operation"] == operation
    assert is_fully_covered_precision_request(query)
    assert build_direct_precision_plan(query)[0]["mcp_tool"] == "exact_probability"


@pytest.mark.parametrize(
    "query",
    [
        "Wie wahrscheinlich ist es, morgen Glück zu haben?",
        "Berechne die Wahrscheinlichkeit für drei Erfolge.",
        "Simuliere die Binomialverteilung für n=10.",
        "Calculate binomial probability for n=10, k=3, p=0.5.",
    ],
)
def test_probability_intent_never_invents_a_random_model(query):
    assert detect_required_precision_intents(query) == []


def test_probability_renderer_keeps_fraction_authoritative():
    facts = _facts(exact_probability(
        "binomial_probability", 10, 3, 1, 2, 6, "half_even"
    ))
    rendered = render_precision_evidence(
        {"status": "completed", "tool": "exact_probability", "facts": facts},
        "Berechne die Wahrscheinlichkeit.",
    )
    assert "15/128" in rendered
    assert "0.117188" in rendered


def test_probability_catalog_declares_cost_limits():
    tool = {item["name"]: item for item in build_tools_catalog()["tools"]}["exact_probability"]
    assert tool["structured_result"] is True
    assert tool["limits"]["max_n"] == 4096
    assert tool["limits"]["max_result_bits"] == 65536

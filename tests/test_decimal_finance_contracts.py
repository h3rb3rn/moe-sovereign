"""TASK-47 deterministic Decimal finance contract."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from mcp_server.server import (
    InvokeRequest,
    build_tools_catalog,
    decimal_finance,
    execute_tool,
)
from services.pipeline.contracts import (
    build_direct_precision_plan,
    detect_required_precision_intents,
    is_fully_covered_precision_request,
)
from services.precision_response import render_precision_evidence


def _facts(raw: str) -> dict:
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    "rounding,expected",
    [("half_even", "2.68"), ("half_up", "2.68"), ("half_down", "2.67")],
)
def test_decimal_rounding_modes_are_explicit(rounding, expected):
    facts = _facts(decimal_finance("add", ["2.675", "0"], "EUR", 2, rounding))
    assert facts["result"] == expected
    assert facts["calculation_value"] == "2.675"
    assert facts["rounding"] == rounding


@pytest.mark.parametrize(
    "operation,operands,expected",
    [
        ("add", ["0.1", "0.2"], "0.30"),
        ("subtract", ["10.00", "0.01"], "9.99"),
        ("multiply", ["12.50", "4"], "50.00"),
        ("divide", ["1", "8"], "0.13"),
        ("percentage", ["119.99", "19"], "22.80"),
        ("simple_interest", ["1000", "5", "2"], "1100.00"),
        ("compound_interest", ["1000", "5", "2", "12"], "1104.94"),
    ],
)
def test_decimal_operations_never_require_binary_float(operation, operands, expected):
    facts = _facts(decimal_finance(operation, operands, "EUR", 2, "half_up"))
    assert facts["result"] == expected
    assert all(isinstance(item, str) for item in facts["operands"])


@pytest.mark.parametrize(
    "operation,operands,error",
    [
        ("divide", ["1", "0"], "division_by_zero"),
        ("percentage", ["01", "19"], "canonical_decimal"),
        ("compound_interest", ["100", "5", "1.5", "12"], "years_must_be_integer"),
        ("compound_interest", ["100", "5", "1000", "365"], "iteration_limit"),
    ],
)
def test_decimal_domain_limits_fail_closed(operation, operands, error):
    with pytest.raises(ValueError, match=error):
        decimal_finance(operation, operands, "EUR", 2, "half_even")


@pytest.mark.asyncio
async def test_decimal_invoke_rejects_float_missing_policy_and_unknown_fields():
    float_input = await execute_tool(InvokeRequest(
        tool="decimal_finance",
        args={
            "operation": "add", "operands": [0.1, "0.2"], "currency": "EUR",
            "scale": 2, "rounding": "half_even",
        },
    ))
    missing_currency = await execute_tool(InvokeRequest(
        tool="decimal_finance",
        args={
            "operation": "add", "operands": ["0.1", "0.2"],
            "scale": 2, "rounding": "half_even",
        },
    ))
    extra = await execute_tool(InvokeRequest(
        tool="decimal_finance",
        args={
            "operation": "add", "operands": ["0.1", "0.2"], "currency": "EUR",
            "scale": 2, "rounding": "half_even", "tax_rate": "19",
        },
    ))
    assert float_input["error_code"] == "input_schema_invalid"
    assert missing_currency["error_code"] == "input_schema_invalid"
    assert extra["error_code"] == "input_schema_invalid"


@pytest.mark.asyncio
async def test_decimal_invoke_returns_typed_hash_bound_evidence():
    response = await execute_tool(InvokeRequest(
        tool="decimal_finance",
        args={
            "operation": "percentage", "operands": ["119.99", "19"],
            "currency": "EUR", "scale": 2, "rounding": "half_up",
        },
    ))
    structured = response["structured_result"]
    assert structured["facts"]["result"] == "22.80"
    assert structured["determinism"] == "input_only"
    assert structured["source"]["name"] == "decimal"
    assert len(structured["contract_hash"]) == len(structured["result_hash"]) == 64


@pytest.mark.parametrize(
    "query,operation,operands",
    [
        (
            "Berechne 19 Prozent von 119.99 EUR mit Scale 2 und Rundung half_up.",
            "percentage", ["119.99", "19"],
        ),
        (
            "Calculate 19 percent of 119.99 EUR with scale 2 and rounding half_up.",
            "percentage", ["119.99", "19"],
        ),
        (
            "Berechne 10.10 + 20.20 EUR mit Scale 2 und Rundung half_even.",
            "add", ["10.10", "20.20"],
        ),
    ],
)
def test_decimal_intent_is_narrow_typed_and_direct(query, operation, operands):
    intents = detect_required_precision_intents(query)
    assert len(intents) == 1
    assert intents[0].tool == "decimal_finance"
    assert intents[0].args["operation"] == operation
    assert intents[0].args["operands"] == operands
    assert is_fully_covered_precision_request(query)
    assert build_direct_precision_plan(query)[0]["mcp_tool"] == "decimal_finance"


@pytest.mark.parametrize(
    "query",
    [
        "Berechne 19 Prozent von 119.99 EUR.",
        "Berechne 19 Prozent von 119.99 mit Scale 2 und Rundung half_up.",
        "Berechne die Mehrwertsteuer für 119.99 EUR mit Scale 2 und Rundung half_up.",
        "Convert 100 EUR to USD with scale 2 and rounding half_even.",
        "Berechne 1.234,56 + 2 EUR mit Scale 2 und Rundung half_even.",
    ],
)
def test_decimal_intent_does_not_guess_missing_or_jurisdictional_semantics(query):
    assert detect_required_precision_intents(query) == []


def test_decimal_renderer_uses_evidence_strings_verbatim():
    facts = _facts(decimal_finance("percentage", ["119.99", "19"], "EUR", 2, "half_up"))
    rendered = render_precision_evidence(
        {"status": "completed", "tool": "decimal_finance", "facts": facts},
        "Berechne den Wert.",
    )
    assert "22.80 EUR" in rendered
    assert "half_up" in rendered


def test_decimal_tool_is_discoverable_with_full_contract():
    tool = {item["name"]: item for item in build_tools_catalog()["tools"]}["decimal_finance"]
    assert tool["structured_result"] is True
    assert tool["contract_version"] == "1.0.0"
    assert tool["inputSchema"]["properties"]["operands"]["items"]["type"] == "string"
    assert tool["cache_policy"] == {"mode": "bypass"}

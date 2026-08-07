"""Advice rules may not inject half-specified MCP work into typed plans."""

from services.advice_store import enforce_advice_rules


def test_math_advice_does_not_inject_calculate_without_expression():
    plan = [
        {
            "task": "Berechne 19 Prozent von 119.99 EUR mit Scale 2 und Rundung half_up.",
            "category": "precision_tools",
            "mcp_tool": "decimal_finance",
            "mcp_args": {
                "operation": "percentage", "operands": ["119.99", "19"],
                "currency": "EUR", "scale": 2, "rounding": "half_up",
            },
        }
    ]
    schemas = {
        "calculate": {"required": ["expression"]},
        "decimal_finance": {"required": ["operation", "operands", "currency", "scale", "rounding"]},
    }

    result = enforce_advice_rules(
        "Berechne 19 Prozent von 119.99 EUR mit Scale 2 und Rundung half_up.",
        plan,
        schemas,
    )

    assert result == plan


def test_advice_keeps_an_already_executable_calculate_task():
    plan = [{
        "task": "calculate", "category": "precision_tools",
        "mcp_tool": "calculate", "mcp_args": {"expression": "2+2"},
    }]
    result = enforce_advice_rules(
        "Calculate 2+2.", plan, {"calculate": {"required": ["expression"]}}
    )
    assert result == plan

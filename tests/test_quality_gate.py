"""Contract tests for the final Trust/HITL decision."""

from services.quality_gate import evaluate_quality_gate
from services.pipeline.contracts import build_precision_preflight, canonical_json_hash
from services.precision_response import (
    bind_precision_response,
    build_precision_fact_slots,
)


def _state(**overrides):
    value = {
        "response_id": "req-quality",
        "user_id": "user-1",
        "input": "Analyse a complex system",
        "final_response": "draft that must not leak",
        "trust_verdict": "PROCEED",
        "complexity_level": "moderate",
        "plan": [{"id": "task-1", "category": "code", "task": "analyse"}],
        "task_events": [
            {
                "task_id": "task-1",
                "category": "code",
                "status": "completed",
                "executor": "workers",
                "iteration": 0,
            }
        ],
        "constitution_violations": [],
        "enable_graphrag": False,
    }
    value.update(overrides)
    return value


def test_trust_block_requires_withholding():
    result = evaluate_quality_gate(_state(trust_verdict="BLOCK"))
    assert result.action == "block"
    assert result.reason == "trust_score_block"
    assert result.cynefin_domain == "CHAOTIC"


def test_assumption_complex_requires_gate():
    result = evaluate_quality_gate(
        _state(
            trust_verdict="PROCEED_WITH_ASSUMPTION",
            complexity_level="complex",
        )
    )
    assert result.action == "gate"
    assert "Cynefin COMPLEX" in result.reason


def test_assumption_with_constitution_warning_requires_gate():
    result = evaluate_quality_gate(
        _state(
            trust_verdict="PROCEED_WITH_ASSUMPTION",
            constitution_violations=[
                {"rule_id": "warn-1", "on_violation": "warn"}
            ],
        )
    )
    assert result.action == "gate"
    assert "constitution warning" in result.reason


def test_proceed_passes_without_gate():
    result = evaluate_quality_gate(_state())
    assert result.action == "pass"


def test_missing_terminal_task_event_blocks_release():
    result = evaluate_quality_gate(
        _state(
            task_events=[
                {
                    "task_id": "task-1",
                    "category": "code",
                    "status": "planned",
                    "executor": "planner",
                    "iteration": 0,
                }
            ]
        )
    )

    assert result.action == "block"
    assert result.reason == "incomplete_task_execution:task-1:planned"


def test_failed_precision_task_blocks_release():
    result = evaluate_quality_gate(
        _state(
            plan=[
                {
                    "id": "task-precision",
                    "category": "precision_tools",
                    "task": "calculate",
                }
            ],
            task_events=[
                {
                    "task_id": "task-precision",
                    "category": "precision_tools",
                    "status": "failed",
                    "executor": "mcp",
                    "iteration": 0,
                    "reason": "tool_error",
                }
            ],
        )
    )

    assert result.action == "block"
    assert "task-precision:failed" in result.reason


def _precision_state(**overrides):
    query = "Berechne den GGT von 391 und 299."
    schema = {
        "required": ["a", "b"],
        "args": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
            "operation": {"type": "string", "default": "both"},
        },
        "access_kind": "read",
        "contract_id": "moe.precision.gcd_lcm",
        "contract_version": "1.0.0",
        "contract_hash": "a" * 64,
        "determinism": "input_only",
        "output_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
                "operation": {"type": "string"},
                "gcd": {"type": "integer"},
                "lcm": {"type": "integer"},
            },
            "required": ["a", "b", "operation", "gcd", "lcm"],
            "additionalProperties": False,
        },
        "structured_result_required": True,
    }
    preflight = build_precision_preflight(query, {"gcd_lcm": schema})
    schema_hash = preflight["required_precision_intents"][0]["schema_hash"]
    args = {"a": 391, "b": 299, "operation": "gcd"}
    facts = {
        "a": 391,
        "b": 299,
        "operation": "gcd",
        "gcd": 23,
        "lcm": 5083,
    }
    value = _state(
        input=query,
        plan=[
            {
                "id": "task-precision",
                "category": "precision_tools",
                "task": query,
                "mcp_tool": "gcd_lcm",
                "mcp_args": args,
            }
        ],
        task_events=[
            {
                "task_id": "task-precision",
                "category": "precision_tools",
                "status": "completed",
                "executor": "mcp",
                "iteration": 0,
            }
        ],
        mcp_evidence=[
            {
                "task_id": "task-precision",
                "tool": "gcd_lcm",
                "args": args,
                "iteration": 0,
                "status": "completed",
                "result": "GCD(391, 299) = 23",
                "error": "",
                "source": "mcp_precision",
                "contract_hash": schema_hash,
                "input_hash": canonical_json_hash(args),
                "contract_id": schema["contract_id"],
                "contract_version": schema["contract_version"],
                "facts": facts,
                "result_hash": canonical_json_hash(
                    {
                        "contract_hash": schema_hash,
                        "input_normalized": args,
                        "facts": facts,
                    }
                ),
                "determinism": schema["determinism"],
                "source_metadata": {
                    "kind": "python_stdlib",
                    "name": "math",
                    "version": "3.11.0",
                    "as_of": None,
                },
                "warnings": [],
            }
        ],
        **preflight,
    )
    slots, projection, errors = build_precision_fact_slots(value)
    assert not errors
    value.update(
        {
            "precision_fact_slots": slots,
            "precision_prompt_projection": projection,
            "final_response": slots[0]["marker"],
        }
    )
    value.update(bind_precision_response(value))
    value.update(overrides)
    return value


def test_precision_evidence_chain_passes_when_all_hashes_match():
    result = evaluate_quality_gate(_precision_state())

    assert result.action == "pass"


def test_precision_post_binding_response_mutation_is_blocked():
    state = _precision_state()
    state["final_response"] = state["final_response"].replace("23", "29")

    result = evaluate_quality_gate(state)

    assert result.action == "block"
    assert result.reason == "precision_binding_mismatch"


def test_precision_evidence_without_final_binding_is_blocked():
    state = _precision_state(
        precision_binding_status="prepared",
        precision_binding_hash="",
        precision_bound_response_hash="",
    )

    result = evaluate_quality_gate(state)

    assert result.action == "block"
    assert result.reason == "precision_binding_missing"


def test_precision_completed_task_without_evidence_is_blocked():
    result = evaluate_quality_gate(_precision_state(mcp_evidence=[]))

    assert result.action == "block"
    assert result.reason == "precision_evidence_missing:task-precision"


def test_precision_evidence_argument_or_hash_drift_is_blocked():
    state = _precision_state()
    state["mcp_evidence"][0]["args"] = {"a": 391, "b": 300, "operation": "gcd"}

    result = evaluate_quality_gate(state)

    assert result.action == "block"
    assert result.reason == "precision_evidence_mismatch:task-precision:content"


def test_precision_request_without_preflight_is_blocked_even_with_empty_plan():
    result = evaluate_quality_gate(
        _state(
            input="Berechne den GGT von 391 und 299.",
            plan=[],
            task_events=[],
        )
    )

    assert result.action == "block"
    assert result.reason == "precision_preflight_missing"

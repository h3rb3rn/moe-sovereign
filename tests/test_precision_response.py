"""Contracts for model-free rendering and post-critic precision binding."""

import copy

from services.pipeline.contracts import (
    build_direct_precision_plan,
    build_precision_preflight,
    canonical_json_hash,
    is_fully_covered_precision_request,
)
from services.precision_response import (
    bind_precision_response,
    build_precision_fact_slots,
    compose_mixed_precision_candidate,
    render_direct_precision_response,
)


def _schemas():
    return {
        "gcd_lcm": {
            "required": ["a", "b"],
            "args": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
                "operation": {"type": "string", "default": "both"},
            },
            "contract_hash": "a" * 64,
        },
        "calendar_facts": {
            "required": ["date_str"],
            "args": {
                "date_str": {"type": "string"},
                "locale": {"type": "string", "default": "de"},
            },
            "contract_hash": "b" * 64,
        },
        "unit_convert": {
            "required": ["value", "from_unit", "to_unit"],
            "args": {
                "value": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
            },
            "contract_hash": "c" * 64,
        },
    }


def _two_fact_state():
    query = (
        "1. Berechne den GGT von 391 und 299.\n"
        "2. Welcher Wochentag ist der 29.07.2026?"
    )
    plan = build_direct_precision_plan(query)
    preflight = build_precision_preflight(query, _schemas())
    gcd_args = plan[0]["mcp_args"]
    calendar_args = plan[1]["mcp_args"]
    evidence = [
        {
            "task_id": plan[0]["id"],
            "tool": "gcd_lcm",
            "args": gcd_args,
            "iteration": 0,
            "status": "completed",
            "contract_hash": "a" * 64,
            "result_hash": canonical_json_hash({"gcd": 23}),
            "facts": {
                "a": 391,
                "b": 299,
                "operation": "gcd",
                "gcd": 23,
                "lcm": 5083,
            },
        },
        {
            "task_id": plan[1]["id"],
            "tool": "calendar_facts",
            "args": calendar_args,
            "iteration": 0,
            "status": "completed",
            "contract_hash": "b" * 64,
            "result_hash": canonical_json_hash({"weekday_name": "Mittwoch"}),
            "facts": {
                "date": "2026-07-29",
                "weekday_name": "Mittwoch",
                "locale": "de",
            },
        },
    ]
    return {
        "input": query,
        "plan": plan,
        "agentic_iteration": 0,
        "mcp_evidence": evidence,
        **preflight,
    }


def test_direct_coverage_is_strict_and_does_not_swallow_other_work():
    assert is_fully_covered_precision_request(
        "Berechne den GGT von 391 und 299."
    )
    assert is_fully_covered_precision_request("Rechne 36 km/h in m/s um.")
    assert is_fully_covered_precision_request(
        "Welcher Wochentag ist der 29.07.2026?"
    )
    assert is_fully_covered_precision_request(
        "1. Berechne den GGT von 391 und 299.\n"
        "2. Welcher Wochentag ist der 29.07.2026?"
    )

    assert not is_fully_covered_precision_request(
        "Berechne den GGT von 391 und 299 und erkläre den Algorithmus."
    )
    assert not is_fully_covered_precision_request(
        "1. Berechne den GGT von 391 und 299.\n"
        "2. Erkläre den euklidischen Algorithmus."
    )
    assert not is_fully_covered_precision_request(
        "Bitte bearbeite: 1. Berechne den GGT von 391 und 299."
    )


def test_slot_projection_hides_results_and_direct_renderer_uses_typed_facts():
    state = _two_fact_state()
    slots, projection, errors = build_precision_fact_slots(state)

    assert errors == []
    assert len(slots) == 2
    assert "ist 23" not in projection
    assert "Mittwoch" not in projection
    assert projection.count("[[MOE_PRECISION:") == 2
    assert render_direct_precision_response(slots) == (
        "1. Der größte gemeinsame Teiler von 391 und 299 ist 23.\n"
        "2. Der 2026-07-29 ist ein Mittwoch."
    )


def test_mixed_binding_replaces_each_ordered_slot_exactly_once():
    state = _two_fact_state()
    slots, projection, errors = build_precision_fact_slots(state)
    state.update(
        {
            "precision_fact_slots": slots,
            "precision_prompt_projection": projection,
            "final_response": (
                f"Erster geprüfter Fakt:\n{slots[0]['marker']}\n"
                f"Zweiter geprüfter Fakt:\n{slots[1]['marker']}"
            ),
        }
    )

    result = bind_precision_response(state)

    assert result["precision_binding_status"] == "bound"
    assert "ist 23" in result["final_response"]
    assert "Mittwoch" in result["final_response"]
    assert "[[MOE_PRECISION:" not in result["final_response"]


def test_binding_blocks_marker_wrapped_in_model_authored_context():
    state = _two_fact_state()
    slots, projection, errors = build_precision_fact_slots(state)
    assert not errors

    result = bind_precision_response(
        {
            **state,
            "precision_fact_slots": slots,
            "precision_prompt_projection": projection,
            "final_response": (
                f"Das Ergebnis ist {slots[0]['marker']}\n"
                f"{slots[1]['marker']}"
            ),
        }
    )

    assert result["precision_binding_status"] == "failed"
    assert "precision_binding_slot_context" in result["precision_binding_errors"]


def test_binding_blocks_independent_duplicate_gcd_claim_outside_slot():
    state = _two_fact_state()
    slots, projection, errors = build_precision_fact_slots(state)
    assert not errors

    result = bind_precision_response(
        {
            **state,
            "precision_fact_slots": slots,
            "precision_prompt_projection": projection,
            "final_response": (
                f"{slots[0]['marker']}\n"
                f"Der GGT ist angeblich 29.\n"
                f"{slots[1]['marker']}"
            ),
        }
    )

    assert result["precision_binding_status"] == "failed"
    assert "precision_binding_unbound_restatement" in result["precision_binding_errors"]


def _mixed_gcd_state(precision_first=True):
    precision_item = "Berechne den GGT von 391 und 299."
    expert_item = "Prüfe, ob parametrisierte SQL-Abfragen Injection verhindern."
    items = [precision_item, expert_item] if precision_first else [expert_item, precision_item]
    query = "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))
    preflight = build_precision_preflight(query, _schemas())
    source_item = 0 if precision_first else 1
    plan = [
        {
            "id": "precision-gcd",
            "task": precision_item,
            "category": "precision_tools",
            "mcp_tool": "gcd_lcm",
            "mcp_args": {"a": 391, "b": 299, "operation": "gcd"},
        },
        {
            "id": "expert-sql",
            "task": expert_item,
            "category": "code_reviewer",
        },
    ]
    state = {
        "input": query,
        "plan": plan,
        "agentic_iteration": 0,
        "mcp_evidence": [{
            "task_id": "precision-gcd",
            "tool": "gcd_lcm",
            "args": {"a": 391, "b": 299, "operation": "gcd"},
            "iteration": 0,
            "status": "completed",
            "contract_hash": "a" * 64,
            "result_hash": canonical_json_hash({"gcd": 23}),
            "facts": {
                "a": 391,
                "b": 299,
                "operation": "gcd",
                "gcd": 23,
                "lcm": 5083,
            },
        }],
        **preflight,
    }
    assert preflight["required_precision_intents"][0]["source_item"] == source_item
    slots, projection, errors = build_precision_fact_slots(state)
    assert not errors
    return {
        **state,
        "precision_fact_slots": slots,
        "precision_prompt_projection": projection,
        "precision_binding_errors": [],
    }


def test_hybrid_composer_preserves_input_order_and_keeps_facts_opaque():
    expert = (
        "[qwen3.6:35b / code_reviewer]: CORE_FINDING: Parameter binding is safe.\n"
        "CONFIDENCE: high\nGAPS: none\nREFERRAL: —\nDETAILS:\n"
        "Parametrisierte SQL-Abfragen trennen Daten vom SQL-Code."
    )

    first = _mixed_gcd_state(precision_first=True)
    second = _mixed_gcd_state(precision_first=False)
    first_result = compose_mixed_precision_candidate(first, [expert])
    second_result = compose_mixed_precision_candidate(second, [expert])

    assert first_result is not None
    assert second_result is not None
    first_candidate, first_body, _ = first_result
    second_candidate, _, _ = second_result
    marker = first["precision_fact_slots"][0]["marker"]
    assert first_candidate.startswith(marker)
    assert second_candidate.endswith(second["precision_fact_slots"][0]["marker"])
    assert "23" not in first_candidate
    assert "CONFIDENCE" not in first_body

    bound = bind_precision_response({**first, "final_response": first_candidate})
    assert bound["precision_binding_status"] == "bound"
    assert "ist 23" in bound["final_response"]


def test_hybrid_composer_rejects_low_confidence_or_precision_restatement():
    state = _mixed_gcd_state()
    low = "[model / code_reviewer]: CONFIDENCE: low\nDETAILS:\nUse parameters."
    duplicate = (
        "[model / code_reviewer]: CONFIDENCE: high\nDETAILS:\n"
        "Der GGT ist 29. Verwende außerdem SQL-Parameter."
    )

    assert compose_mixed_precision_candidate(state, [low]) is None
    assert compose_mixed_precision_candidate(state, [duplicate]) is None

    medium = (
        "[model / code_reviewer]: CONFIDENCE: medium\nDETAILS:\n"
        "Verwende eine parametrisierte SQL-Abfrage."
    )
    assert compose_mixed_precision_candidate(state, [medium]) is not None

    tagless_medium = (
        "[model / code_reviewer]: Die direkte Konkatenation ist unsicher. "
        "Verwende cursor.execute mit einem gebundenen Platzhalter und einem "
        "separaten Parameter-Tupel."
    )
    assert compose_mixed_precision_candidate(state, [tagless_medium]) is not None


def test_hybrid_composer_rejects_expert_task_that_contains_full_mixed_input():
    state = _mixed_gcd_state()
    state["plan"][1]["task"] = state["input"]
    expert = (
        "[model / code_reviewer]: CONFIDENCE: high\nDETAILS:\n"
        "Verwende parametrisierte SQL-Abfragen."
    )

    assert compose_mixed_precision_candidate(state, [expert]) is None


def test_binding_blocks_removed_duplicated_changed_and_swapped_slots():
    state = _two_fact_state()
    slots, projection, errors = build_precision_fact_slots(state)
    assert not errors
    base = {
        **state,
        "precision_fact_slots": slots,
        "precision_prompt_projection": projection,
    }
    candidates = {
        "removed": f"Nur {slots[0]['marker']}",
        "duplicated": (
            f"{slots[0]['marker']} {slots[0]['marker']} {slots[1]['marker']}"
        ),
        "changed": f"Das Ergebnis ist 29. {slots[1]['marker']}",
        "swapped": f"{slots[1]['marker']} {slots[0]['marker']}",
    }

    for name, candidate in candidates.items():
        result = bind_precision_response({**copy.deepcopy(base), "final_response": candidate})
        assert result["precision_binding_status"] == "failed", name
        assert result["final_response"] == "", name


def test_direct_binding_rejects_any_post_renderer_mutation():
    state = _two_fact_state()
    slots, projection, errors = build_precision_fact_slots(state)
    rendered = render_direct_precision_response(slots)
    base = {
        **state,
        "precision_direct": True,
        "precision_fact_slots": slots,
        "precision_prompt_projection": projection,
        "precision_rendered_response": rendered,
    }

    passed = bind_precision_response({**base, "final_response": rendered})
    failed = bind_precision_response(
        {**base, "final_response": rendered.replace("23", "29")}
    )

    assert passed["precision_binding_status"] == "bound"
    assert failed["precision_binding_status"] == "failed"
    assert failed["precision_binding_errors"] == [
        "precision_direct_response_mismatch"
    ]

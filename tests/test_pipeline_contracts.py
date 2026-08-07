"""Tests for the shared planner/judge output contracts."""

import pytest

from services.pipeline.contracts import (
    PlannerContractError,
    assign_stable_task_ids,
    detect_required_precision_intents,
    parse_plan,
    recover_explicit_supported_plan,
    repair_precision_task_contracts,
    validate_plan_or_raise,
    validate_plan_tasks,
    validate_required_precision_intents,
)
from services.pipeline import chat as chat_pipeline
from services import inference
from graph.planner import _compact_planner_role, planner_node
from graph.tool_nodes import _validate_tool_result
import state as runtime_state


def test_parse_plan_preserves_full_array_task_payload():
    result = parse_plan(
        '[{"task":"Research","category":"research",'
        '"search_query":"current standard","depends_on":[0]}]'
    )

    assert result.valid is True
    assert result.tasks[0].instruction == "Research"
    assert result.tasks[0].payload["search_query"] == "current standard"
    assert result.tasks[0].payload["depends_on"] == [0]


def test_parse_plan_accepts_wrapped_tasks_after_thinking_trace():
    result = parse_plan(
        '<think>private reasoning</think>\n'
        '{"tasks":[{"instruction":"Calculate","category":"math"}]}'
    )

    assert result.valid is True
    assert result.tasks[0].category == "math"


def test_parse_plan_rejects_non_task_json():
    assert parse_plan('{"message":"not a plan"}').valid is False


def test_precision_task_requires_tool_and_args():
    issues = validate_plan_tasks(
        [{"task": "Compute exactly", "category": "precision_tools"}],
        {"calculate": {"required": ["expression"], "args": {}}},
    )

    assert [issue.code for issue in issues] == ["missing_mcp_tool"]


def test_precision_task_rejects_missing_required_tool_argument():
    issues = validate_plan_tasks(
        [
            {
                "task": "Compute exactly",
                "category": "precision_tools",
                "mcp_tool": "calculate",
                "mcp_args": {},
            }
        ],
        {"calculate": {"required": ["expression"], "args": {}}},
    )

    assert [issue.code for issue in issues] == ["missing_mcp_args"]
    assert "expression" in issues[0].message


def test_precision_task_rejects_unknown_discovered_tool():
    issues = validate_plan_tasks(
        [
            {
                "task": "Compute exactly",
                "category": "precision_tools",
                "mcp_tool": "imaginary_tool",
                "mcp_args": {},
            }
        ],
        {"calculate": {"required": ["expression"], "args": {}}},
    )

    assert [issue.code for issue in issues] == ["unknown_mcp_tool"]


def test_precision_tool_with_no_required_args_accepts_empty_object():
    validate_plan_or_raise(
        [
            {
                "task": "Fetch deterministic status",
                "category": "precision_tools",
                "mcp_tool": "status",
                "mcp_args": {},
            }
        ],
        {"status": {"required": [], "args": {}}},
    )


def test_plan_over_runtime_budget_is_rejected_instead_of_truncated():
    tasks = [
        {"task": f"work item {index}", "category": "general"}
        for index in range(5)
    ]

    issues = validate_plan_tasks(tasks, {}, max_tasks=4)

    assert [issue.code for issue in issues] == ["too_many_tasks"]
    assert "without omitting any requested outcome" in issues[0].message


def test_deterministic_precision_contract_repair_for_task38_tools():
    schemas = {
        "gcd_lcm": {"required": ["a", "b"]},
        "unit_convert": {"required": ["value", "from_unit", "to_unit"]},
        "calendar_facts": {"required": ["date_str"]},
    }
    tasks, repairs = repair_precision_task_contracts(
        [
            {
                "task": "Berechne den größten gemeinsamen Teiler von 391 und 299",
                "category": "precision_tools",
            },
            {
                "task": "Rechne 72 km/h exakt in m/s um",
                "category": "precision_tools",
            },
            {
                "task": "Bestimme den Wochentag für den 29.07.2026",
                "category": "precision_tools",
            },
        ],
        "",
        schemas,
    )

    assert [task["mcp_tool"] for task in tasks] == [
        "gcd_lcm",
        "unit_convert",
        "calendar_facts",
    ]
    assert tasks[0]["mcp_args"] == {"a": 391, "b": 299, "operation": "gcd"}
    assert tasks[1]["mcp_args"] == {
        "value": 72,
        "from_unit": "km/h",
        "to_unit": "m/s",
    }
    assert tasks[2]["mcp_args"] == {
        "date_str": "2026-07-29",
        "locale": "de",
    }
    assert len(repairs) == 3
    validate_plan_or_raise(tasks, schemas)


def test_precision_contract_repair_remains_fail_closed_when_ambiguous():
    tasks, repairs = repair_precision_task_contracts(
        [{"task": "Berechne das exakt", "category": "precision_tools"}],
        "Berechne etwas",
        {"calculate": {"required": ["expression"]}},
    )

    assert repairs == []
    assert "mcp_tool" not in tasks[0]
    with pytest.raises(PlannerContractError):
        validate_plan_or_raise(tasks, {"calculate": {"required": ["expression"]}})


def _precision_intent_schemas():
    return {
        "gcd_lcm": {
            "required": ["a", "b"],
            "args": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
                "operation": {"type": "string", "default": "gcd"},
            },
        },
        "unit_convert": {
            "required": ["value", "from_unit", "to_unit"],
            "args": {},
        },
        "calendar_facts": {
            "required": ["date_str"],
            "args": {
                "date_str": {"type": "string"},
                "locale": {"type": "string", "default": "de"},
            },
        },
    }


@pytest.mark.parametrize(
    ("query", "tool", "expected_args"),
    [
        (
            "Berechne den GGT von 391 und 299.",
            "gcd_lcm",
            {"a": 391, "b": 299, "operation": "gcd"},
        ),
        (
            "Rechne 72 km/h exakt in m/s um.",
            "unit_convert",
            {"value": 72, "from_unit": "km/h", "to_unit": "m/s"},
        ),
        (
            "Bestimme den Wochentag für den 29.07.2026.",
            "calendar_facts",
            {"date_str": "2026-07-29", "locale": "de"},
        ),
        (
            "Which weekday was 2026-07-29?",
            "calendar_facts",
            {"date_str": "2026-07-29", "locale": "en"},
        ),
    ],
)
def test_detect_required_precision_intents_is_narrow_and_multilingual(
    query,
    tool,
    expected_args,
):
    intents = detect_required_precision_intents(query)

    assert [(intent.tool, intent.args) for intent in intents] == [
        (tool, expected_args)
    ]


def test_precision_intent_guard_rejects_general_planner_downgrade():
    query = "Bestimme den Wochentag für den 29.07.2026."
    tasks = [{"task": query, "category": "general"}]

    with pytest.raises(PlannerContractError) as exc_info:
        validate_plan_or_raise(
            tasks,
            _precision_intent_schemas(),
            input_query=query,
        )

    assert [issue.code for issue in exc_info.value.issues] == [
        "precision_intent_downgraded"
    ]
    assert "calendar_facts" in exc_info.value.repair_instruction()


@pytest.mark.asyncio
async def test_planner_handoff_applies_precision_intent_guard_to_direct_routes(
    monkeypatch,
):
    query = "Bestimme den Wochentag für den 29.07.2026."
    monkeypatch.setattr(
        runtime_state,
        "MCP_TOOL_SCHEMAS",
        _precision_intent_schemas(),
    )

    with pytest.raises(PlannerContractError) as exc_info:
        await planner_node(
            {
                "input": query,
                "direct_expert": "general",
                "plan": [{"task": query, "category": "general"}],
                "response_id": "guard-test",
            }
        )

    assert [issue.code for issue in exc_info.value.issues] == [
        "precision_intent_downgraded"
    ]


def test_precision_intent_guard_accepts_matching_plan_and_optional_defaults():
    query = "Bestimme den Wochentag für den 29.07.2026."

    validate_plan_or_raise(
        [
            {
                "task": query,
                "category": "precision_tools",
                "mcp_tool": "calendar_facts",
                # German locale is the schema default and may be omitted.
                "mcp_args": {"date_str": "2026-07-29"},
            }
        ],
        _precision_intent_schemas(),
        input_query=query,
    )


def test_precision_intent_guard_rejects_wrong_semantic_arguments_or_tool():
    query = "Which weekday was 2026-07-29?"
    wrong_args = [
        {
            "task": query,
            "category": "precision_tools",
            "mcp_tool": "calendar_facts",
            "mcp_args": {"date_str": "2026-07-30", "locale": "en"},
        }
    ]
    wrong_tool = [
        {
            "task": query,
            "category": "precision_tools",
            "mcp_tool": "day_of_week",
            "mcp_args": {"date_str": "2026-07-29"},
        }
    ]

    assert [
        issue.code
        for issue in validate_required_precision_intents(
            wrong_args,
            query,
            _precision_intent_schemas(),
        )
    ] == ["precision_intent_downgraded"]
    assert [
        issue.code
        for issue in validate_required_precision_intents(
            wrong_tool,
            query,
            {
                **_precision_intent_schemas(),
                "day_of_week": {"required": ["date_str"], "args": {}},
            },
        )
    ] == ["precision_intent_downgraded"]


def test_precision_intent_guard_fails_closed_when_required_tool_is_unavailable():
    query = "Berechne den GGT von 391 und 299."

    issues = validate_required_precision_intents(
        [{"task": query, "category": "general"}],
        query,
        {"calendar_facts": _precision_intent_schemas()["calendar_facts"]},
    )

    assert [issue.code for issue in issues] == [
        "required_precision_tool_unavailable"
    ]


def test_precision_intent_guard_preserves_deterministic_items_in_mixed_plan():
    query = """Bearbeite beide Aufgaben:
1. Bestimme den Wochentag für den 29.07.2026.
2. Erkläre anschließend die historische Bedeutung des Datums.
"""
    tasks = [
        {
            "task": "Bestimme den Wochentag.",
            "category": "general",
        },
        {
            "task": "Erkläre die historische Bedeutung.",
            "category": "research",
        },
    ]

    issues = validate_required_precision_intents(
        tasks,
        query,
        _precision_intent_schemas(),
    )

    assert [issue.code for issue in issues] == ["precision_intent_downgraded"]
    assert "numbered input item 1" in issues[0].message


@pytest.mark.parametrize(
    "query",
    [
        "Die Dokumentation erwähnt den Wochentag 29.07.2026 nur als Beispiel.",
        "Implementiere Python-Code, der den Wochentag für 29.07.2026 bestimmt.",
        "Bestimme den Wochentag für den 31.02.2026.",
        "Berechne den GGT von 10 und 15 sowie den Wochentag für 2026-07-29.",
        "1. Berechne den GGT von 10 und 15.\n3. Bestimme den Wochentag für 2026-07-29.",
    ],
)
def test_precision_intent_guard_does_not_guess_ambiguous_or_meta_requests(query):
    assert detect_required_precision_intents(query) == []


def test_empty_planner_result_recovers_fully_explicit_supported_task_list():
    schemas = {
        "gcd_lcm": {"required": ["a", "b"]},
        "unit_convert": {"required": ["value", "from_unit", "to_unit"]},
        "calendar_facts": {"required": ["date_str"]},
    }
    query = """Bearbeite alle Teilaufgaben:
1. Berechne den größten gemeinsamen Teiler von 391 und 299.
2. Rechne 72 km/h exakt in m/s um.
3. Bestimme den Wochentag für den 29.07.2026.
4. Prüfe cursor.execute("SELECT * FROM students WHERE name = '" + user_input + "'")
   auf SQL-Injection und gib eine sichere parametrisierte Ersatzzeile an.
"""

    tasks, repairs = recover_explicit_supported_plan(
        query,
        schemas,
        max_tasks=4,
    )

    assert [task["category"] for task in tasks] == [
        "precision_tools",
        "precision_tools",
        "precision_tools",
        "code_reviewer",
    ]
    assert [task.get("mcp_tool") for task in tasks[:3]] == [
        "gcd_lcm",
        "unit_convert",
        "calendar_facts",
    ]
    assert len(repairs) == 4
    validate_plan_or_raise(tasks, schemas, max_tasks=4)


def test_empty_planner_result_recovery_rejects_any_unknown_numbered_item():
    tasks, repairs = recover_explicit_supported_plan(
        """1. Berechne den GGT von 10 und 15.
2. Erkläre mir anschließend Quantenchromodynamik.
""",
        {"gcd_lcm": {"required": ["a", "b"]}},
        max_tasks=4,
    )

    assert tasks == []
    assert repairs == []


def test_empty_planner_result_recovery_rejects_non_contiguous_or_over_budget():
    schemas = {"gcd_lcm": {"required": ["a", "b"]}}

    non_contiguous, _ = recover_explicit_supported_plan(
        "1. Berechne den GGT von 10 und 15.\n3. Berechne den GGT von 21 und 14.",
        schemas,
        max_tasks=4,
    )
    over_budget, _ = recover_explicit_supported_plan(
        "\n".join(
            f"{index}. Berechne den GGT von {index + 10} und {index + 20}."
            for index in range(1, 6)
        ),
        schemas,
        max_tasks=4,
    )

    assert non_contiguous == []
    assert over_budget == []


def test_contract_error_provides_bounded_repair_instruction():
    with pytest.raises(PlannerContractError) as exc_info:
        validate_plan_or_raise(
            [
                {
                    "task": "Compute",
                    "category": "precision_tools",
                    "mcp_tool": "calculate",
                    "mcp_args": {},
                }
            ],
            {"calculate": {"required": ["expression"], "args": {}}},
        )

    repair = exc_info.value.repair_instruction()
    assert "task[0]" in repair
    assert "Do not remove or downgrade" in repair


def test_assign_stable_task_ids_preserves_unique_explicit_ids():
    tasks = assign_stable_task_ids(
        [
            {"id": "research", "task": "Research", "category": "research"},
            {"task": "Compute", "category": "precision_tools"},
            {"id": "research", "task": "Explain", "category": "general"},
        ]
    )

    assert [task["id"] for task in tasks] == ["research", "task-2", "task-3"]


def test_nonstream_timeout_budget_includes_preprocessing():
    source = open(chat_pipeline.__file__, encoding="utf-8").read()
    assert "_request_started = time.monotonic()" in source
    assert "ORCHESTRATION_TIMEOUT - (time.monotonic() - _request_started)" in source
    assert "timeout=_remaining_timeout" in source


def test_planner_role_compaction_preserves_policy_edges():
    role = "BEGIN-MANDATORY\n" + ("middle\n" * 3000) + "END-MANDATORY"
    compacted = _compact_planner_role(role, 4000)

    assert compacted.startswith("BEGIN-MANDATORY")
    assert compacted.endswith("END-MANDATORY")
    assert "compacted to runtime budget" in compacted
    assert len(compacted) < len(role)


@pytest.mark.parametrize(
    "result",
    [
        "Error: invalid date",
        "Fehler: ungültiges Datum",
        "[calendar_facts error: invalid date]",
        '{"error":"invalid date"}',
    ],
)
def test_mcp_error_payloads_cannot_become_precision_evidence(result):
    assert _validate_tool_result(result, "calendar_facts") == (
        False,
        "error_result",
    )


def test_mcp_valid_calendar_json_is_precision_evidence():
    assert _validate_tool_result(
        '{"date":"2026-07-29","weekday_name":"Mittwoch"}',
        "calendar_facts",
    ) == (True, "")


def test_structured_planner_disables_hidden_reasoning_by_default():
    source = open(inference.__file__, encoding="utf-8").read()

    assert '"think":      PLANNER_THINKING_ENABLED' in source
    assert '"think": PLANNER_THINKING_ENABLED' in source


def test_bounded_expert_and_judge_disable_hidden_reasoning_by_default():
    import config

    expert_source = open(
        "graph/expert.py",
        encoding="utf-8",
    ).read()
    inference_source = open(inference.__file__, encoding="utf-8").read()

    assert config.EXPERT_THINKING_ENABLED is False
    assert config.JUDGE_THINKING_ENABLED is False
    assert '"think":      JUDGE_THINKING_ENABLED' in inference_source
    assert '_native_payload["think"] = False' in expert_source
    assert "expert returned no public answer content" in expert_source


def test_ollama_public_answer_excludes_private_thinking_and_keeps_judge_usage():
    synthesis_source = open(
        "graph/synthesis.py",
        encoding="utf-8",
    ).read()

    assert inference._ollama_answer_content(
        {
            "message": {
                "content": "",
                "thinking": "private chain of thought",
            }
        }
    ) == ""
    usage_capture = synthesis_source.index(
        "merger_usage = _extract_usage(res)"
    )
    content_wrapper = synthesis_source.index(
        "res = _StrResult(_judge_raw)"
    )
    assert usage_capture < content_wrapper

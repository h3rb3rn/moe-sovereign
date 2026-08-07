"""Deterministic IANA timezone, DST and structured evidence contracts."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from mcp_server.server import (
    InvokeRequest,
    build_tools_catalog,
    execute_tool,
    time_facts,
    timezone_convert,
)
from services.pipeline.contracts import (
    build_direct_precision_plan,
    build_precision_preflight,
    canonical_json_hash,
    detect_required_precision_intents,
    is_fully_covered_precision_request,
)
from services.precision_response import (
    bind_precision_response,
    build_precision_fact_slots,
    render_direct_precision_response,
)


def _facts(raw: str) -> dict:
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def test_time_facts_converts_explicit_utc_to_iana_with_localized_calendar():
    facts = _facts(time_facts("2026-07-29T12:00:00Z", "Europe/Berlin", "de"))

    assert facts["utc_instant"] == "2026-07-29T12:00:00+00:00"
    assert facts["local_datetime"] == "2026-07-29T14:00:00+02:00"
    assert facts["utc_offset"] == "+02:00"
    assert facts["utc_offset_seconds"] == 7200
    assert facts["is_dst"] is True
    assert facts["weekday_name"] == "Mittwoch"
    assert facts["as_of"] == facts["utc_instant"]


def test_time_facts_preserves_iso_week_year_and_leap_day():
    leap = _facts(time_facts("2024-02-29T23:30:00Z", "UTC", "en"))
    boundary = _facts(time_facts("2021-01-01T00:00:00Z", "UTC", "en"))

    assert leap["date"] == "2024-02-29"
    assert leap["weekday_name"] == "Thursday"
    assert boundary["iso_week"] == 53
    assert boundary["iso_week_year"] == 2020


@pytest.mark.parametrize(
    "instant",
    ["now", "2026-07-29T12:00:00", "2026-07-29", "2201-01-01T00:00:00Z"],
)
def test_time_facts_rejects_implicit_or_out_of_contract_clock_values(instant):
    with pytest.raises(ValueError):
        time_facts(instant, "Europe/Berlin", "de")


def test_timezone_convert_requires_fold_for_repeated_local_time():
    with pytest.raises(ValueError, match="ambiguous_local_time_fold_required"):
        timezone_convert(
            "2026-10-25T02:30:00", "Europe/Berlin", "UTC", None, "de"
        )

    first = _facts(timezone_convert(
        "2026-10-25T02:30:00", "Europe/Berlin", "UTC", 0, "de"
    ))
    second = _facts(timezone_convert(
        "2026-10-25T02:30:00", "Europe/Berlin", "UTC", 1, "de"
    ))

    assert first["ambiguous"] is True
    assert first["source_utc_offset"] == "+02:00"
    assert first["utc_instant"] == "2026-10-25T00:30:00+00:00"
    assert second["source_utc_offset"] == "+01:00"
    assert second["utc_instant"] == "2026-10-25T01:30:00+00:00"


def test_timezone_convert_rejects_spring_forward_gap():
    with pytest.raises(ValueError, match="nonexistent_local_time_gap"):
        timezone_convert(
            "2026-03-29T02:30:00", "Europe/Berlin", "UTC", None, "de"
        )


def test_timezone_convert_utc_to_iana_is_exact_and_unambiguous():
    facts = _facts(timezone_convert(
        "2026-07-29T12:00:00", "UTC", "Europe/Berlin", None, "en"
    ))

    assert facts["ambiguous"] is False
    assert facts["source_datetime"] == "2026-07-29T12:00:00+00:00"
    assert facts["target_datetime"] == "2026-07-29T14:00:00+02:00"
    assert facts["target_is_dst"] is True


@pytest.mark.parametrize(
    "zone",
    ["Europe/Does_Not_Exist", "../etc/passwd", "/usr/share/zoneinfo/UTC"],
)
def test_timezone_contracts_reject_unknown_or_path_like_zones(zone):
    with pytest.raises(ValueError):
        time_facts("2026-07-29T12:00:00Z", zone, "en")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args,expected_field,expected_value",
    [
        (
            "time_facts",
            {
                "instant": "2026-07-29T12:00:00Z",
                "timezone_name": "Europe/Berlin",
                "locale": "de",
            },
            "utc_offset",
            "+02:00",
        ),
        (
            "timezone_convert",
            {
                "local_datetime": "2026-10-25T02:30:00",
                "from_timezone": "Europe/Berlin",
                "to_timezone": "UTC",
                "fold": 1,
                "locale": "de",
            },
            "utc_instant",
            "2026-10-25T01:30:00+00:00",
        ),
    ],
)
async def test_time_invoke_contract_is_typed_versioned_and_source_bound(
    tool, args, expected_field, expected_value
):
    response = await execute_tool(InvokeRequest(tool=tool, args=args))

    assert "error" not in response
    structured = response["structured_result"]
    assert structured["contract_version"] == "1.0.0"
    assert len(structured["contract_hash"]) == 64
    assert len(structured["result_hash"]) == 64
    assert structured["facts"][expected_field] == expected_value
    assert structured["source"]["name"] == "tzdata"
    assert structured["source"]["version"] == structured["facts"]["tzdata_version"]
    assert structured["source"]["as_of"] == structured["facts"]["utc_instant"]


@pytest.mark.asyncio
async def test_time_invoke_returns_typed_errors_for_gap_fold_and_schema():
    gap = await execute_tool(InvokeRequest(
        tool="timezone_convert",
        args={
            "local_datetime": "2026-03-29T02:30:00",
            "from_timezone": "Europe/Berlin",
            "to_timezone": "UTC",
        },
    ))
    fold = await execute_tool(InvokeRequest(
        tool="timezone_convert",
        args={
            "local_datetime": "2026-10-25T02:30:00",
            "from_timezone": "Europe/Berlin",
            "to_timezone": "UTC",
        },
    ))
    naive = await execute_tool(InvokeRequest(
        tool="time_facts",
        args={"instant": "2026-07-29T12:00:00", "timezone_name": "UTC"},
    ))

    assert gap["error_code"] == "tool_execution_error"
    assert "nonexistent_local_time_gap" in gap["error"]
    assert fold["error_code"] == "tool_execution_error"
    assert "ambiguous_local_time_fold_required" in fold["error"]
    assert naive["error_code"] == "input_schema_invalid"


def test_time_tools_are_fully_registered_and_discoverable():
    tools = {item["name"]: item for item in build_tools_catalog()["tools"]}

    for name in ("time_facts", "timezone_convert"):
        tool = tools[name]
        assert tool["enabled"] is True
        assert tool["structured_result"] is True
        assert tool["determinism"] == "source_versioned"
        assert tool["source_policy"]["version"] == "2026.3"
        assert tool["access_kind"] == "read"
        assert tool["cache_policy"] == {"mode": "bypass"}


@pytest.mark.parametrize(
    "query,tool,expected",
    [
        (
            "Bestimme die Zeitfakten für 2026-07-29T12:00:00Z in Europe/Berlin.",
            "time_facts",
            {"instant": "2026-07-29T12:00:00Z", "timezone_name": "Europe/Berlin", "locale": "de"},
        ),
        (
            "Give time facts for 2026-07-29T12:00:00Z in America/New_York.",
            "time_facts",
            {"instant": "2026-07-29T12:00:00Z", "timezone_name": "America/New_York", "locale": "en"},
        ),
        (
            "Konvertiere 2026-10-25T02:30:00 von Europe/Berlin nach UTC mit Fold 1.",
            "timezone_convert",
            {
                "local_datetime": "2026-10-25T02:30:00",
                "from_timezone": "Europe/Berlin",
                "to_timezone": "UTC",
                "fold": 1,
                "locale": "de",
            },
        ),
        (
            "Convert 2026-07-29T12:00:00 from UTC to Europe/Berlin.",
            "timezone_convert",
            {
                "local_datetime": "2026-07-29T12:00:00",
                "from_timezone": "UTC",
                "to_timezone": "Europe/Berlin",
                "fold": None,
                "locale": "en",
            },
        ),
    ],
)
def test_time_intents_are_narrow_typed_and_directly_covered(query, tool, expected):
    intents = detect_required_precision_intents(query)
    plan = build_direct_precision_plan(query)

    assert [(item.tool, item.args) for item in intents] == [(tool, expected)]
    assert is_fully_covered_precision_request(query)
    assert len(plan) == 1
    assert plan[0]["mcp_tool"] == tool
    assert plan[0]["mcp_args"] == expected


def test_implicit_current_time_is_mandatory_but_schema_invalid_not_guessed():
    query = "Wie spät ist es jetzt in Europe/Berlin?"
    intent = detect_required_precision_intents(query)[0]

    assert intent.tool == "time_facts"
    assert intent.args["instant"] == "__implicit_clock__"
    assert is_fully_covered_precision_request(query)


def test_time_intent_inside_mixed_numbered_request_does_not_swallow_prose():
    query = (
        "1. Convert 2026-07-29T12:00:00 from UTC to Europe/Berlin.\n"
        "2. Explain why time zones are politically maintained."
    )

    intents = detect_required_precision_intents(query)

    assert len(intents) == 1
    assert intents[0].tool == "timezone_convert"
    assert not is_fully_covered_precision_request(query)


def test_time_renderer_uses_only_typed_evidence():
    query = "Bestimme die Zeitfakten für 2026-07-29T12:00:00Z in Europe/Berlin."
    schemas = {item["name"]: item for item in build_tools_catalog()["tools"]}
    plan = build_direct_precision_plan(query)
    preflight = build_precision_preflight(query, schemas)
    facts = _facts(time_facts("2026-07-29T12:00:00Z", "Europe/Berlin", "de"))
    contract_hash = schemas["time_facts"]["contract_hash"]
    state = {
        "input": query,
        "plan": plan,
        "agentic_iteration": 0,
        "mcp_evidence": [{
            "task_id": plan[0]["id"],
            "tool": "time_facts",
            "args": plan[0]["mcp_args"],
            "iteration": 0,
            "status": "completed",
            "contract_hash": contract_hash,
            "result_hash": canonical_json_hash(facts),
            "facts": facts,
        }],
        **preflight,
    }

    slots, _, errors = build_precision_fact_slots(state)

    assert errors == []
    assert render_direct_precision_response(slots) == (
        "Der Zeitpunkt 2026-07-29T12:00:00+00:00 entspricht in "
        "Europe/Berlin 2026-07-29T14:00:00+02:00 (UTC+02:00, CEST); "
        "Wochentag: Mittwoch."
    )

    duplicate = bind_precision_response({
        **state,
        "precision_fact_slots": slots,
        "final_response": (
            f"{slots[0]['marker']}\n"
            "Zusätzliche Zeitkonvertierung: 2026-07-29T15:00:00+02:00."
        ),
    })
    assert duplicate["precision_binding_status"] == "failed"
    assert "precision_binding_unbound_restatement" in (
        duplicate["precision_binding_errors"]
    )

from __future__ import annotations

import pytest

from services.deliberation.runtime import (
    DeliberationExecutionError,
    build_role_roster,
    compact_transcript,
    max_role_repetition,
    ngram_similarity,
    parse_moderator_decision,
    summarize_deliberation_telemetry,
)


def test_role_roster_prioritizes_distinct_specialist_domains():
    roles = build_role_roster(4, ["legal_advisor", "code_reviewer", "legal_advisor"])
    assert [role.role_id for role in roles[:2]] == [
        "domain_expert:legal_advisor",
        "domain_expert:code_reviewer",
    ]
    assert len(roles) == 4


def test_moderator_parser_accepts_fenced_json_and_rejects_extra_fields():
    decision = parse_moderator_decision(
        """```json
        {"status":"CONSENSUS","reason":"Covered","correction":"","direction":"",
         "convergence_score":0.9,"unresolved_conflicts":0,"missing_perspectives":[]}
        ```"""
    )
    assert decision.status == "CONSENSUS"

    with pytest.raises(DeliberationExecutionError, match="unexpected"):
        parse_moderator_decision(
            '{"status":"CONTINUE","reason":"x","convergence_score":0.2,'
            '"unresolved_conflicts":1,"missing_perspectives":[],"unexpected":true}'
        )


def test_moderator_parser_repairs_only_invalid_literal_backslashes():
    decision = parse_moderator_decision(
        '{"status":"CONTINUE","reason":"Check \\epsilon bound",'
        '"correction":"","direction":"Validate it",'
        '"convergence_score":0.4,"unresolved_conflicts":1,'
        '"missing_perspectives":[]}'
    )
    assert decision.status == "CONTINUE"
    assert decision.reason == "Check \\epsilon bound"

    with pytest.raises(DeliberationExecutionError, match="invalid JSON"):
        parse_moderator_decision(
            '{"status":"CONTINUE","reason":"still structurally broken",}'
        )


def test_compaction_keeps_recent_turns_and_bounds_output():
    turns = [
        {"round": index, "role": f"role-{index}", "content": "x" * 1200}
        for index in range(1, 10)
    ]
    compacted = compact_transcript(turns, max_chars=4000, recent_full_turns=2)
    assert len(compacted) <= 4030
    assert "Round 9" in compacted
    assert "older transcript omitted" in compacted


def test_repetition_is_role_scoped():
    repeated = "one two three four five six seven eight nine"
    turns = [
        {"role": "skeptic", "content": repeated},
        {"role": "proponent", "content": repeated},
        {"role": "skeptic", "content": repeated},
    ]
    assert ngram_similarity(repeated, repeated) == 1.0
    assert max_role_repetition(turns) == 1.0


def test_completed_request_telemetry_is_bounded_and_operational():
    summary = summarize_deliberation_telemetry(
        {
            "active": True,
            "selected_mode": "moderated",
            "activation_reason": "adaptive_complexity",
        },
        [
            {"event": "early_consensus", "round": 2},
            {
                "event": "moderated_debate_completed",
                "model_calls": 9,
                "reserve_agents_used": 1,
                "reserve_rounds_used": 1,
                "private_prompt": "must not be copied",
            },
        ],
    )

    assert summary == {
        "deliberation_active": True,
        "deliberation_mode": "moderated",
        "deliberation_reason": "adaptive_complexity",
        "deliberation_model_calls": 9,
        "deliberation_stop_reason": "early_consensus",
        "deliberation_reserve_agents_used": 1,
        "deliberation_reserve_rounds_used": 1,
    }

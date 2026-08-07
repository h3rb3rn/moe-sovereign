"""Agent-mode shortcuts must preserve mandatory precision contracts."""

from pathlib import Path

from graph.planner import _build_agent_mode_plan
from services.pipeline.contracts import build_precision_preflight


def _timezone_schema():
    return {
        "timezone_convert": {
            "required": ["local_datetime", "from_timezone", "to_timezone"],
            "args": {
                "local_datetime": {"type": "string"},
                "from_timezone": {"type": "string"},
                "to_timezone": {"type": "string"},
                "fold": {"type": ["integer", "null"], "default": None},
                "locale": {"type": "string", "default": "de"},
            },
            "contract_hash": "d" * 64,
        }
    }


def test_agent_mode_recovers_exact_mixed_precision_and_code_items():
    query = (
        "1. Konvertiere 2026-07-29T12:00:00 von UTC nach Europe/Berlin.\n"
        "2. Prüfe cursor.execute(\"SELECT * FROM t WHERE x = '\" + value + \"'\") "
        "auf SQL-Injection und gib eine sichere parametrisierte Zeile an."
    )
    schemas = _timezone_schema()
    preflight = build_precision_preflight(query, schemas)

    plan, reason = _build_agent_mode_plan(
        {"input": query, **preflight}, schemas
    )

    assert reason == "explicit_precision_recovery"
    assert [task["category"] for task in plan] == [
        "precision_tools", "code_reviewer"
    ]
    assert plan[0]["mcp_tool"] == "timezone_convert"
    assert plan[1]["task"].startswith("Prüfe cursor.execute")


def test_agent_mode_fallback_materializes_precision_instead_of_dropping_it():
    query = (
        "1. Konvertiere 2026-07-29T12:00:00 von UTC nach Europe/Berlin.\n"
        "2. Erkläre politische Zeitzonenentscheidungen."
    )
    schemas = _timezone_schema()
    preflight = build_precision_preflight(query, schemas)

    plan, reason = _build_agent_mode_plan(
        {"input": query, **preflight}, schemas
    )

    assert reason == "precision_preserving_fallback"
    assert plan[0]["category"] == "precision_tools"
    assert plan[0]["mcp_tool"] == "timezone_convert"
    assert plan[1]["category"] == "code_reviewer"


def test_agent_mode_without_precision_keeps_existing_pair():
    plan, reason = _build_agent_mode_plan(
        {"input": "Review this patch.", "required_precision_intents": []}, {}
    )

    assert reason == "default_code_pair"
    assert [task["category"] for task in plan] == [
        "code_reviewer", "technical_support"
    ]


def test_messages_facade_surfaces_quality_block_instead_of_empty_http_200():
    source = (
        Path(__file__).parents[1] / "services" / "pipeline" / "anthropic.py"
    ).read_text(encoding="utf-8")
    non_stream = source.index(
        'if result.get("quality_blocked"):',
        source.index("# Non-Streaming"),
    )
    response = source.index("status_code=422", non_stream)

    assert non_stream < response
    assert '"X-MoE-Quality": "blocked"' in source[non_stream:response + 500]

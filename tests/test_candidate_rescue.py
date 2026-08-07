"""Regression checks for budget-aware synthesis shortcuts."""

from unittest.mock import AsyncMock

import pytest

import graph.synthesis as synthesis


@pytest.mark.asyncio
async def test_thinking_skips_redundant_call_for_complete_precision_evidence(
    monkeypatch,
):
    record_stage = AsyncMock()
    monkeypatch.setattr(synthesis, "_record_stage", record_stage)

    result = await synthesis.thinking_node(
        {
            "response_id": "req-candidate",
            "mode": "default",
            "plan": [
                {
                    "id": "task-1",
                    "task": "calculate",
                    "category": "precision_tools",
                },
                {
                    "id": "task-2",
                    "task": "review",
                    "category": "code_reviewer",
                },
            ],
            "mcp_evidence": [
                {
                    "task_id": "task-1",
                    "status": "completed",
                    "result": "23",
                }
            ],
            "expert_results": ["[CODE_REVIEWER / model]: valid review"],
            "cache_hit": False,
            "guard_blocked": False,
            "skip_thinking": False,
        }
    )

    assert result == {"reasoning_trace": ""}
    record_stage.assert_awaited_once()
    assert record_stage.await_args.args[2] == "skipped_deterministic"


def test_merger_contains_explicit_minimum_budget_gate():
    source = open(synthesis.__file__, encoding="utf-8").read()
    assert "MIN_MERGER_REMAINING_SECONDS" in source
    assert "degraded_candidate" in source
    assert "incomplete_plan_tasks" in source

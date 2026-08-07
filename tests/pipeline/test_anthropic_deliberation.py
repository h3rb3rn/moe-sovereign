from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config import ORCHESTRATION_TIMEOUT
from services.pipeline.anthropic import _anthropic_moe_handler
from services.pipeline.cc_session import CCSession


@pytest.mark.asyncio
async def test_anthropic_facade_freezes_policy_deadline_and_output_budget():
    policy = {
        "schema_version": "1.0",
        "activation": "adaptive",
        "mode": "auto",
        "min_agents": 2,
        "initial_agent_cap": 6,
        "reserve_agents": 2,
        "absolute_max_agents": 8,
        "min_rounds": 1,
        "initial_round_cap": 3,
        "reserve_rounds": 2,
        "absolute_max_rounds": 5,
        "max_model_calls": 18,
        "moderator_interval": 1,
        "estimated_turn_seconds": 20.0,
        "synthesis_reserve_seconds": 30.0,
        "convergence_threshold": 0.82,
        "repetition_threshold": 0.78,
        "fallback": "standard",
    }
    session = CCSession(
        mode="moe_orchestrated",
        planner_cfg={"deliberation_policy": policy},
    )
    graph = SimpleNamespace(
        ainvoke=AsyncMock(return_value={
            "final_response": "ok",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "deliberation_capacity": {
                "active": True,
                "selected_mode": "moderated",
                "activation_reason": "adaptive_complexity",
            },
            "deliberation_events": [{
                "event": "moderated_debate_completed",
                "model_calls": 5,
                "reserve_agents_used": 0,
                "reserve_rounds_used": 0,
            }],
        })
    )
    started = time.monotonic()

    with (
        patch("services.pipeline.anthropic.state.app_graph", graph),
        patch(
            "services.pipeline.anthropic._resolve_skill_secure",
            new=AsyncMock(return_value="test"),
        ),
        patch(
            "services.pipeline.anthropic._apply_semantic_memory",
            new=AsyncMock(return_value=[]),
        ),
        patch("services.quality_probe.run_probe", new=AsyncMock()),
        patch(
            "services.pipeline.anthropic._deregister_active_request",
            new=AsyncMock(),
        ) as deregister,
    ):
        result = await _anthropic_moe_handler(
            {
                "model": "owned-template",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
                "max_tokens": 4096,
            },
            "msg-test",
            session,
        )
        await asyncio.sleep(0)

    assert result["content"][0]["text"] == "ok"
    invoke_state = graph.ainvoke.await_args.args[0]
    assert invoke_state["deliberation_policy"] == policy
    assert invoke_state["client_max_output_tokens"] == 4096
    assert invoke_state["request_deadline_monotonic"] >= started + ORCHESTRATION_TIMEOUT
    assert invoke_state["request_deadline_monotonic"] <= time.monotonic() + ORCHESTRATION_TIMEOUT
    assert deregister.await_args.args[1]["deliberation_model_calls"] == 5

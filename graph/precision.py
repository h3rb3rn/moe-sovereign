"""Graph nodes for deterministic rendering and post-mutation fact binding."""

from __future__ import annotations

import logging

from pipeline.state import AgentState
from services.helpers import _report
from services.precision_response import (
    bind_precision_response,
    build_precision_fact_slots,
    render_direct_precision_response,
)
from services.tracking import _record_stage


logger = logging.getLogger("MOE-SOVEREIGN")


def _telemetry_contract(state_: AgentState) -> str:
    required = [item for item in state_.get("required_precision_intents") or [] if isinstance(item, dict)]
    return str(required[0].get("tool") or "none") if len(required) == 1 else "multi" if required else "none"


async def precision_slot_prepare_node(state_: AgentState) -> dict:
    """Create opaque merger/critic slots after all worker evidence exists."""
    slots, projection, errors = build_precision_fact_slots(dict(state_))
    status = "not_required" if not state_.get("required_precision_intents") else (
        "prepared" if not errors else "failed"
    )
    await _record_stage(
        state_.get("response_id", ""),
        "precision_slots",
        status,
        errors[0] if errors else str(len(slots)),
    )
    if errors:
        logger.error("Precision slot preparation failed: %s", errors)
    return {
        "precision_fact_slots": slots,
        "precision_prompt_projection": projection,
        "precision_binding_status": status,
        "precision_binding_errors": errors,
    }


async def deterministic_precision_renderer_node(state_: AgentState) -> dict:
    """Render a fully covered precision request from typed MCP evidence."""
    slots, projection, errors = build_precision_fact_slots(dict(state_))
    if errors:
        await _record_stage(
            state_.get("response_id", ""),
            "precision_renderer",
            "failed",
            errors[0],
        )
        return {
            "final_response": "",
            "precision_fact_slots": slots,
            "precision_prompt_projection": projection,
            "precision_rendered_response": "",
            "precision_binding_status": "failed",
            "precision_binding_errors": errors,
        }
    try:
        response = render_direct_precision_response(slots)
    except ValueError as exc:
        error = str(exc)
        await _record_stage(
            state_.get("response_id", ""),
            "precision_renderer",
            "failed",
            error,
        )
        return {
            "final_response": "",
            "precision_fact_slots": slots,
            "precision_prompt_projection": projection,
            "precision_rendered_response": "",
            "precision_binding_status": "failed",
            "precision_binding_errors": [error],
        }
    await _report(f"⚙️ Deterministic precision response ({len(slots)} fact set(s))")
    await _record_stage(
        state_.get("response_id", ""),
        "precision_renderer",
        "done",
        str(len(slots)),
    )
    return {
        "final_response": response,
        "precision_fact_slots": slots,
        "precision_prompt_projection": projection,
        "precision_rendered_response": response,
        "precision_binding_status": "rendered",
        "precision_binding_errors": [],
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


async def precision_bind_node(state_: AgentState) -> dict:
    """Run after the final response mutation and seal it to evidence."""
    result = bind_precision_response(dict(state_))
    status = str(result.get("precision_binding_status") or "failed")
    errors = result.get("precision_binding_errors") or []
    await _record_stage(
        state_.get("response_id", ""),
        "precision_bind",
        status,
        str(errors[0]) if errors else "",
    )
    if status == "failed":
        logger.error("Precision response binding failed: %s", errors)
    from services.precision_telemetry import record_precision_event
    record_precision_event(
        "binding", "passed" if status == "bound" else "failed",
        tool=_telemetry_contract(state_),
        mode=str(state_.get("precision_contract_mode") or "enforce"),
    )
    return result

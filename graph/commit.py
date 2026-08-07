"""Post-quality graph routing and response persistence node."""

from __future__ import annotations

from services.response_commit import response_commit_node


def _route_quality_gate(state_: dict) -> str:
    """Only an explicit final pass may reach reusable persistence."""
    return (
        "response_commit"
        if state_.get("quality_gate_status") == "passed"
        else "end"
    )


__all__ = ["_route_quality_gate", "response_commit_node"]

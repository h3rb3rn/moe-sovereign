"""Bounded Prometheus telemetry for the precision-contract lifecycle."""

from __future__ import annotations

from metrics import PROM_PRECISION_EVENTS


_CONTRACTS = {
    "calendar_facts", "gcd_lcm", "unit_convert", "time_facts",
    "timezone_convert", "decimal_finance", "exact_probability",
    "structured_validate", "multi", "none",
}
_STAGES = {
    "intent", "route", "cache", "input_schema", "output_schema",
    "contract", "tool", "binding", "quality", "escape", "commit",
}
_OUTCOMES = {
    "detected", "shadow", "direct", "mixed", "bypassed", "passed",
    "blocked", "failed", "drift", "complete", "completed", "none", "skipped",
    "pending", "reused", "partial", "busy",
}


def precision_contract_label(tool: str) -> str:
    value = str(tool or "none")
    return value if value in _CONTRACTS else "none"


def record_precision_event(
    stage: str,
    outcome: str,
    *,
    tool: str = "none",
    mode: str = "enforce",
) -> None:
    """Record only allowlisted labels; never accept prompt/argument values."""
    safe_stage = stage if stage in _STAGES else "contract"
    safe_outcome = outcome if outcome in _OUTCOMES else "failed"
    safe_mode = mode if mode in {"shadow", "enforce"} else "shadow"
    PROM_PRECISION_EVENTS.labels(
        contract=precision_contract_label(tool),
        stage=safe_stage,
        outcome=safe_outcome,
        mode=safe_mode,
    ).inc()

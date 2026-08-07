"""
services/boundary_check.py — Deterministic boundary contracts between pipeline stages.

Checks that all required fields are present at each stage boundary before any
expensive LLM dispatch. A failed boundary check emits a CascadeEvent and the
call site should skip the LLM call rather than proceeding with a degraded input.

Designed to be <10ms per check — pure dict inspection, no I/O during check.
"""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

import yaml

logger = logging.getLogger("MOE-SOVEREIGN")

_CONTRACTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs",
    "boundary_contracts.yaml",
)

_CONTRACTS: Optional[dict] = None


class BoundaryConfigurationError(RuntimeError):
    """Raised when a mandatory stage-boundary contract is unavailable or invalid."""


def _validate_contracts(raw_contracts: Any) -> dict:
    """Validate the complete contract document before it can enter the cache."""
    if not isinstance(raw_contracts, dict):
        raise BoundaryConfigurationError(
            "boundary_contracts.yaml must contain a mapping at its root"
        )

    stages = raw_contracts.get("stages")
    if not isinstance(stages, dict) or not stages:
        raise BoundaryConfigurationError(
            "boundary_contracts.yaml must define a non-empty 'stages' mapping"
        )

    for stage, stage_cfg in stages.items():
        if not isinstance(stage, str) or not stage.strip():
            raise BoundaryConfigurationError(
                "boundary_contracts.yaml contains an invalid stage name"
            )
        if not isinstance(stage_cfg, dict):
            raise BoundaryConfigurationError(
                f"Boundary contract '{stage}' must be a mapping"
            )

        for field_group in ("required_fields", "optional_fields"):
            fields = stage_cfg.get(field_group, [])
            if not isinstance(fields, list) or not all(
                isinstance(field, str) and field.strip() for field in fields
            ):
                raise BoundaryConfigurationError(
                    f"Boundary contract '{stage}.{field_group}' "
                    "must be a list of non-empty strings"
                )

        on_violation = stage_cfg.get("on_violation")
        if not isinstance(on_violation, str) or not on_violation.strip():
            raise BoundaryConfigurationError(
                f"Boundary contract '{stage}.on_violation' must be a non-empty string"
            )

    return raw_contracts


def _load_contracts(*, force_reload: bool = False) -> dict:
    global _CONTRACTS
    if _CONTRACTS is not None and not force_reload:
        return _CONTRACTS

    try:
        with open(_CONTRACTS_PATH, "r", encoding="utf-8") as f:
            contracts = _validate_contracts(yaml.safe_load(f))
    except Exception as e:
        _CONTRACTS = None
        if isinstance(e, BoundaryConfigurationError):
            raise
        raise BoundaryConfigurationError(
            f"Failed to load mandatory boundary contracts from {_CONTRACTS_PATH}: {e}"
        ) from e

    _CONTRACTS = contracts
    logger.debug(
        "Boundary contracts loaded: %d stages",
        len(_CONTRACTS["stages"]),
    )
    return _CONTRACTS


def validate_boundary_contracts(*, force_reload: bool = False) -> int:
    """Load and validate the mandatory contract file for startup/readiness checks."""
    contracts = _load_contracts(force_reload=force_reload)
    return len(contracts["stages"])


def check_boundary(stage: str, payload: dict, request_id: str = "") -> List[str]:
    """Check required fields for a pipeline stage boundary.

    Returns a list of violation messages (empty = all good).
    The payload is the dict crossing the boundary (task dict or expert result dict).
    Contract/configuration errors raise so mandatory boundaries fail closed.
    Telemetry emitted for a violation remains best-effort and cannot mask the
    deterministic validation result.
    """
    contracts = _load_contracts()
    stage_cfg = contracts["stages"].get(stage)
    if stage_cfg is None:
        raise BoundaryConfigurationError(
            f"No mandatory boundary contract is configured for stage '{stage}'"
        )

    violations: List[str] = []
    if not isinstance(payload, dict):
        violations.append(
            f"Invalid payload type '{type(payload).__name__}' at stage '{stage}'; "
            "expected mapping"
        )
    else:
        for field in stage_cfg["required_fields"]:
            value = payload.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                violations.append(
                    f"Missing required field '{field}' at stage '{stage}'"
                )

    if violations:
        _emit_cascade(
            stage,
            violations,
            stage_cfg["on_violation"],
            request_id,
        )

    return violations


def _emit_cascade(
    stage: str,
    violations: List[str],
    cascade_type_str: str,
    request_id: str = "",
) -> None:
    """Emit a CascadeEvent and Decision Log entry for a boundary violation."""
    detail = f"Boundary '{stage}': " + "; ".join(violations)

    try:
        from services.cascade import CascadeType, CascadeEvent, emit_cascade
        ctype = CascadeType(cascade_type_str) if cascade_type_str in CascadeType._value2member_map_ else CascadeType.CONTEXT_GAP
        event = CascadeEvent(
            cascade_type=ctype,
            message=detail,
            replan_strategy="fix the missing fields before retrying",
            request_id=request_id,
        )
        emit_cascade(event, request_id=request_id)
        logger.warning("🚧 Boundary violation [%s → %s]: %s", stage, cascade_type_str, detail)
    except Exception as e:
        logger.debug("boundary_check: cascade emit failed: %s", e)

    try:
        from services.decision_log import log_decision, DecisionType
        log_decision(
            DecisionType.BOUNDARY_VIOLATION,
            request_id=request_id,
            rationale=detail,
            metadata={"stage": stage, "cascade_type": cascade_type_str},
        )
    except Exception as e:
        logger.debug("boundary_check: decision_log emit failed: %s", e)

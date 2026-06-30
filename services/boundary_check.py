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
from typing import List, Optional

import yaml

logger = logging.getLogger("MOE-SOVEREIGN")

_CONTRACTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs",
    "boundary_contracts.yaml",
)

_CONTRACTS: Optional[dict] = None


def _load_contracts() -> dict:
    global _CONTRACTS
    if _CONTRACTS is not None:
        return _CONTRACTS
    try:
        with open(_CONTRACTS_PATH, "r", encoding="utf-8") as f:
            _CONTRACTS = yaml.safe_load(f)
        logger.debug("Boundary contracts loaded: %d stages", len((_CONTRACTS or {}).get("stages", {})))
    except FileNotFoundError:
        logger.warning("boundary_contracts.yaml not found — boundary checks skipped")
        _CONTRACTS = {"stages": {}}
    except Exception as e:
        logger.error("Failed to load boundary_contracts.yaml: %s", e)
        _CONTRACTS = {"stages": {}}
    return _CONTRACTS


def check_boundary(stage: str, payload: dict) -> List[str]:
    """Check required fields for a pipeline stage boundary.

    Returns a list of violation messages (empty = all good).
    The payload is the dict crossing the boundary (task dict or expert result dict).
    Fail-open: exceptions return an empty list so the pipeline is never blocked.
    """
    try:
        contracts = _load_contracts()
        stage_cfg = (contracts.get("stages") or {}).get(stage)
        if stage_cfg is None:
            return []

        required = stage_cfg.get("required_fields") or []
        violations: List[str] = []

        for field in required:
            value = payload.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                violations.append(f"Missing required field '{field}' at stage '{stage}'")

        if violations:
            on_violation = stage_cfg.get("on_violation", "SPEC_GAP")
            _emit_cascade(stage, violations, on_violation)

        return violations

    except Exception as e:
        logger.debug("boundary_check: unexpected error (fail-open): %s", e)
        return []


def _emit_cascade(stage: str, violations: List[str], cascade_type_str: str) -> None:
    """Emit a CascadeEvent and Decision Log entry for a boundary violation."""
    detail = f"Boundary '{stage}': " + "; ".join(violations)

    try:
        from services.cascade import CascadeType, CascadeEvent
        ctype = CascadeType(cascade_type_str) if cascade_type_str in CascadeType._value2member_map_ else CascadeType.CONTEXT_GAP
        event = CascadeEvent(cascade_type=ctype, message=detail, replan_strategy="fix the missing fields before retrying")
        logger.warning("🚧 Boundary violation [%s → %s]: %s", stage, cascade_type_str, detail)
    except Exception as e:
        logger.debug("boundary_check: cascade emit failed: %s", e)

    try:
        from services.decision_log import log_decision, DecisionType
        log_decision(
            DecisionType.BOUNDARY_VIOLATION,
            request_id="",
            rationale=detail,
            metadata={"stage": stage, "cascade_type": cascade_type_str},
        )
    except Exception as e:
        logger.debug("boundary_check: decision_log emit failed: %s", e)

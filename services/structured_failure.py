"""services/structured_failure.py — Structured-Output Failure Recovery (TASK-30).

Classifies LLM-call failures into three semantic kinds and maps each to a set
of allowed recovery actions. Inspired by the ADHS project's structured-failure
and recovery modules, adapted for MoE-Sovereign's Python / LangGraph stack.

Usage in graph/synthesis.py or graph/planner.py:
    except (json.JSONDecodeError, Exception) as exc:
        failure = build_failure(exc, model=model, stage="synthesis", raw_text=raw)
        if failure.retry_round < MAX_RETRIES:
            # retry with resolve_retry_model(failure, RecoveryAction.RETRY_SAME)
        else:
            # emit SPEC_GAP cascade, stop
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class StructuredFailureKind(str, Enum):
    SCHEMA_OUTPUT     = "SCHEMA_OUTPUT"      # JSON parse / schema violation
    PROVIDER_TRANSPORT = "PROVIDER_TRANSPORT" # Timeout, connection reset, 4xx/5xx
    RUNTIME_ERROR     = "RUNTIME_ERROR"      # Everything else


class RecoveryAction(str, Enum):
    RETRY_SAME     = "RETRY_SAME"      # Retry with same model
    RETRY_FALLBACK = "RETRY_FALLBACK"  # Retry with fallback model (if configured)
    RETRY_SELECTED = "RETRY_SELECTED"  # Retry with a caller-supplied model
    STOP           = "STOP"            # Abort — emit SPEC_GAP cascade


_SCHEMA_PATTERN    = re.compile(
    r"json|schema|parse|structured|top.level|missing required|unexpected token|"
    r"expecting value|object has no attribute|key error",
    re.I,
)
_TRANSPORT_PATTERN = re.compile(
    r"timeout|ECONNRESET|ETIMEDOUT|rate.?limit|429|502|503|504|"
    r"connection refused|name or service not known|read timeout|connect timeout",
    re.I,
)

_STRUCTURED_FAILURE_FALLBACK = os.getenv("STRUCTURED_FAILURE_FALLBACK_MODEL", "")
_MAX_RETRIES = int(os.getenv("STRUCTURED_FAILURE_MAX_RETRIES", "2"))


@dataclass
class StructuredFailure:
    failure_kind:    StructuredFailureKind
    model:           str
    fallback_model:  str
    stage:           str
    message:         str
    raw_text:        str                        # truncated to 1600 chars
    retry_round:     int
    allowed_actions: List[RecoveryAction] = field(default_factory=list)

    @property
    def max_retries(self) -> int:
        return _MAX_RETRIES


def classify_failure(error: Exception, raw_text: str = "") -> StructuredFailureKind:
    """Classify *error* into a StructuredFailureKind by regex on message + raw_text."""
    combined = f"{type(error).__name__}: {error} {raw_text}"
    if _SCHEMA_PATTERN.search(combined):
        return StructuredFailureKind.SCHEMA_OUTPUT
    if _TRANSPORT_PATTERN.search(combined):
        return StructuredFailureKind.PROVIDER_TRANSPORT
    return StructuredFailureKind.RUNTIME_ERROR


def build_failure(
    error: Exception,
    model: str,
    stage: str,
    fallback_model: str = "",
    raw_text: str = "",
    retry_round: int = 0,
) -> StructuredFailure:
    """Classify *error* and build a StructuredFailure with appropriate allowed_actions."""
    kind = classify_failure(error, raw_text)
    fb = fallback_model or _STRUCTURED_FAILURE_FALLBACK

    if kind in (StructuredFailureKind.SCHEMA_OUTPUT, StructuredFailureKind.PROVIDER_TRANSPORT):
        if fb:
            actions = [
                RecoveryAction.RETRY_SAME,
                RecoveryAction.RETRY_FALLBACK,
                RecoveryAction.RETRY_SELECTED,
                RecoveryAction.STOP,
            ]
        else:
            actions = [
                RecoveryAction.RETRY_SAME,
                RecoveryAction.RETRY_SELECTED,
                RecoveryAction.STOP,
            ]
    else:
        actions = [RecoveryAction.RETRY_SAME, RecoveryAction.STOP]

    return StructuredFailure(
        failure_kind=kind,
        model=model,
        fallback_model=fb,
        stage=stage,
        message=str(error)[:400],
        raw_text=raw_text[:1600],
        retry_round=retry_round,
        allowed_actions=actions,
    )


def resolve_retry_model(
    failure: StructuredFailure,
    action: RecoveryAction,
    selected_model: str = "",
) -> str:
    """Return the model name to use for the next retry attempt.

    Raises ValueError for STOP (caller must handle this and emit a cascade).
    """
    if action == RecoveryAction.STOP:
        raise ValueError(f"StructuredFailure STOP at stage={failure.stage}: {failure.message}")
    if action == RecoveryAction.RETRY_FALLBACK:
        if not failure.fallback_model:
            raise ValueError("RETRY_FALLBACK requested but no fallback_model configured")
        return failure.fallback_model
    if action == RecoveryAction.RETRY_SELECTED:
        if not selected_model:
            raise ValueError("RETRY_SELECTED requires a non-empty selected_model")
        return selected_model
    return failure.model  # RETRY_SAME

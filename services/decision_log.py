"""
services/decision_log.py — Append-only Decision Log with mandatory rationale.

Every non-trivial runtime decision (constitution block, DoR fail, trust block,
replan, stuck loop, self-critique trigger) is persisted here with a mandatory
rationale field. This satisfies EU AI Act Art. 13 (transparency) and enables
post-mortem analysis without relying on ephemeral application logs.

Backend: Kafka topic moe.decisions (primary) + decision_log.jsonl (fallback).
Fail-open: errors in persistence never block the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN")

KAFKA_TOPIC_DECISIONS = "moe.decisions"
_FALLBACK_LOG_PATH = os.getenv("DECISION_LOG_PATH", "/app/logs/decision_log.jsonl")


class DecisionType(str, Enum):
    JUDGE_OVERRIDE          = "JUDGE_OVERRIDE"
    CONSTITUTION_BLOCK      = "CONSTITUTION_BLOCK"
    CONSTITUTION_WARN       = "CONSTITUTION_WARN"
    DOR_FAIL                = "DOR_FAIL"
    TRUST_BLOCK             = "TRUST_BLOCK"
    REPLAN                  = "REPLAN"
    STUCK_LOOP              = "STUCK_LOOP"
    SELF_CRITIQUE_TRIGGERED = "SELF_CRITIQUE_TRIGGERED"
    BOUNDARY_VIOLATION      = "BOUNDARY_VIOLATION"
    SCOPE_VIOLATION         = "SCOPE_VIOLATION"


def log_decision(
    decision_type: DecisionType,
    request_id: str,
    rationale: str,
    metadata: Optional[dict] = None,
) -> None:
    """Persist a decision entry synchronously to the fallback log and schedule Kafka emit.

    Args:
        decision_type: Type of decision from DecisionType enum.
        request_id:    Unique request identifier for correlation.
        rationale:     Non-empty explanation of WHY the decision was made.
        metadata:      Optional additional context (rule_id, task_id, score, …).

    Raises:
        ValueError: If rationale is empty — the rationale is the whole point.
    """
    if not rationale or not rationale.strip():
        raise ValueError("decision_log: rationale must not be empty")

    entry = {
        "decision_type": decision_type.value,
        "request_id":    request_id,
        "rationale":     rationale.strip(),
        "ts":            time.time(),
        **(metadata or {}),
    }

    # Primary: emit to Kafka asynchronously
    try:
        from services.kafka import _kafka_publish
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_DECISIONS, entry))
    except RuntimeError:
        # No running event loop (e.g. during sync test) — skip Kafka
        pass
    except Exception as e:
        logger.debug("decision_log: Kafka emit failed: %s", e)

    # Fallback: append to JSONL file (always runs, so log survives Kafka outage)
    try:
        log_dir = os.path.dirname(_FALLBACK_LOG_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(_FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("decision_log: fallback write failed: %s", e)

    logger.info(
        "📋 Decision[%s] req=%s: %s",
        entry["decision_type"], request_id, rationale[:120],
    )

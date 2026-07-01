"""
services/scope_guard.py — Deterministic Scope Guard (TASK-17).

Checks whether an expert category is within the declared domain for a task
before any LLM call. Block is deterministic (<10ms) and does not require
an LLM judgment.

Usage in expert.py:
    from services.scope_guard import check_scope
    violation = check_scope(task_item, expert_category)
    if violation:
        # emit SCOPE_DRIFT and skip this expert slot
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("MOE-SOVEREIGN")

# Categories that are always allowed in any task (cross-cutting concerns)
_UNIVERSAL_CATEGORIES = {"general", "precision_tools"}


@dataclass
class ScopeViolation:
    task_id:          str
    requested_domain: str
    allowed_domains:  List[str]
    message:          str


def check_scope(task: dict, expert_category: str) -> Optional[ScopeViolation]:
    """Return a ScopeViolation if expert_category is outside the task's declared scope.

    A task's allowed scope is determined by:
      1. task["allowed_domains"] if present (explicit whitelist)
      2. Falling back to [task["category"]] if absent

    Universal categories (general, precision_tools) always pass.
    """
    if expert_category in _UNIVERSAL_CATEGORIES:
        return None

    task_category    = task.get("category", "general")
    allowed_domains: List[str] = task.get("allowed_domains") or [task_category]

    if expert_category in allowed_domains:
        return None

    task_id = str(task.get("task", ""))[:60]
    msg = (
        f"Scope violation: expert_category='{expert_category}' "
        f"not in allowed_domains={allowed_domains} for task='{task_id}'"
    )
    logger.warning("🚫 %s", msg)

    # Emit SCOPE_DRIFT cascade event (fail-open: do not block on cascade errors)
    try:
        from services.cascade import CascadeEvent, CascadeType
        from services.decision_log import log_decision, DecisionType
        log_decision(
            DecisionType.SCOPE_VIOLATION,
            request_id="",
            rationale=msg,
            metadata={"expert_category": expert_category, "allowed_domains": allowed_domains},
        )
    except Exception as _e:
        logger.debug("scope_guard: decision log emit failed: %s", _e)

    return ScopeViolation(
        task_id=task_id,
        requested_domain=expert_category,
        allowed_domains=allowed_domains,
        message=msg,
    )

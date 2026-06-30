"""
services/dor_check.py — Deterministic Definition of Ready (DoR) checks.

Validates each planned task before expert dispatch without any LLM involvement.
A DoR violation means a pre-condition is not satisfied — dispatching the task
would waste an expert call or produce a degraded result.

All checks are fail-open: an exception in a check does not block the task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger("MOE-SOVEREIGN")

# Rough accumulated token threshold above which spawning more experts risks
# context overflow. This is a pre-flight warning, not a hard block.
_TOKEN_BUDGET_WARN = 60_000


@dataclass
class DoRViolation:
    task_id: str   # "{category}[{index}]" for tracing
    rule: str      # short rule name, e.g. "token_budget"
    message: str   # human-readable explanation


def check_dor(task: dict, state_: dict, task_index: int = 0) -> List[DoRViolation]:
    """Run deterministic pre-dispatch checks for a single planned task.

    Returns an empty list when the task is ready. Each returned entry is one
    violated pre-condition. Does not raise — all errors are caught internally.
    """
    violations: List[DoRViolation] = []
    tid = f"{task.get('category', '?')}[{task_index}]"

    try:
        # Rule 1: task description must be non-empty
        if not (task.get("task") or "").strip():
            violations.append(DoRViolation(tid, "empty_task", "Task description is empty"))

        # Rule 2: research tasks need a search_query
        if task.get("category") == "research" and not (task.get("search_query") or "").strip():
            violations.append(DoRViolation(
                tid, "missing_search_query",
                "Research task has no search_query — planner should provide one",
            ))

        # Rule 3: precision_tools tasks need both mcp_tool and mcp_args
        if task.get("category") == "precision_tools":
            if not task.get("mcp_tool"):
                violations.append(DoRViolation(
                    tid, "missing_mcp_tool",
                    "precision_tools task has no mcp_tool field",
                ))
            if not task.get("mcp_args"):
                violations.append(DoRViolation(
                    tid, "missing_mcp_args",
                    "precision_tools task has no mcp_args field",
                ))

        # Rule 4: accumulated token budget warning
        prompt_tokens = (state_.get("prompt_tokens") or 0)
        if prompt_tokens > _TOKEN_BUDGET_WARN:
            violations.append(DoRViolation(
                tid, "token_budget",
                f"Accumulated prompt tokens {prompt_tokens} exceed warning threshold {_TOKEN_BUDGET_WARN}",
            ))

        # Rule 5: depends_on — prior task result must be available
        depends_on = (task.get("depends_on") or "").strip()
        if depends_on:
            wm = state_.get("working_memory") or {}
            resolved = any(depends_on in k for k in wm)
            has_any_results = bool(state_.get("expert_results"))
            if not resolved and not has_any_results:
                violations.append(DoRViolation(
                    tid, "unresolved_dependency",
                    f"Task depends on '{depends_on}' but no prior result is available in working_memory",
                ))

    except Exception as e:
        logger.debug("DoR check for %s raised an unexpected error (skipped): %s", tid, e)

    return violations


def log_dor_result(
    task: dict,
    violations: List[DoRViolation],
    task_index: int = 0,
    request_id: str = "",
) -> None:
    """Write DoR check results to the application log and decision log."""
    tid = f"{task.get('category', '?')}[{task_index}]"
    if not violations:
        logger.debug("✅ DoR[%s]: all checks passed", tid)
        return
    for v in violations:
        logger.warning("⚠️ DoR[%s] %s: %s", v.task_id, v.rule, v.message)
    if violations and request_id:
        try:
            from services.decision_log import log_decision, DecisionType
            summary = "; ".join(f"{v.rule}: {v.message}" for v in violations)
            log_decision(
                DecisionType.DOR_FAIL,
                request_id,
                rationale=f"DoR pre-dispatch check failed for task {tid}: {summary}",
                metadata={"task_id": tid, "violations": [{"rule": v.rule, "message": v.message} for v in violations]},
            )
        except Exception as e:
            logger.debug("dor_check: decision_log emit failed: %s", e)

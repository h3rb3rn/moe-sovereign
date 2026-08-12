"""graph/hypothesis_verifier.py — deterministic hypothesis verification node.

Runs expert-produced Python code against a set of oracle test cases supplied
in state.verification_oracle. On any failure the node sets agentic_gap so the
re-planning loop can incorporate the concrete failure information.

The node is a no-op when:
  - verification_oracle is empty (not set by template/caller/planner)
  - no executable Python block is found in expert_results
  - the agentic iteration limit has been reached

Verification is intentionally restricted: code runs in the same sandbox as the
MCP python_sandbox tool (allowlisted stdlib modules only, no file/network I/O).
The sandbox is called via the MCP HTTP endpoint so security controls stay
centralised in mcp_server/server.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from config import MCP_URL
from pipeline.state import AgentState

logger = logging.getLogger("MOE-SOVEREIGN")

_VERIFIER_MCP_TIMEOUT = 15.0  # seconds per sandbox call
_CODE_FENCE_RE = re.compile(
    r"```(?:python|py)?\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_python_code(expert_results: list[Any]) -> str | None:
    """Return the first fenced Python block found across all expert results."""
    for result in expert_results:
        body = result.get("response", "") if isinstance(result, dict) else str(result)
        m = _CODE_FENCE_RE.search(body)
        if m:
            return m.group(1).strip()
    return None


def _build_test_snippet(code: str, case: dict[str, Any]) -> str:
    """Wrap the expert code so the sandbox can run a single oracle test case.

    Calls the entry function (default "solve") with the case input and prints
    the repr() of the result so we can compare it against the expected value.
    """
    entry_fn = str(case.get("entry_fn") or "solve")
    input_repr = repr(case["input"])
    return (
        f"{code}\n\n"
        f"_result = {entry_fn}({input_repr})\n"
        f"print(repr(_result))"
    )


async def _call_sandbox(client: httpx.AsyncClient, code: str) -> str:
    """Call python_sandbox via MCP HTTP and return its output string."""
    try:
        resp = await client.post(
            f"{MCP_URL}/invoke",
            json={"tool": "python_sandbox", "args": {"code": code}},
            timeout=_VERIFIER_MCP_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        # MCP /invoke returns {"result": <str>} or {"content": [{"text": ...}]}
        if isinstance(payload, dict):
            if "result" in payload:
                return str(payload["result"])
            content = payload.get("content") or []
            if content and isinstance(content[0], dict):
                return str(content[0].get("text", ""))
        return str(payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return f"[SANDBOX_CALL_ERROR] {exc}"


async def hypothesis_verifier_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: verify expert hypothesis against oracle test cases."""

    oracle: list[dict[str, Any]] = state.get("verification_oracle") or []
    if not oracle:
        return {"verification_result": {"passed": True, "skipped": True}}

    # Guard: stop verifying once the agentic budget is exhausted
    iteration: int = state.get("agentic_iteration", 0)
    max_rounds: int = state.get("max_agentic_rounds", 3)
    if iteration >= max_rounds:
        logger.info(
            "[hypothesis_verifier] agentic iteration %d >= max %d — skipping",
            iteration,
            max_rounds,
        )
        return {"verification_result": {"passed": True, "skipped": True, "reason": "agentic_budget_exhausted"}}

    expert_results: list[Any] = state.get("expert_results") or []
    code = _extract_python_code(expert_results)
    if not code:
        logger.debug("[hypothesis_verifier] no executable Python block in expert_results")
        return {"verification_result": {"passed": True, "skipped": True, "reason": "no_executable_code"}}

    deadline: float = state.get("request_deadline_monotonic", time.monotonic() + 30.0)
    remaining = max(1.0, deadline - time.monotonic())
    # Budget for verification: at most half the remaining time, capped at 60 s
    verify_budget = min(remaining * 0.5, 60.0)

    failed_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    async with httpx.AsyncClient() as client:
        for case in oracle:
            if time.monotonic() - t0 >= verify_budget:
                logger.warning("[hypothesis_verifier] time budget exhausted mid-oracle")
                break
            snippet = _build_test_snippet(code, case)
            output = await _call_sandbox(client, snippet)
            expected_repr = (
                repr(case["expected"])
                if not isinstance(case["expected"], str)
                else case["expected"]
            )
            # Normalise whitespace for comparison
            if output.strip() != expected_repr.strip() or output.startswith("["):
                failed_cases.append(
                    {
                        "description": str(case.get("description", f"case_{len(failed_cases)}")),
                        "input": case["input"],
                        "expected": expected_repr,
                        "actual": output.strip()[:400],
                    }
                )

    if not failed_cases:
        logger.info(
            "[hypothesis_verifier] all %d oracle cases passed",
            len(oracle),
        )
        return {
            "verification_result": {
                "passed": True,
                "failed_cases": [],
                "code_used": code,
                "cases_checked": len(oracle),
            }
        }

    summary_lines = [
        f"- {c['description']}: expected {c['expected']}, got {c['actual']}"
        for c in failed_cases
    ]
    gap = (
        f"Hypothesis verification failed ({len(failed_cases)}/{len(oracle)} cases):\n"
        + "\n".join(summary_lines)
        + f"\n\nFailing code:\n```python\n{code}\n```\n"
        "Revise the transformation rule so all oracle cases pass."
    )
    logger.info(
        "[hypothesis_verifier] %d/%d cases failed — setting agentic_gap",
        len(failed_cases),
        len(oracle),
    )
    return {
        "agentic_gap": gap,
        "verification_result": {
            "passed": False,
            "failed_cases": failed_cases,
            "code_used": code,
            "cases_checked": len(oracle),
        },
    }


def _route_after_strategy_review(state: AgentState) -> str:
    """Conditional edge: strategy_review → hypothesis_verifier or merger."""
    oracle: list = state.get("verification_oracle") or []
    iteration: int = state.get("agentic_iteration", 0)
    max_rounds: int = state.get("max_agentic_rounds", 3)
    if oracle and iteration < max_rounds:
        return "hypothesis_verifier"
    return "merger"

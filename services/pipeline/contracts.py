"""
services/pipeline/contracts.py — Typed contracts between pipeline stages.

parse_plan()/parse_verdict() are tolerant parsers with a single well-defined
result shape. Adoption is incremental: call sites keep their current fallback
behavior when parsing fails (flag MOE_STRICT_CONTRACTS=1 makes failures loud).
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger("MOE-SOVEREIGN")


@dataclass
class PlanTask:
    category: str = "general"
    instruction: str = ""


@dataclass
class PlannerPlan:
    tasks: list = field(default_factory=list)   # list[PlanTask]
    raw: str = ""
    valid: bool = False


def _first_json(text: str):
    dec = json.JSONDecoder()
    pos = text.find("{")
    while pos >= 0:
        try:
            obj, _ = dec.raw_decode(text, pos)
            return obj
        except (json.JSONDecodeError, ValueError):
            pos = text.find("{", pos + 1)
    pos = text.find("[")
    if pos >= 0:
        try:
            return json.loads(text[pos:])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def parse_plan(raw: str) -> PlannerPlan:
    plan = PlannerPlan(raw=raw or "")
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    obj = _first_json(cleaned)
    tasks = obj.get("tasks") if isinstance(obj, dict) else obj
    if isinstance(tasks, list):
        for t in tasks:
            if isinstance(t, dict) and (t.get("category") or t.get("instruction") or t.get("task")):
                plan.tasks.append(PlanTask(
                    category=str(t.get("category", "general")),
                    instruction=str(t.get("instruction") or t.get("task") or ""),
                ))
        plan.valid = bool(plan.tasks)
    if not plan.valid and os.getenv("MOE_STRICT_CONTRACTS", "0") == "1":
        logger.error("contracts: planner output failed schema parse (chars=%d)", len(raw or ""))
    return plan


def parse_verdict(raw: str) -> dict:
    """Judge verdict: {'winner': int|-1, 'reason': str, 'valid': bool}."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    obj = _first_json(cleaned)
    if isinstance(obj, dict) and "winner" in obj:
        try:
            return {"winner": int(obj["winner"]), "reason": str(obj.get("reason", "")), "valid": True}
        except (TypeError, ValueError):
            pass
    m = re.search(r"\b(?:answer|antwort|winner)\s*[:#]?\s*(\d)", cleaned, re.I)
    if m:
        return {"winner": int(m.group(1)), "reason": "", "valid": True}
    if os.getenv("MOE_STRICT_CONTRACTS", "0") == "1":
        logger.error("contracts: judge verdict failed schema parse (chars=%d)", len(raw or ""))
    return {"winner": -1, "reason": "", "valid": False}

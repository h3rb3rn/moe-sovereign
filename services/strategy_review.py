"""
services/strategy_review.py — Strategy Review Service (TASK-22).

Two-step abstraction-first quality layer:
  1. Abstractor (small/fast local model): reads expert content, produces a
     content-free abstract (problem class, approach, assumptions, uncertainties).
  2. Reviewer (potent open-weight by default; optionally a frontier endpoint):
     sees ONLY the abstract — never the domain content. Returns structural
     feedback and a confidence adjustment for the Trust-Score.

Core invariant: no domain content crosses the abstractor→reviewer boundary.
Frontier use is strictly opt-in via STRATEGY_REVIEWER_URL env var.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("MOE-SOVEREIGN")

_ABSTRACTOR_PROMPT = """\
You are a solution strategist. Analyze the expert outputs below and produce a \
CONTENT-FREE strategy abstract. Do not include any specific facts, names, numbers, \
or domain content in your output.

USER REQUEST (problem statement only, no answer needed):
{input_query}

PLAN TASKS (categories only):
{plan_summary}

EXPERT OUTPUTS (read to understand the approach, do not reproduce):
{expert_summary}

Respond in JSON with exactly these keys:
{{
  "problem_class": "<2-6 word category, e.g. 'comparative-analysis', 'root-cause-diagnosis'>",
  "solution_approach": "<1-2 sentences describing HOW the solution is structured, no content>",
  "assumptions": ["<assumption 1>", "<assumption 2>"],
  "uncertainties": ["<uncertainty 1>", "<uncertainty 2>"]
}}
Respond with JSON only. No markdown fences."""

_REVIEWER_PROMPT = """\
You are a solution architect reviewing a strategy abstract. You have no access \
to the original content — only the abstract below. Identify structural gaps and \
suggest alternative approaches if warranted.

PROBLEM CLASS: {problem_class}
SOLUTION APPROACH: {solution_approach}
ASSUMPTIONS: {assumptions}
UNCERTAINTIES: {uncertainties}

Respond in JSON with exactly these keys:
{{
  "structural_gaps": ["<gap 1>", "<gap 2>"],
  "alternative_approaches": ["<approach 1>"],
  "confidence_adjustment": <float between -0.2 and +0.2>,
  "summary": "<1 sentence structural assessment>"
}}
Respond with JSON only. No markdown fences."""


@dataclass
class StrategyAbstract:
    problem_class:    str
    solution_approach: str
    assumptions:      List[str] = field(default_factory=list)
    uncertainties:    List[str] = field(default_factory=list)


@dataclass
class StrategyFeedback:
    structural_gaps:        List[str] = field(default_factory=list)
    alternative_approaches: List[str] = field(default_factory=list)
    confidence_adjustment:  float = 0.0
    summary:                str = ""


def _parse_json_response(text: str, label: str) -> dict:
    """Extract JSON from LLM response, tolerating minor formatting issues."""
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.M)
    text = re.sub(r"\s*```$", "", text, flags=re.M)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try extracting first {...} block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    logger.debug("strategy_review: could not parse %s JSON: %s", label, text[:200])
    return {}


async def abstract_solution(
    expert_results: list,
    plan: list,
    input_query: str,
    abstractor_llm,
) -> StrategyAbstract:
    """Step 1: small/fast local model reads content and produces a content-free abstract."""
    non_empty = [r for r in expert_results if r and len(r.strip()) > 20]
    # Pass only the first 300 chars per expert to keep the abstractor prompt short
    expert_summary = "\n".join(f"- Expert {i+1}: {r[:300]}…" for i, r in enumerate(non_empty[:4]))
    plan_summary   = ", ".join(t.get("category", "general") for t in plan if isinstance(t, dict))

    prompt = _ABSTRACTOR_PROMPT.format(
        input_query=input_query[:400],
        plan_summary=plan_summary or "general",
        expert_summary=expert_summary or "(no expert results)",
    )
    try:
        resp = await abstractor_llm.ainvoke(prompt)
        raw  = resp.content if hasattr(resp, "content") else str(resp)
        data = _parse_json_response(raw, "abstractor")
        return StrategyAbstract(
            problem_class=    str(data.get("problem_class", "unknown"))[:80],
            solution_approach=str(data.get("solution_approach", ""))[:300],
            assumptions=      [str(a) for a in data.get("assumptions", [])[:5]],
            uncertainties=    [str(u) for u in data.get("uncertainties", [])[:5]],
        )
    except Exception as e:
        logger.warning("strategy_review: abstractor failed: %s", e)
        return StrategyAbstract(problem_class="unknown", solution_approach="")


async def review_strategy(abstract: StrategyAbstract, reviewer_llm) -> StrategyFeedback:
    """Step 2: reviewer sees ONLY the abstract — no domain content crosses this boundary."""
    if not abstract.solution_approach:
        return StrategyFeedback()

    prompt = _REVIEWER_PROMPT.format(
        problem_class=    abstract.problem_class,
        solution_approach=abstract.solution_approach,
        assumptions=      json.dumps(abstract.assumptions),
        uncertainties=    json.dumps(abstract.uncertainties),
    )
    try:
        resp = await reviewer_llm.ainvoke(prompt)
        raw  = resp.content if hasattr(resp, "content") else str(resp)
        data = _parse_json_response(raw, "reviewer")
        adj  = float(data.get("confidence_adjustment", 0.0))
        adj  = max(-0.2, min(0.2, adj))   # clamp to declared range
        return StrategyFeedback(
            structural_gaps=       [str(g) for g in data.get("structural_gaps", [])[:5]],
            alternative_approaches=[str(a) for a in data.get("alternative_approaches", [])[:3]],
            confidence_adjustment= adj,
            summary=               str(data.get("summary", ""))[:200],
        )
    except Exception as e:
        logger.warning("strategy_review: reviewer failed: %s", e)
        return StrategyFeedback()


def build_reviewer_llm(state_: dict):
    """Construct the reviewer LLM from env config.

    Priority:
      1. STRATEGY_REVIEWER_URL set → use that endpoint (frontier or dedicated node)
      2. Otherwise → use the session's judge LLM (local, sovereign default)
    """
    reviewer_url   = os.getenv("STRATEGY_REVIEWER_URL", "").strip()
    reviewer_model = os.getenv("STRATEGY_REVIEWER_MODEL", "").strip()
    reviewer_token = os.getenv("STRATEGY_REVIEWER_TOKEN", "").strip()

    if reviewer_url:
        # Explicit reviewer endpoint configured (may be frontier)
        from langchain_openai import ChatOpenAI
        model = reviewer_model or "gpt-4o-mini"
        logger.info("strategy_review: using configured reviewer endpoint (%s @ %s)", model, reviewer_url)
        return ChatOpenAI(
            model=model,
            base_url=f"{reviewer_url}/v1",
            api_key=reviewer_token or "sk-reviewer",
            temperature=0.1,
            max_tokens=512,
        )

    # Default: local judge LLM (sovereign, no external call)
    from graph.synthesis import _get_judge_llm
    logger.info("strategy_review: using local judge as reviewer (sovereign default)")
    return _get_judge_llm(state_)


def build_abstractor_llm(state_: dict):
    """Construct the abstractor LLM from env config.

    Priority:
      1. STRATEGY_ABSTRACTOR_MODEL set → use that model on the judge endpoint
      2. Otherwise → planner_llm (typically the lightest available model)
    """
    abstractor_model = os.getenv("STRATEGY_ABSTRACTOR_MODEL", "").strip()
    if abstractor_model:
        from langchain_openai import ChatOpenAI
        from config import JUDGE_URL, JUDGE_TOKEN
        return ChatOpenAI(
            model=abstractor_model,
            base_url=f"{JUDGE_URL}/v1",
            api_key=JUDGE_TOKEN or "sk-local",
            temperature=0.1,
            max_tokens=512,
        )
    from services.llm_instances import planner_llm
    return planner_llm

"""
graph/strategy_review_node.py — Strategy Review LangGraph Node (TASK-22).

Activated when STRATEGY_REVIEW_ENABLED=true AND
  (trust_verdict == PROCEED_WITH_ASSUMPTION OR cynefin_domain in COMPLEX/CHAOTIC).

Flow:
  expert_results → abstractor (local, sees content) → StrategyAbstract
                → reviewer  (local or frontier, sees NO content) → StrategyFeedback
                → strategy_feedback injected into merger context
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("MOE-SOVEREIGN")

_ENABLED_DOMAINS = {"COMPLEX", "CHAOTIC"}


def _should_activate(state_: dict) -> bool:
    if not os.getenv("STRATEGY_REVIEW_ENABLED", "").strip().lower() in ("1", "true", "yes"):
        return False
    trust_verdict  = (state_.get("trust_verdict") or "").upper()
    cynefin_domain = (state_.get("cynefin_domain") or "").upper()
    return trust_verdict == "PROCEED_WITH_ASSUMPTION" or cynefin_domain in _ENABLED_DOMAINS


async def strategy_review_node(state_):
    """LangGraph node: abstraction-first strategy review."""
    if not _should_activate(state_):
        return {}

    from graph.synthesis import _report
    from services.tracking import _record_stage
    await _report("🧠 Strategy Review: abstracting solution…")
    await _record_stage(state_.get("response_id", ""), "strategy_review", "started")

    expert_results = state_.get("expert_results") or []
    plan           = state_.get("plan") or []
    input_query    = state_.get("input") or ""

    try:
        from services.strategy_review import (
            abstract_solution, review_strategy,
            build_abstractor_llm, build_reviewer_llm,
            StrategyAbstract,
        )

        abstractor_llm = build_abstractor_llm(state_)
        reviewer_llm   = build_reviewer_llm(state_)

        # Step 1: local abstractor reads content, produces abstract
        abstract = await abstract_solution(expert_results, plan, input_query, abstractor_llm)
        logger.info(
            "📐 Strategy Abstract: class=%s approach='%s…' assumptions=%d uncertainties=%d",
            abstract.problem_class, abstract.solution_approach[:60],
            len(abstract.assumptions), len(abstract.uncertainties),
        )

        # Step 2: reviewer sees ONLY abstract — content boundary enforced here
        feedback = await review_strategy(abstract, reviewer_llm)
        logger.info(
            "📋 Strategy Feedback: gaps=%d adj=%.2f summary='%s'",
            len(feedback.structural_gaps), feedback.confidence_adjustment, feedback.summary[:80],
        )

        # Compose feedback string for merger injection
        parts = []
        if feedback.summary:
            parts.append(f"[Strategy Review] {feedback.summary}")
        if feedback.structural_gaps:
            parts.append("Structural gaps: " + "; ".join(feedback.structural_gaps))
        if feedback.alternative_approaches:
            parts.append("Consider: " + "; ".join(feedback.alternative_approaches))
        strategy_feedback = "\n".join(parts)

        await _report(f"📋 Strategy Review: {feedback.summary or 'complete'}")
        await _record_stage(state_.get("response_id", ""), "strategy_review", "done")

        # Adjust trust_score by reviewer's confidence delta (clamped to [0, 1])
        current_score = float(state_.get("trust_score") or 0.0)
        new_score = max(0.0, min(1.0, current_score + feedback.confidence_adjustment))

        return {
            "strategy_feedback": strategy_feedback,
            "trust_score":       new_score,
        }

    except Exception as e:
        logger.warning("strategy_review_node failed (fail-open): %s", e)
        return {}

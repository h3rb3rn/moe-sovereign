#!/usr/bin/env python3
"""MoE Sovereign — Hypothesis-driven Experiment Loop.

Iteratively attempts a GAIA question by generating and refining hypotheses.
Each epoch:
  1. Load question from GAIA validation set by task_id
  2. Send question + hypothesis prefix to MoE API
  3. Extract and evaluate answer against expected
  4. If wrong: meta-call to MoE generates a refined hypothesis for next epoch
  5. Exit on N consecutive correct answers or max_epochs reached

This loop tests whether the system can learn *how to approach* a question
across iterations, not just answer it in one shot.

Usage:
    MOE_API_KEY=moe-sk-... python3 benchmarks/experiment_loop.py \\
        --question-id e1fc63a2 --max-epochs 5

    # Target multiple questions sequentially
    python3 benchmarks/experiment_loop.py \\
        --question-id e1fc63a2 8e867cd7 --max-epochs 5 --consecutive-wins 2

Configuration (env vars):
    MOE_API_BASE        Orchestrator URL (default: http://localhost:8002)
    MOE_API_KEY         API key (required)
    MOE_TEMPLATE        Template to use (default: moe-reference-30b-balanced)
    HF_TOKEN            HuggingFace token for GAIA dataset access
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE  = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY   = os.environ.get("MOE_API_KEY", "")
TEMPLATE  = os.environ.get("MOE_TEMPLATE", "moe-reference-30b-balanced")
HF_TOKEN  = os.environ.get("HF_TOKEN", "")

RESULTS_DIR = Path(__file__).parent / "results" / "experiment_loop"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

REQUEST_TIMEOUT = 300  # seconds per API call


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExperimentHypothesis:
    """A testable approach for answering a specific question."""
    text: str                  # hypothesis / strategy text prepended to question
    epoch: int                 # which epoch generated this hypothesis
    source: str = "initial"    # "initial" | "meta_refinement"


@dataclass
class EpochResult:
    """Outcome of a single experiment epoch."""
    epoch: int
    hypothesis: str
    raw_response: str
    extracted_answer: str
    expected_answer: str
    correct: bool
    wall_clock_s: float
    working_memory_snapshot: dict = field(default_factory=dict)
    next_hypothesis: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RunResult:
    """Full run outcome for one question."""
    question_id: str
    question_text: str
    expected_answer: str
    template: str
    max_epochs: int
    consecutive_wins_required: int
    success: bool
    total_epochs: int
    final_answer: str
    epochs: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# GAIA dataset loading
# ---------------------------------------------------------------------------

def _load_gaia_question(task_id: str) -> dict:
    """Load a single GAIA question by task_id from HuggingFace dataset."""
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: 'datasets' package required. pip install datasets", file=sys.stderr)
        sys.exit(1)

    if not HF_TOKEN:
        print("ERROR: HF_TOKEN required to access the GAIA dataset.", file=sys.stderr)
        sys.exit(1)

    ds = load_dataset(
        "gaia-benchmark/GAIA",
        "2023_all",
        split="validation",
        token=HF_TOKEN,
        trust_remote_code=True,
    )
    for row in ds:
        if row.get("task_id", "").startswith(task_id):
            return {
                "task_id":         row["task_id"],
                "question":        row["Question"],
                "expected_answer": row["Final answer"],
                "level":           row.get("Level", "?"),
            }
    raise ValueError(f"Question {task_id!r} not found in GAIA validation set.")


# ---------------------------------------------------------------------------
# MoE API helpers
# ---------------------------------------------------------------------------

async def _call_moe(client: httpx.AsyncClient, messages: list[dict]) -> tuple[str, dict]:
    """Send messages to the MoE API. Returns (content, usage_dict)."""
    payload = {
        "model":       TEMPLATE,
        "messages":    messages,
        "temperature": 0.1,
        "stream":      False,
    }
    resp = await client.post(
        f"{API_BASE}/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    usage   = data.get("usage", {})
    return content, usage


def _extract_final_answer(response: str) -> str:
    """Extract the final answer from GAIA-style response.

    Tries FINAL ANSWER: prefix, then falls back to last non-empty line.
    """
    patterns = [
        r"FINAL\s+ANSWER\s*[:=]\s*(.+)",
        r"final answer\s*[:=]\s*(.+)",
        r"The answer is\s*[:=]?\s*(.+)",
    ]
    for pat in patterns:
        m = re.search(pat, response, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")

    # Fallback: last non-empty line
    lines = [l.strip() for l in response.strip().splitlines() if l.strip()]
    return lines[-1] if lines else response.strip()[:100]


def _answers_match(extracted: str, expected: str) -> bool:
    """Fuzzy answer comparison — normalise whitespace and case."""
    def _norm(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[,.]$", "", s)
        return s

    return _norm(extracted) == _norm(expected)


# ---------------------------------------------------------------------------
# Hypothesis generation (meta-call)
# ---------------------------------------------------------------------------

async def _generate_next_hypothesis(
    client: httpx.AsyncClient,
    question: str,
    expected: str,
    epoch_history: list[EpochResult],
) -> str:
    """Ask MoE to reflect on past failures and suggest a new approach."""
    history_summary = "\n".join(
        f"  Epoch {e.epoch}: hypothesis='{e.hypothesis[:80]}' "
        f"→ answer='{e.extracted_answer}' (expected: '{e.expected_answer}', "
        f"correct={e.correct})"
        for e in epoch_history[-3:]  # show last 3 epochs only
    )
    meta_prompt = (
        f"You are an expert problem-solving coach. A student has been trying to answer "
        f"the following question:\n\n"
        f"QUESTION: {question}\n\n"
        f"CORRECT ANSWER (hidden from student): {expected}\n\n"
        f"The student's recent attempts:\n{history_summary}\n\n"
        f"Suggest a SHORT (2-3 sentence) new reasoning strategy or approach that the "
        f"student should try next. Be concrete and specific. Do NOT reveal the answer. "
        f"Start with 'Approach:'"
    )
    content, _ = await _call_moe(client, [{"role": "user", "content": meta_prompt}])
    # Extract the approach text
    m = re.search(r"Approach:\s*(.+)", content, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:400]
    return content.strip()[:400]


# ---------------------------------------------------------------------------
# Single-question experiment loop
# ---------------------------------------------------------------------------

async def run_experiment(
    question_data: dict,
    max_epochs: int = 5,
    consecutive_wins: int = 2,
) -> RunResult:
    """Run the hypothesis-driven experiment loop for one GAIA question."""
    qid      = question_data["task_id"]
    question = question_data["question"]
    expected = question_data["expected_answer"]
    level    = question_data["level"]

    print(f"\n{'='*70}")
    print(f"Question ID: {qid}  (Level {level})")
    print(f"Q: {question[:100]}{'...' if len(question) > 100 else ''}")
    print(f"Expected: {expected}")
    print(f"Max epochs: {max_epochs} | Consecutive wins required: {consecutive_wins}")
    print(f"{'='*70}")

    epochs: list[EpochResult] = []
    current_hypothesis = ExperimentHypothesis(
        text="Think step by step before answering.",
        epoch=0,
        source="initial",
    )
    consecutive_correct = 0

    async with httpx.AsyncClient() as client:
        for epoch in range(1, max_epochs + 1):
            print(f"\n[Epoch {epoch}/{max_epochs}] Strategy: {current_hypothesis.text[:80]}")
            t0 = time.monotonic()

            messages = [
                {
                    "role": "system",
                    "content": (
                        f"You are a precise problem-solving assistant. "
                        f"Strategy hint: {current_hypothesis.text}\n"
                        f"Always end your response with 'FINAL ANSWER: <your answer>'"
                    ),
                },
                {"role": "user", "content": question},
            ]

            raw_response = ""
            extracted    = ""
            error_msg    = None
            try:
                raw_response, usage = await _call_moe(client, messages)
                extracted = _extract_final_answer(raw_response)
                print(f"  → Extracted: {extracted!r} (expected: {expected!r})")
            except Exception as exc:
                error_msg = str(exc)
                print(f"  ✗ API error: {exc}")

            wall_clock = round(time.monotonic() - t0, 2)
            correct    = _answers_match(extracted, expected) if not error_msg else False

            if correct:
                consecutive_correct += 1
                print(f"  ✓ CORRECT ({consecutive_correct}/{consecutive_wins} consecutive wins needed)")
            else:
                consecutive_correct = 0
                print(f"  ✗ Wrong answer")

            next_hyp_text = None
            if not correct and epoch < max_epochs:
                try:
                    next_hyp_text = await _generate_next_hypothesis(
                        client, question, expected, epochs + [
                            EpochResult(
                                epoch=epoch, hypothesis=current_hypothesis.text,
                                raw_response=raw_response, extracted_answer=extracted,
                                expected_answer=expected, correct=correct,
                                wall_clock_s=wall_clock,
                            )
                        ]
                    )
                    print(f"  → Next hypothesis: {next_hyp_text[:80]}")
                except Exception as exc:
                    print(f"  ⚠ Hypothesis generation failed: {exc}")

            epoch_result = EpochResult(
                epoch=epoch,
                hypothesis=current_hypothesis.text,
                raw_response=raw_response[:2000],
                extracted_answer=extracted,
                expected_answer=expected,
                correct=correct,
                wall_clock_s=wall_clock,
                working_memory_snapshot={},  # MVP: internal state not exposed via API
                next_hypothesis=next_hyp_text,
                error=error_msg,
            )
            epochs.append(epoch_result)

            if consecutive_correct >= consecutive_wins:
                print(f"\n🎉 SUCCESS after {epoch} epochs ({consecutive_wins} consecutive correct answers)")
                break

            if next_hyp_text:
                current_hypothesis = ExperimentHypothesis(
                    text=next_hyp_text, epoch=epoch, source="meta_refinement"
                )

    success = consecutive_correct >= consecutive_wins
    final_answer = epochs[-1].extracted_answer if epochs else ""
    return RunResult(
        question_id=qid,
        question_text=question[:300],
        expected_answer=expected,
        template=TEMPLATE,
        max_epochs=max_epochs,
        consecutive_wins_required=consecutive_wins,
        success=success,
        total_epochs=len(epochs),
        final_answer=final_answer,
        epochs=epochs,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="MoE Sovereign — Hypothesis-driven Experiment Loop for GAIA questions"
    )
    parser.add_argument(
        "--question-id", nargs="+", required=True,
        help="One or more GAIA task_id prefixes (e.g. e1fc63a2)",
    )
    parser.add_argument(
        "--max-epochs", type=int, default=5,
        help="Maximum iterations per question (default: 5)",
    )
    parser.add_argument(
        "--consecutive-wins", type=int, default=2,
        help="Consecutive correct answers required to declare success (default: 2)",
    )
    parser.add_argument(
        "--template", default=None,
        help="MoE template name (overrides MOE_TEMPLATE env var)",
    )
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: MOE_API_KEY required", file=sys.stderr)
        sys.exit(1)

    global TEMPLATE
    if args.template:
        TEMPLATE = args.template

    print(f"MoE Experiment Loop")
    print(f"  Template:  {TEMPLATE}")
    print(f"  Max epochs: {args.max_epochs}")
    print(f"  Win condition: {args.consecutive_wins} consecutive correct")
    print(f"  Questions: {args.question_id}")

    all_results = []
    for qid in args.question_id:
        try:
            question_data = _load_gaia_question(qid)
        except ValueError as exc:
            print(f"\nERROR: {exc}", file=sys.stderr)
            continue

        result = await run_experiment(
            question_data=question_data,
            max_epochs=args.max_epochs,
            consecutive_wins=args.consecutive_wins,
        )
        all_results.append(result)

        # Save per-question JSON log
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_path = RESULTS_DIR / f"exp_{qid[:8]}_{ts}.json"
        with open(log_path, "w") as f:
            json.dump(asdict(result), f, indent=2, ensure_ascii=False)
        print(f"\n  📄 Log saved: {log_path}")

    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY  ({len(all_results)} question(s))")
    for r in all_results:
        status = "✓ SUCCESS" if r.success else f"✗ FAILED  (best: {r.final_answer!r}, expected: {r.expected_answer!r})"
        print(f"  {r.question_id[:8]}  {status}  ({r.total_epochs} epoch(s))")


if __name__ == "__main__":
    asyncio.run(main())

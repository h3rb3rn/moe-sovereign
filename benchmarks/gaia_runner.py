"""
gaia_runner.py -- GAIA Benchmark Runner for MoE Sovereign

Runs the official GAIA (General AI Assistants) benchmark validation set
against the MoE Sovereign orchestrator. GAIA tests multi-step reasoning,
tool use, and real-world problem solving with 165 questions across 3 levels.

Level 1: Simple tool-use and factual recall (53 questions)
Level 2: Multi-step reasoning with tool chains (86 questions)
Level 3: Complex multi-tool, multi-step tasks (26 questions)

Configuration:
  HF_TOKEN         HuggingFace token with GAIA access (required)
  MOE_API_BASE     Orchestrator URL (default: http://localhost:8002)
  MOE_API_KEY      API key (required)
  MOE_TEMPLATE     Template to use (default: moe-reference-30b-balanced)
  GAIA_LEVELS      Comma-separated levels to run (default: 1,2,3)
  GAIA_MAX_PER_LEVEL  Max questions per level (default: 10 — use 0 for all)

Usage:
  HF_TOKEN=hf_... MOE_API_KEY=moe-sk-... python benchmarks/gaia_runner.py
  GAIA_LEVELS=1 GAIA_MAX_PER_LEVEL=5 python benchmarks/gaia_runner.py  # quick test
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass, asdict

import httpx

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

HF_TOKEN = os.environ.get("HF_TOKEN", "")
API_BASE = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY  = os.environ.get("MOE_API_KEY", "")
TEMPLATE = os.environ.get("MOE_TEMPLATE", "moe-reference-30b-balanced")
LEVELS   = [int(x) for x in os.environ.get("GAIA_LEVELS", "1,2,3").split(",")]
MAX_PER_LEVEL = int(os.environ.get("GAIA_MAX_PER_LEVEL", "10"))

RESULTS_DIR = pathlib.Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

if not API_KEY:
    print("ERROR: MOE_API_KEY required", file=sys.stderr)
    sys.exit(1)
if not HF_TOKEN:
    print("ERROR: HF_TOKEN required (gated dataset)", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

@dataclass
class GAIAResult:
    task_id: str
    level: int
    question: str
    expected_answer: str
    model_answer: str
    correct: bool
    wall_clock_s: float
    tokens_out: int
    error: str = ""


def load_gaia_dataset() -> list[dict]:
    """Load GAIA validation set from HuggingFace."""
    from huggingface_hub import login
    login(token=HF_TOKEN)
    from datasets import load_dataset
    ds = load_dataset("gaia-benchmark/GAIA", "2023_all", split="validation")
    return list(ds)


def normalize_answer(text: str) -> str:
    """Normalize an answer for comparison (lowercase, strip, remove punctuation)."""
    text = text.strip().lower()
    # Remove trailing punctuation
    text = re.sub(r"[.,;:!?\s]+$", "", text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def check_answer(model_output: str, expected: str) -> bool:
    """Check if the model's answer contains the expected answer.

    GAIA uses exact-match scoring, but we're slightly lenient: the expected
    answer must appear as a substring in the model's final response (after
    normalization). This handles cases where the model wraps the answer in
    explanatory text.
    """
    if not expected or not model_output:
        return False
    norm_expected = normalize_answer(expected)
    norm_output = normalize_answer(model_output)
    # Exact substring match
    if norm_expected in norm_output:
        return True
    # Try numeric comparison for numbers
    try:
        exp_num = float(re.sub(r"[^\d.\-]", "", norm_expected))
        # Find all numbers in output
        output_nums = re.findall(r"-?\d+\.?\d*", norm_output)
        for n in output_nums:
            if abs(float(n) - exp_num) < 0.01:
                return True
    except (ValueError, TypeError):
        pass
    return False


# --------------------------------------------------------------------------
# API call
# --------------------------------------------------------------------------

async def call_orchestrator(
    client: httpx.AsyncClient, question: str, timeout: int = 1800,
) -> dict:
    """Send a GAIA question to the orchestrator."""
    try:
        r = await client.post(
            f"{API_BASE}/v1/chat/completions",
            json={
                "model": TEMPLATE,
                "messages": [
                    {"role": "system", "content": (
                        "You are a helpful assistant answering factual questions. "
                        "Give ONLY the final answer — no explanation, no preamble. "
                        "If the answer is a number, give just the number. "
                        "If a name, give just the name. Be as concise as possible."
                    )},
                    {"role": "user", "content": question},
                ],
                "stream": False,
                "max_tokens": 500,
                "temperature": 0.1,
            },
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return {
            "status": r.status_code,
            "content": content,
            "tokens_out": usage.get("completion_tokens", 0),
            "error": "",
        }
    except Exception as e:
        return {"status": 0, "content": "", "tokens_out": 0, "error": str(e)[:300]}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

async def main() -> int:
    print("GAIA Benchmark Runner for MoE Sovereign", flush=True)
    print(f"  Template: {TEMPLATE}", flush=True)
    print(f"  Levels:   {LEVELS}", flush=True)
    print(f"  Max/level: {MAX_PER_LEVEL} (0=all)", flush=True)
    print(f"{'='*72}", flush=True)

    print("Loading GAIA dataset...", flush=True)
    all_questions = load_gaia_dataset()
    print(f"  Loaded {len(all_questions)} validation questions", flush=True)

    # Filter by level and limit
    questions = []
    for lvl in LEVELS:
        lvl_qs = [q for q in all_questions if q["Level"] == str(lvl)]
        if MAX_PER_LEVEL > 0:
            lvl_qs = lvl_qs[:MAX_PER_LEVEL]
        questions.extend(lvl_qs)
        print(f"  Level {lvl}: {len(lvl_qs)} questions selected", flush=True)

    print(f"\nTotal questions to run: {len(questions)}", flush=True)
    print(f"{'='*72}", flush=True)

    results: list[GAIAResult] = []

    async with httpx.AsyncClient() as client:
        for i, q in enumerate(questions, 1):
            level = int(q["Level"])
            question = q["Question"]
            expected = q.get("Final answer", "")
            task_id = q.get("task_id", f"q{i}")

            print(f"\n[{i}/{len(questions)}] L{level} {task_id}", flush=True)
            print(f"  Q: {question[:120]}...", flush=True)
            print(f"  Expected: {expected[:80]}", flush=True)

            t0 = time.perf_counter()
            res = await call_orchestrator(client, question)
            dt = time.perf_counter() - t0

            answer = res["content"]
            correct = check_answer(answer, expected)

            result = GAIAResult(
                task_id=task_id,
                level=level,
                question=question[:200],
                expected_answer=expected,
                model_answer=answer[:500],
                correct=correct,
                wall_clock_s=dt,
                tokens_out=res["tokens_out"],
                error=res["error"],
            )
            results.append(result)

            mark = "✓" if correct else "✗"
            print(f"  {mark} dt={dt:.1f}s  A: {answer[:100]}",
                  flush=True)

    # Compute scores
    by_level: dict[int, list[GAIAResult]] = {}
    for r in results:
        by_level.setdefault(r.level, []).append(r)

    print(f"\n{'='*72}", flush=True)
    print(f"GAIA Results Summary", flush=True)
    print(f"{'='*72}", flush=True)

    total_correct = sum(1 for r in results if r.correct)
    total = len(results)

    for lvl in sorted(by_level.keys()):
        lvl_results = by_level[lvl]
        lvl_correct = sum(1 for r in lvl_results if r.correct)
        pct = lvl_correct / len(lvl_results) * 100 if lvl_results else 0
        print(f"  Level {lvl}: {lvl_correct}/{len(lvl_results)} = {pct:.1f}%",
              flush=True)

    overall_pct = total_correct / total * 100 if total else 0
    print(f"\n  OVERALL: {total_correct}/{total} = {overall_pct:.1f}%", flush=True)
    print(f"  (GAIA leaderboard reference: GPT-5 Mini = 44.8%, "
          f"Qwen3 32B = 12.3%)", flush=True)

    # Save
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = {
        "benchmark": "GAIA",
        "template": TEMPLATE,
        "timestamp": ts,
        "levels_tested": LEVELS,
        "max_per_level": MAX_PER_LEVEL,
        "total_questions": total,
        "total_correct": total_correct,
        "overall_pct": round(overall_pct, 1),
        "by_level": {
            str(lvl): {
                "total": len(rs),
                "correct": sum(1 for r in rs if r.correct),
                "pct": round(sum(1 for r in rs if r.correct) / len(rs) * 100, 1) if rs else 0,
            }
            for lvl, rs in by_level.items()
        },
        "results": [asdict(r) for r in results],
    }
    path = RESULTS_DIR / f"gaia_{TEMPLATE}_{ts}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    (RESULTS_DIR / f"gaia_latest_{TEMPLATE}.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False)
    )
    print(f"\nSaved: {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

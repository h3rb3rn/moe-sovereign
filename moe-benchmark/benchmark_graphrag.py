#!/usr/bin/env python3
"""
moe-benchmark/benchmark_graphrag.py — GraphRAG Benchmark Harness (TASK-21).

Evaluates MoE-Sovereign's GraphRAG quality using:
- CypherBench: Property-graph Q&A (Cypher-style questions)
- GraphRAG-Bench: Multi-hop reasoning Q&A (ICLR'26)
- LLM-as-Judge scoring (0–5 scale vs. ground truth)

Usage:
    python benchmark_graphrag.py --fixtures data/fixtures/sample_100.jsonl
    python benchmark_graphrag.py --dataset graphrag-bench --difficulties fact_retrieval complex_reasoning

Output: results/graphrag_bench_YYYYMMDD_HHMMSS.json + summary table
"""

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_MOE_API_URL     = os.getenv("MOE_API_URL", "http://localhost:8000/v1/chat/completions")
_MOE_API_TOKEN   = os.getenv("MOE_API_TOKEN", "sk-moe-internal")
_JUDGE_MODEL     = os.getenv("BENCHMARK_JUDGE_MODEL", "moe-auto")
_CONCURRENCY     = int(os.getenv("BENCHMARK_CONCURRENCY", "3"))
_RESULTS_DIR     = Path("results")

_JUDGE_PROMPT = (
    "You are an objective evaluator. Score the ANSWER compared to the GROUND_TRUTH "
    "on a scale from 0 to 5:\n"
    "5 = Completely correct and complete\n"
    "4 = Mostly correct, minor omissions\n"
    "3 = Partially correct\n"
    "2 = Mostly incorrect but shows some understanding\n"
    "1 = Mostly wrong\n"
    "0 = Completely wrong or refuses to answer\n\n"
    "GROUND_TRUTH: {ground_truth}\n\n"
    "ANSWER: {answer}\n\n"
    "Respond with ONLY a single digit (0-5). No explanation."
)


def load_fixtures(path: str) -> list[dict]:
    items = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


async def query_moe(session: httpx.AsyncClient, question: str, enable_graphrag: bool) -> tuple[str, int, float]:
    """Query MoE API. Returns (answer, token_count, latency_ms)."""
    t0 = time.monotonic()
    payload = {
        "model": "moe-auto",
        "messages": [{"role": "user", "content": question}],
        "stream": False,
        "x_moe_enable_graphrag": enable_graphrag,
    }
    try:
        resp = await session.post(
            _MOE_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {_MOE_API_TOKEN}"},
            timeout=300,
        )
        data    = resp.json()
        choices = data.get("choices")
        answer  = choices[0].get("message", {}).get("content", "") if choices else ""
        tokens  = data.get("usage", {}).get("total_tokens", 0)
        latency = (time.monotonic() - t0) * 1000
        return answer, tokens, latency
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        logger.warning("MoE query failed: %s", e)
        return "", 0, latency


async def judge_score(session: httpx.AsyncClient, ground_truth: str, answer: str) -> float:
    """Use LLM-as-Judge to score answer against ground truth (0.0–5.0)."""
    if not answer:
        return 0.0
    prompt = _JUDGE_PROMPT.format(ground_truth=ground_truth, answer=answer)
    payload = {
        "model": _JUDGE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 5,
    }
    try:
        resp = await session.post(
            _MOE_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {_MOE_API_TOKEN}"},
            timeout=120,
        )
        choices = resp.json().get("choices")
        text = choices[0].get("message", {}).get("content", "0").strip() if choices else "0"
        return float(text[0]) if text and text[0].isdigit() else 0.0
    except Exception:
        return 0.0


async def benchmark_item(session: httpx.AsyncClient, item: dict, semaphore: asyncio.Semaphore) -> dict:
    """Run both zero-shot and GraphRAG queries for one Q&A item."""
    async with semaphore:
        question     = item["question"]
        ground_truth = item["answer"]

        ans_zs,  tok_zs,  lat_zs  = await query_moe(session, question, enable_graphrag=False)
        ans_rag, tok_rag, lat_rag = await query_moe(session, question, enable_graphrag=True)

        score_zs  = await judge_score(session, ground_truth, ans_zs)
        score_rag = await judge_score(session, ground_truth, ans_rag)

        logger.info("[%s] zero_shot=%.1f graphrag=%.1f lat_delta=%.0fms",
                    item.get("id", "?"), score_zs, score_rag, lat_rag - lat_zs)
        return {
            "id":              item.get("id"),
            "question":        question,
            "ground_truth":    ground_truth,
            "difficulty":      item.get("difficulty", ""),
            "category":        item.get("category", ""),
            "zero_shot_score": score_zs,
            "graphrag_score":  score_rag,
            "zero_shot_latency_ms":  lat_zs,
            "graphrag_latency_ms":   lat_rag,
            "zero_shot_tokens":  tok_zs,
            "graphrag_tokens":   tok_rag,
            "latency_delta_ms":  lat_rag - lat_zs,
            "score_delta":       score_rag - score_zs,
        }


def summarize(results: list[dict]) -> dict:
    if not results:
        return {}
    zs_scores  = [r["zero_shot_score"]  for r in results]
    rag_scores = [r["graphrag_score"]   for r in results]
    return {
        "n":                        len(results),
        "avg_zero_shot_score":      sum(zs_scores)  / len(zs_scores),
        "avg_graphrag_score":       sum(rag_scores) / len(rag_scores),
        "avg_score_delta":          (sum(rag_scores) - sum(zs_scores)) / len(results),
        "avg_latency_delta_ms":     sum(r["latency_delta_ms"] for r in results) / len(results),
        "graphrag_wins":            sum(1 for r in results if r["graphrag_score"] > r["zero_shot_score"]),
        "zero_shot_wins":           sum(1 for r in results if r["zero_shot_score"] > r["graphrag_score"]),
    }


async def main_async(fixtures: list[dict], difficulties: list[str] | None) -> list[dict]:
    if difficulties:
        fixtures = [f for f in fixtures if f.get("difficulty") in difficulties]
    logger.info("Running benchmark on %d items …", len(fixtures))

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient() as session:
        tasks   = [benchmark_item(session, item, semaphore) for item in fixtures]
        results = await asyncio.gather(*tasks)
    return list(results)


def main():
    parser = argparse.ArgumentParser(description="GraphRAG Benchmark Harness (TASK-21)")
    parser.add_argument("--fixtures",    default="data/fixtures/sample_100.jsonl")
    parser.add_argument("--difficulties", nargs="*",
                        choices=["fact_retrieval", "complex_reasoning", "summarization"],
                        help="Filter by difficulty levels")
    parser.add_argument("--output-dir",  default=str(_RESULTS_DIR))
    args = parser.parse_args()

    fixtures = load_fixtures(args.fixtures)
    results  = asyncio.run(main_async(fixtures, args.difficulties))
    summary  = summarize(results)

    ts        = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path  = Path(args.output_dir) / f"graphrag_bench_{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "timestamp": ts,
        "git_hash":  os.popen("git rev-parse --short HEAD 2>/dev/null").read().strip(),
        "summary":   summary,
        "results":   results,
    }
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info("Results written to %s", out_path)
    logger.info(
        "Summary: N=%d | ZeroShot=%.2f | GraphRAG=%.2f | Delta=+%.2f | GraphRAG wins=%d/%d",
        summary.get("n", 0),
        summary.get("avg_zero_shot_score", 0),
        summary.get("avg_graphrag_score", 0),
        summary.get("avg_score_delta", 0),
        summary.get("graphrag_wins", 0),
        summary.get("n", 0),
    )


if __name__ == "__main__":
    main()

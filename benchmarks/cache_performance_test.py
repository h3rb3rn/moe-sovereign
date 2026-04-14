#!/usr/bin/env python3
"""
Cache Performance A/B Test

Measures the actual impact of the 4-layer cache hierarchy by sending:
1. A cold query (no cache)
2. The exact same query (should be L1 cache hit)
3. A semantically similar query (should be L1 soft hit or L2 plan cache hit)
4. A different query (cache miss baseline)

Reports latency, tokens, and cache hit status for each.

Usage:
  MOE_API_KEY=moe-sk-... python3 benchmarks/cache_performance_test.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime

import httpx

API_BASE = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY = os.environ.get("MOE_API_KEY", "")
TEMPLATE = os.environ.get("MOE_TEMPLATE", "moe-reference-30b-balanced")

if not API_KEY:
    print("ERROR: MOE_API_KEY required.", file=sys.stderr)
    sys.exit(1)

QUERIES = [
    {
        "id": "cold",
        "label": "Cold query (first time, no cache)",
        "query": "What are the main differences between TCP and UDP protocols? "
                 "Explain the trade-offs for real-time applications.",
    },
    {
        "id": "exact_repeat",
        "label": "Exact repeat (should be L1 cache hit)",
        "query": "What are the main differences between TCP and UDP protocols? "
                 "Explain the trade-offs for real-time applications.",
    },
    {
        "id": "semantic_similar",
        "label": "Semantically similar (L1 soft hit or L2 plan cache)",
        "query": "Compare TCP vs UDP — which is better for streaming and gaming?",
    },
    {
        "id": "different",
        "label": "Different topic (cache miss baseline)",
        "query": "Explain how photosynthesis works in C4 plants versus C3 plants.",
    },
]


async def send_query(query: str) -> dict:
    """Send a query and measure response time + token usage."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": TEMPLATE,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": query}],
    }

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            resp = await client.post(
                f"{API_BASE}/v1/chat/completions",
                headers=headers,
                json=body,
            )
            dt = time.time() - t0
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "latency_s": dt}

            data = resp.json()
            usage = data.get("usage", {})
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            return {
                "latency_s": round(dt, 1),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
                "response_length": len(content),
                "response_preview": content[:150],
            }
    except Exception as e:
        return {"error": str(e), "latency_s": round(time.time() - t0, 1)}


async def main():
    print("Cache Performance A/B Test")
    print(f"  API:      {API_BASE}")
    print(f"  Template: {TEMPLATE}")
    print(f"  Queries:  {len(QUERIES)}")
    print("=" * 72)

    results = []
    for i, q in enumerate(QUERIES, 1):
        print(f"\n[{i}/{len(QUERIES)}] {q['label']}")
        print(f"  Query: {q['query'][:80]}...")

        result = await send_query(q["query"])
        result["id"] = q["id"]
        result["label"] = q["label"]
        results.append(result)

        if "error" in result:
            print(f"  ERROR: {result['error']} ({result['latency_s']}s)")
        else:
            print(f"  Latency: {result['latency_s']}s")
            print(f"  Tokens:  {result['total_tokens']} (prompt={result['prompt_tokens']}, completion={result['completion_tokens']})")
            print(f"  Response: {result['response_length']} chars")

        # Small pause between queries
        if i < len(QUERIES):
            await asyncio.sleep(2)

    # Summary
    print(f"\n{'=' * 72}")
    print("Cache Performance Summary")
    print(f"{'=' * 72}")
    print(f"{'Test':<40} {'Latency':>10} {'Tokens':>8} {'Chars':>7}")
    print(f"{'-' * 40} {'-' * 10} {'-' * 8} {'-' * 7}")

    cold_latency = None
    for r in results:
        latency = f"{r['latency_s']}s" if 'latency_s' in r else "ERR"
        tokens = r.get('total_tokens', 0)
        chars = r.get('response_length', 0)
        print(f"{r['label']:<40} {latency:>10} {tokens:>8} {chars:>7}")

        if r['id'] == 'cold' and 'error' not in r:
            cold_latency = r['latency_s']

    if cold_latency:
        print(f"\n--- Cache Impact ---")
        for r in results:
            if r['id'] != 'cold' and 'error' not in r:
                delta = ((r['latency_s'] - cold_latency) / cold_latency) * 100
                print(f"  {r['label']}: {delta:+.0f}% vs cold")

    # Save results
    out = {
        "timestamp": datetime.utcnow().isoformat(),
        "template": TEMPLATE,
        "results": results,
    }
    out_path = "benchmarks/results/cache_performance_test.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

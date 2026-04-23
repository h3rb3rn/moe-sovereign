"""
warm_search_cache.py — Pre-warms the SearXNG search cache for all GAIA questions.

Run this before a GAIA benchmark run to ensure deterministic, consistent search
results. Each question's key search terms are sent through the orchestrator's
/v1/chat/completions endpoint in research mode, warming the 24h Redis cache.

Subsequent benchmark runs within 24h will hit the warm cache and get identical
search results regardless of SearXNG state, making scores reproducible.

Usage:
    HF_TOKEN=hf_... MOE_API_KEY=moe-sk-... python benchmarks/warm_search_cache.py
"""
import asyncio
import hashlib
import json
import os
import sys
import re

import httpx

HF_TOKEN    = os.environ.get("HF_TOKEN", "")
API_BASE    = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY     = os.environ.get("MOE_API_KEY", "")
REDIS_URL   = os.environ.get("REDIS_URL", "redis://:@localhost:6379")
CONCURRENCY = int(os.environ.get("WARM_CONCURRENCY", "3"))  # parallel warm requests

if not HF_TOKEN:
    print("ERROR: HF_TOKEN required", file=sys.stderr)
    sys.exit(1)
if not API_KEY:
    print("ERROR: MOE_API_KEY required", file=sys.stderr)
    sys.exit(1)


def _extract_search_terms(question: str, file_name: str = "") -> list[str]:
    """Extract 2-3 targeted search queries from a GAIA question."""
    q = question.strip()
    queries = []

    # Extract quoted strings (exact titles, names)
    quoted = re.findall(r'"([^"]{10,80})"', q)
    queries.extend(quoted[:2])

    # Extract year + topic patterns
    year_m = re.search(r'\b(19|20)\d{2}\b', q)
    year = year_m.group(0) if year_m else ""

    # Extract proper nouns and key entities (capitalized multi-word phrases)
    proper = re.findall(r'\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})\b', q)
    if proper:
        queries.append(" ".join(proper[:3]) + (f" {year}" if year else ""))

    # Use first 120 chars of question as fallback
    if not queries:
        queries.append(q[:120])

    # Always add the raw question start as a catch-all
    queries.append(q[:100])

    return list(dict.fromkeys(queries))[:3]  # deduplicate, max 3


async def warm_question(client: httpx.AsyncClient, question: str,
                        task_id: str, level: int, file_name: str = "") -> dict:
    """Send a warm-up request for one question through the orchestrator."""
    queries = _extract_search_terms(question, file_name)
    print(f"  L{level} [{task_id[:8]}] Warming {len(queries)} queries: {queries[0][:60]}...")

    # Use a lightweight research-only call — we don't need an answer, just cache warm
    warmup_msg = (
        f"Research only (no answer needed): {question[:300]}\n"
        f"Search for: {' | '.join(queries)}"
    )
    try:
        r = await client.post(
            f"{API_BASE}/v1/chat/completions",
            json={
                "model":       "moe-aihub-free-gremium-deep-wcc",
                "messages":    [{"role": "user", "content": warmup_msg}],
                "stream":      False,
                "max_tokens":  50,
                "temperature": 0.0,
                "no_cache":    False,  # allow L0 cache — we want L1 search cache warmed
                "mode":        "research",
            },
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type":  "application/json",
            },
            timeout=120,
        )
        status = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:
        status = f"error: {e}"

    return {"task_id": task_id, "level": level, "status": status}


async def main():
    print("GAIA Search Cache Pre-Warmer")
    print("=" * 60)

    # Load GAIA dataset
    try:
        from huggingface_hub import hf_hub_download
        import pandas as pd
        path = hf_hub_download(
            repo_id="gaia-benchmark/GAIA",
            filename="2023/validation/metadata.parquet",
            repo_type="dataset",
            token=HF_TOKEN,
        )
        df = pd.read_parquet(path)
        print(f"Loaded {len(df)} GAIA questions")
    except Exception as e:
        print(f"ERROR loading GAIA dataset: {e}", file=sys.stderr)
        sys.exit(1)

    # Sample same questions as benchmark (10 per level)
    import random
    random.seed(42)  # deterministic sample
    questions = []
    for lvl in [1, 2, 3]:
        lvl_df = df[df["Level"] == str(lvl)]  # Level column is string in parquet
        sample = lvl_df.sample(min(10, len(lvl_df)), random_state=42)
        for _, row in sample.iterrows():
            questions.append({
                "task_id":  row["task_id"],
                "level":    lvl,
                "question": row["Question"],
                "file":     row.get("file_name", ""),
            })

    print(f"Warming cache for {len(questions)} questions (concurrency={CONCURRENCY})...")
    print("=" * 60)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient() as client:
        async def _bounded(q):
            async with sem:
                return await warm_question(client, q["question"],
                                           q["task_id"], q["level"], q["file"])

        results = await asyncio.gather(*[_bounded(q) for q in questions])

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nDone: {ok}/{len(results)} warmed successfully")
    if ok < len(results):
        for r in results:
            if r["status"] != "ok":
                print(f"  FAILED [{r['task_id'][:8]}] L{r['level']}: {r['status']}")


if __name__ == "__main__":
    asyncio.run(main())

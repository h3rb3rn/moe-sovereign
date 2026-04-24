"""
warm_search_cache.py — Pre-warms the SearXNG search cache for all GAIA questions.

Sends requests identical to gaia_runner.py so the orchestrator's planner generates
the same search queries as during the real benchmark run. The resulting SearXNG
search results are stored in the 24h Redis search cache.

Subsequent benchmark runs within 24h will hit the warm cache and get identical
search results, making scores fully reproducible.

Usage:
    HF_TOKEN=hf_... MOE_API_KEY=moe-sk-... python benchmarks/warm_search_cache.py
"""
import asyncio
import os
import sys

import httpx

HF_TOKEN    = os.environ.get("HF_TOKEN", "")
API_BASE    = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY     = os.environ.get("MOE_API_KEY", "")
TEMPLATE    = os.environ.get("MOE_TEMPLATE", "moe-aihub-free-gremium-deep-wcc")
LANGUAGE    = os.environ.get("GAIA_LANGUAGE", "en")
MAX_LEVEL   = int(os.environ.get("GAIA_MAX_PER_LEVEL", "10"))
CONCURRENCY = int(os.environ.get("WARM_CONCURRENCY", "2"))

if not HF_TOKEN:
    print("ERROR: HF_TOKEN required", file=sys.stderr)
    sys.exit(1)
if not API_KEY:
    print("ERROR: MOE_API_KEY required", file=sys.stderr)
    sys.exit(1)


async def warm_question(client: httpx.AsyncClient, question: dict, idx: int, total: int) -> dict:
    """Send a warm-up request using the exact same format as gaia_runner.py.

    Uses the actual benchmark system prompt and question format so the planner
    generates identical search queries — warming exactly the right cache entries.
    no_cache=False allows the L0 LLM cache to be hit (only the search cache is warmed).
    """
    q_text = question["Question"]
    task_id = question["task_id"]
    level = question["Level"]

    # Use same system prompt as gaia_runner.py
    system_msg = (
        "You are a helpful assistant answering factual questions. "
        "Give ONLY the final answer — no explanation, no preamble. "
        "If the answer is a number, give just the number. "
        "If a name, give just the name. Be as concise as possible. "
        "ALWAYS answer in English, regardless of the question language. "
        "Provide only the final answer value — no step-by-step explanation."
    )

    print(f"  [{idx}/{total}] L{level} [{task_id[:8]}] {q_text[:70]}...", flush=True)
    try:
        r = await client.post(
            f"{API_BASE}/v1/chat/completions",
            json={
                "model":       TEMPLATE,
                "messages":    [
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": q_text},
                ],
                "stream":      False,
                "max_tokens":  100,        # short — we only need the cache warmed
                "temperature": 0.0,
                "no_cache":    False,      # allow LLM cache — we want SEARCH cache warmed
                "mode":        "research", # same as benchmark runner
            },
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type":  "application/json",
            },
            timeout=180,
        )
        status = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:
        status = f"error: {e}"

    return {"task_id": task_id, "level": level, "status": status}


async def main():
    print("GAIA Search Cache Pre-Warmer")
    print("=" * 60)

    # Load GAIA dataset — same method as gaia_runner.py
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
        print(f"Loaded {len(df)} GAIA questions", flush=True)
    except Exception as e:
        print(f"ERROR loading GAIA dataset: {e}", file=sys.stderr)
        sys.exit(1)

    # Mirror gaia_runner.py sampling: take first MAX_LEVEL per level (deterministic, no shuffle)
    questions = []
    for lvl in [1, 2, 3]:
        lvl_rows = df[df["Level"] == str(lvl)].to_dict(orient="records")
        questions.extend(lvl_rows[:MAX_LEVEL])
        print(f"  Level {lvl}: {min(MAX_LEVEL, len(lvl_rows))} questions", flush=True)

    print(f"\nWarming cache for {len(questions)} questions (concurrency={CONCURRENCY})...")
    print("=" * 60)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient() as client:
        async def _bounded(q, idx):
            async with sem:
                return await warm_question(client, q, idx, len(questions))

        results = await asyncio.gather(*[_bounded(q, i+1) for i, q in enumerate(questions)])

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nDone: {ok}/{len(results)} warmed successfully", flush=True)
    if ok < len(results):
        for r in results:
            if r["status"] != "ok":
                print(f"  FAILED [{r['task_id'][:8]}] L{r['level']}: {r['status']}")


if __name__ == "__main__":
    asyncio.run(main())

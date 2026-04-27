"""
mrcr_lite_runner.py — MRCR-lite v2: Semantic Memory Recall Benchmark

Properly tests Tier-2 semantic memory by:
  1. Evicting the needle from the LLM hot-window (5 recent filler pairs after needle)
  2. Pre-populating ChromaDB with evicted turns under a unique session_id
  3. Sending only the hot window + recall question with that session_id
  4. Disabling the response cache (no_cache:true) for each call

This design ensures the memory_recall expert receives only recent context, and
must rely on ChromaDB retrieval to find the injected fact.

Environment variables:
  MOE_API_BASE       Orchestrator URL (default: http://localhost:8002)
  MOE_API_KEY        API key (required)
  MOE_TEMPLATE       Template to test (default: moe-memory-aihub-hybrid)
  MRCR_DATASET       Dataset path (default: datasets/mrcr_lite_v1.json)
  MRCR_MAX_DEPTH     Max needle depth to test (default: 20)
  CHROMA_HOST        ChromaDB host for pre-population (default: localhost)
  CHROMA_PORT        ChromaDB port (default: 8001)
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Optional

import httpx

# Set ChromaDB host before importing memory_retrieval so module-level vars pick it up
os.environ.setdefault("CHROMA_HOST", "localhost")
os.environ.setdefault("CHROMA_PORT", "8001")

# Import after env vars are set
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from memory_retrieval import ConversationMemoryStore

API_BASE        = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY         = os.environ.get("MOE_API_KEY", "")
TEMPLATE        = os.environ.get("MOE_TEMPLATE", "moe-memory-aihub-hybrid")
# Per-condition template override: allows true A/B comparison between a template
# with semantic memory and one without, using the same prompt suite.
TEMPLATE_WITH   = os.environ.get("MRCR_TEMPLATE_WITH",  TEMPLATE)
TEMPLATE_NO     = os.environ.get("MRCR_TEMPLATE_NO",    "moe-memory-aihub-nosm")
MAX_DEPTH       = int(os.environ.get("MRCR_MAX_DEPTH", "100"))
DATASET   = os.environ.get(
    "MRCR_DATASET",
    str(pathlib.Path(__file__).parent / "datasets" / "mrcr_lite_v1.json"),
)

# Hot-window size: must match orchestrator default_max_turns (4 turn pairs = 8 messages)
HOT_WINDOW_PAIRS = 4
# Recent filler pairs AFTER needle — must exceed HOT_WINDOW_PAIRS to force eviction
RECENT_FILLER_PAIRS = 5

RESULTS_DIR = pathlib.Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

if not API_KEY:
    print("ERROR: MOE_API_KEY required", file=sys.stderr)
    sys.exit(1)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class NeedleResult:
    needle_id:   str
    needle_type: str
    depth:       int
    condition:   str
    expected:    str
    response:    str
    score:       float
    latency_s:   float
    error:       str = ""


@dataclass
class BenchmarkResult:
    template:    str
    timestamp:   str
    needle_results: list[NeedleResult] = field(default_factory=list)

    def summary(self) -> dict:
        by_depth, by_cond, by_type = {}, {}, {}
        for r in self.needle_results:
            if r.error:
                continue
            by_depth.setdefault(r.depth, []).append(r.score)
            by_cond.setdefault(r.condition, []).append(r.score)
            by_type.setdefault(r.needle_type, []).append(r.score)

        def avg(lst):
            return round(sum(lst) / len(lst), 3) if lst else 0.0

        return {
            "by_depth":     {d: avg(s) for d, s in sorted(by_depth.items())},
            "by_condition": {c: avg(s) for c, s in sorted(by_cond.items())},
            "by_type":      {t: avg(s) for t, s in sorted(by_type.items())},
            "overall":      avg([r.score for r in self.needle_results if not r.error]),
        }


# ── Semantic Memory pre-population ───────────────────────────────────────────

_mem_store: Optional[ConversationMemoryStore] = None

def get_store() -> ConversationMemoryStore:
    global _mem_store
    if _mem_store is None:
        _mem_store = ConversationMemoryStore()
    return _mem_store


async def prepopulate_session(session_id: str, evicted_turns: list[dict]) -> int:
    """Store evicted conversation turns in ChromaDB under session_id.

    Called before the recall API request so semantic memory can retrieve
    the needle when the orchestrator processes the recall question.
    """
    store = get_store()
    return await store.store_turns(session_id, evicted_turns, base_turn_index=0)


# ── HTTP helper ───────────────────────────────────────────────────────────────

async def chat(
    client: httpx.AsyncClient,
    messages: list[dict],
    session_id: str,
    *,
    template: str = TEMPLATE,
    timeout: float = 600.0,
) -> tuple[str, float]:
    """Send a /v1/chat/completions request with session_id and no_cache.

    Returns (response_text, latency_s).
    """
    t0 = time.monotonic()
    resp = await client.post(
        f"{API_BASE}/v1/chat/completions",
        headers={
            "Authorization":   f"Bearer {API_KEY}",
            "Content-Type":    "application/json",
            "X-Session-Id":    session_id,
        },
        json={
            "model":    template,
            "messages": messages,
            "stream":   False,
            "no_cache": True,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    latency = time.monotonic() - t0
    content = resp.json()["choices"][0]["message"]["content"]
    return content, latency


# ── Conversation builder ──────────────────────────────────────────────────────

def build_conversation(
    needle: dict,
    filler_turns: list[dict],
    depth: int,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build a conversation and split into evicted turns and hot window.

    Structure:
      [pre-injection fillers × depth]  ← evicted (before needle)
      [needle injection]               ← evicted (needle itself)
      [recent fillers × RECENT_FILLER_PAIRS]  ← partial eviction + hot window

    With RECENT_FILLER_PAIRS > HOT_WINDOW_PAIRS, the needle is always evicted
    from the hot window and must be retrieved from semantic memory.

    Returns:
      (evicted_turns, hot_window, full_history)
    """
    fillers = (filler_turns * ((depth // len(filler_turns)) + 2))[:depth]
    inject_text = needle["inject_template"].format(value=needle["value"])

    history: list[dict] = []
    for f in fillers:
        history.append({"role": "user",      "content": f["user"]})
        history.append({"role": "assistant", "content": f["assistant"]})

    history.append({"role": "user",      "content": inject_text})
    history.append({"role": "assistant", "content": "Verstanden, ich merke mir diese Information."})

    recent = (filler_turns * ((RECENT_FILLER_PAIRS // len(filler_turns)) + 2))[:RECENT_FILLER_PAIRS]
    for f in recent:
        history.append({"role": "user",      "content": f["user"]})
        history.append({"role": "assistant", "content": f["assistant"]})

    # Hot window: last HOT_WINDOW_PAIRS pairs (= HOT_WINDOW_PAIRS * 2 messages)
    hot_size    = HOT_WINDOW_PAIRS * 2
    hot_window  = history[-hot_size:]
    evicted     = history[:-hot_size]

    return evicted, hot_window, history


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_response(response: str, expected: str, needle_type: str) -> float:
    """Score whether the model correctly recalled the needle value.

    Returns:
      1.0 — exact or clearly correct recall
      0.5 — partial recall (key component present but not complete)
      0.0 — miss
    """
    resp_lower = response.lower()
    exp_lower  = expected.lower()

    if needle_type == "number":
        digits_only = re.sub(r"[\s.,]", "", expected)
        if expected in response or digits_only in re.sub(r"[\s.,]", "", response):
            return 1.0
        return 0.0

    if needle_type == "technical":
        if expected in response:
            return 1.0
        host_match = re.search(r"([\w\-]+\.internal[:\d/]*)", expected)
        if host_match and host_match.group(1) in response:
            return 0.5
        return 0.0

    if needle_type == "date":
        if exp_lower in resp_lower:
            return 1.0
        year  = re.search(r"\d{4}", expected)
        day   = re.search(r"\b(\d{1,2})\b", expected)
        month_names = {
            "januar": "01", "februar": "02", "märz": "03", "april": "04",
            "mai": "05", "juni": "06", "juli": "07", "august": "08",
            "september": "09", "oktober": "10", "november": "11", "dezember": "12",
        }
        month_num = next((n for m, n in month_names.items() if m in exp_lower), None)
        year_ok  = year  and year.group()  in response
        day_ok   = day   and day.group(1)  in response
        month_ok = month_num and (month_num in response or
                   any(m in resp_lower for m in month_names if month_names[m] == month_num))
        if year_ok and day_ok and month_ok:
            return 1.0
        if year_ok and (day_ok or month_ok):
            return 0.5
        return 0.0

    if needle_type in ("name", "person"):
        if exp_lower in resp_lower:
            return 1.0
        tokens = [t for t in re.split(r"\s+", expected) if len(t) >= 4]
        matched = sum(1 for t in tokens if t.lower() in resp_lower)
        if matched >= len(tokens):
            return 1.0
        if matched >= 1:
            return 0.5
        return 0.0

    if exp_lower in resp_lower:
        return 1.0
    return 0.0


# ── Single test run ───────────────────────────────────────────────────────────

async def run_needle_test(
    client: httpx.AsyncClient,
    needle: dict,
    filler_turns: list[dict],
    depth: int,
    condition: str,
    template: str,
) -> NeedleResult:
    """Run one needle test:
    1. Build conversation → split into evicted + hot window
    2. Pre-populate ChromaDB with evicted turns (including needle)
    3. Send only hot window + recall question to orchestrator
    4. Score the response
    """
    session_id = uuid.uuid4().hex
    evicted, hot_window, _ = build_conversation(needle, filler_turns, depth)
    recall_q = needle["recall_question"]

    try:
        # A/B design:
        #   with_prepopulation    → ChromaDB pre-seeded; TEMPLATE_WITH used.
        #                           Semantic memory retrieves the needle.
        #   without_prepopulation → ChromaDB NOT seeded; TEMPLATE_NO used.
        #                           Needle is truly lost — baseline recall only.
        if "with_prepopulation" in condition:
            await prepopulate_session(session_id, evicted)
            used_template = TEMPLATE_WITH
        else:
            used_template = TEMPLATE_NO

        # Send only hot window + recall question; semantic memory provides the needle
        messages = hot_window + [{"role": "user", "content": recall_q}]
        response_text, latency = await chat(
            client, messages, session_id, template=used_template
        )
        score = score_response(response_text, needle["value"], needle["type"])
        return NeedleResult(
            needle_id=needle["id"], needle_type=needle["type"],
            depth=depth, condition=condition,
            expected=needle["value"], response=response_text[:500],
            score=score, latency_s=round(latency, 2),
        )
    except Exception as exc:
        err_msg = str(exc) or type(exc).__name__
        return NeedleResult(
            needle_id=needle["id"], needle_type=needle["type"],
            depth=depth, condition=condition,
            expected=needle["value"], response="",
            score=0.0, latency_s=0.0, error=err_msg[:120],
        )


# ── Main runner ───────────────────────────────────────────────────────────────

async def main() -> None:
    dataset      = json.loads(pathlib.Path(DATASET).read_text())
    needles      = dataset["needles"]
    filler_turns = dataset["filler_turns"]
    matrix       = dataset["test_matrix"]

    depths     = [d for d in matrix["depths"] if d <= MAX_DEPTH]
    conditions = matrix["conditions"]
    reps       = matrix.get("repetitions", 1)
    total      = len(needles) * len(depths) * len(conditions) * reps

    # Verify ChromaDB connectivity before starting
    try:
        store = get_store()
        store._ensure_connected()
        print(f"ChromaDB: connected at {os.environ['CHROMA_HOST']}:{os.environ['CHROMA_PORT']}")
    except Exception as e:
        print(f"ERROR: ChromaDB not reachable — {e}", file=sys.stderr)
        print("Set CHROMA_HOST and CHROMA_PORT to the ChromaDB instance.", file=sys.stderr)
        sys.exit(1)

    ts     = time.strftime("%Y%m%d-%H%M%S")
    result = BenchmarkResult(template=TEMPLATE, timestamp=ts)
    done   = 0

    print(f"MRCR-lite v2 — template: {TEMPLATE}")
    print(f"Needle eviction: {RECENT_FILLER_PAIRS} recent pairs (hot window: {HOT_WINDOW_PAIRS} pairs)")
    print(f"Needles: {len(needles)}, depths: {depths}, conditions: {conditions}, reps: {reps}")
    print(f"Total runs: {total}\n")

    # Warmup: use a SEPARATE client so a failed warmup connection does not
    # corrupt the pool used by the actual benchmark calls.
    print("Warmup call (priming remote models)…", flush=True)
    for _attempt in range(3):
        try:
            async with httpx.AsyncClient() as _wc:
                warmup_msgs = [{"role": "user", "content": "Antworte nur mit: READY"}]
                _wr, _ = await chat(_wc, warmup_msgs, uuid.uuid4().hex,
                                    template=TEMPLATE_WITH, timeout=300.0)
            print(f"Warmup done: {_wr[:30]}\n")
            break
        except Exception as _we:
            err_str = str(_we) or type(_we).__name__
            print(f"Warmup attempt {_attempt+1}/3 failed (non-fatal): {err_str}\n")

    async with httpx.AsyncClient() as client:
        for rep in range(reps):
            for depth in depths:
                for condition in conditions:
                    for needle in needles:
                        nr = await run_needle_test(
                            client, needle, filler_turns,
                            depth=depth, condition=condition, template=TEMPLATE,
                        )
                        result.needle_results.append(nr)
                        done += 1
                        status = f"✓ {nr.score:.1f}" if not nr.error else f"✗ {nr.error[:40]}"
                        cond_label = "WITH-pop" if "with_prep" in condition else "NO-pop "
                        print(
                            f"[{done:3}/{total}] {needle['id']} depth={depth:2} "
                            f"{cond_label} {status} ({nr.latency_s:.1f}s)"
                        )

    # Save results
    out_path = RESULTS_DIR / f"mrcr_lite_v2_{TEMPLATE}_{ts}.json"
    out_path.write_text(json.dumps(
        {"meta": asdict(result), "results": [asdict(r) for r in result.needle_results]},
        indent=2, ensure_ascii=False,
    ))

    # Print summary
    summary = result.summary()
    print("\n═══ MRCR-lite v2 Results ═══")
    print(f"Template:  {TEMPLATE}")
    print(f"Overall:   {summary['overall']:.3f}")
    print("\nBy depth:")
    for depth, score in summary["by_depth"].items():
        bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        print(f"  depth={depth:3}: {bar} {score:.3f}")
    print("\nBy condition (with / without semantic memory):")
    for cond, score in summary["by_condition"].items():
        print(f"  {cond:35}: {score:.3f}")
    print("\nBy needle type:")
    for t, score in summary["by_type"].items():
        print(f"  {t:20}: {score:.3f}")
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

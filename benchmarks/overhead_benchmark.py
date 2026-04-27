"""
overhead_benchmark.py — Token-Overhead & Reasoning-Cost Messung

Vergleicht für denselben Prompt:
  A) Direkter Aufruf an claude-sonnet-4-6 via CHEXT
  B) Aufruf über das MoE-Expert-Template (moe-overhead-sonnet46)

Gemessen werden:
  - Prompt-Tokens:      Kontext-Overhead durch Planner-Augmentation, System-Prompts,
                        GraphRAG-Kontext, Expert-Routing
  - Completion-Tokens:  Mehrfach-Antwort-Overhead (Planner + Expert + Judge)
  - Overhead-Faktor:    MoE_total / Direct_total (Kostenmultiplikator)
  - Reasoning-Tokens:   Wenn vom Modell geliefert (extended_thinking / thinking_tokens)

Methodik analog zu Artificial Analysis Intelligence Index:
  Gleiche Prompt-Suite, unterschiedliche Aufruf-Pfade, Token-Kosten verglichen.

Environment:
  MOE_API_KEY         MoE-Orchestrator API-Key (required)
  MOE_API_BASE        Orchestrator URL (default: http://localhost:8002)
  MOE_OVH_TEMPLATE    Overhead-Template (default: moe-overhead-sonnet46)
  CHEXT_BASE          Direkter CHEXT-Endpunkt (required; configure via env var)
  CHEXT_MODEL         Direktes Modell (default: claude-sonnet-4-6)
  OVH_DATASET         Dataset-Pfad (default: datasets/overhead_suite_v1.json)
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Optional

import httpx

MOE_BASE      = os.environ.get("MOE_API_BASE", "http://localhost:8002")
MOE_KEY       = os.environ.get("MOE_API_KEY", "")
MOE_TEMPLATE  = os.environ.get("MOE_OVH_TEMPLATE", "moe-overhead-sonnet46")
CHEXT_BASE    = os.environ.get("CHEXT_BASE", "")
CHEXT_MODEL   = os.environ.get("CHEXT_MODEL", "claude-sonnet-4-6")
DATASET       = os.environ.get(
    "OVH_DATASET",
    str(pathlib.Path(__file__).parent / "datasets" / "overhead_suite_v1.json"),
)

RESULTS_DIR = pathlib.Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

if not MOE_KEY:
    import sys
    print("ERROR: MOE_API_KEY required", file=sys.stderr)
    sys.exit(1)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class PromptResult:
    prompt_id:    str
    category:     str
    prompt_text:  str
    # Direct call
    direct_prompt_tokens:     int = 0
    direct_completion_tokens: int = 0
    direct_reasoning_tokens:  int = 0
    direct_latency_s:         float = 0.0
    direct_response:          str = ""
    direct_error:             str = ""
    # MoE call
    moe_prompt_tokens:        int = 0
    moe_completion_tokens:    int = 0
    moe_reasoning_tokens:     int = 0
    moe_latency_s:            float = 0.0
    moe_response:             str = ""
    moe_error:                str = ""

    @property
    def overhead_prompt_factor(self) -> Optional[float]:
        if self.direct_prompt_tokens > 0:
            return round(self.moe_prompt_tokens / self.direct_prompt_tokens, 2)
        return None

    @property
    def overhead_completion_factor(self) -> Optional[float]:
        if self.direct_completion_tokens > 0:
            return round(self.moe_completion_tokens / self.direct_completion_tokens, 2)
        return None

    @property
    def overhead_total_factor(self) -> Optional[float]:
        d = self.direct_prompt_tokens + self.direct_completion_tokens
        m = self.moe_prompt_tokens    + self.moe_completion_tokens
        if d > 0:
            return round(m / d, 2)
        return None

    @property
    def overhead_prompt_absolute(self) -> int:
        return self.moe_prompt_tokens - self.direct_prompt_tokens

    @property
    def overhead_completion_absolute(self) -> int:
        return self.moe_completion_tokens - self.direct_completion_tokens


@dataclass
class BenchmarkResult:
    moe_template: str
    direct_model: str
    timestamp:    str
    results:      list[PromptResult] = field(default_factory=list)

    def summary(self) -> dict:
        valid = [r for r in self.results if not r.direct_error and not r.moe_error]
        if not valid:
            return {}

        def avg(vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        return {
            "n_prompts":                   len(valid),
            "avg_direct_prompt_tokens":    avg([r.direct_prompt_tokens     for r in valid]),
            "avg_direct_completion_tokens":avg([r.direct_completion_tokens  for r in valid]),
            "avg_moe_prompt_tokens":       avg([r.moe_prompt_tokens         for r in valid]),
            "avg_moe_completion_tokens":   avg([r.moe_completion_tokens     for r in valid]),
            "avg_overhead_prompt_factor":  avg([r.overhead_prompt_factor    for r in valid]),
            "avg_overhead_completion_factor": avg([r.overhead_completion_factor for r in valid]),
            "avg_overhead_total_factor":   avg([r.overhead_total_factor     for r in valid]),
            "avg_overhead_prompt_absolute":avg([r.overhead_prompt_absolute  for r in valid]),
            "avg_overhead_completion_absolute": avg([r.overhead_completion_absolute for r in valid]),
            "avg_direct_latency_s":        avg([r.direct_latency_s          for r in valid]),
            "avg_moe_latency_s":           avg([r.moe_latency_s             for r in valid]),
            "by_category": _by_category(valid),
        }


def _by_category(results: list[PromptResult]) -> dict:
    cats: dict[str, list] = {}
    for r in results:
        cats.setdefault(r.category, []).append(r)
    out = {}
    for cat, rs in cats.items():
        out[cat] = {
            "n":                         len(rs),
            "avg_overhead_total_factor": round(
                sum(r.overhead_total_factor or 0 for r in rs) / len(rs), 2),
            "avg_overhead_prompt_abs":   round(
                sum(r.overhead_prompt_absolute for r in rs) / len(rs)),
        }
    return out


# ── API calls ─────────────────────────────────────────────────────────────────

def _extract_usage(resp_json: dict) -> tuple[int, int, int]:
    """Returns (prompt_tokens, completion_tokens, reasoning_tokens)."""
    usage = resp_json.get("usage", {})
    prompt     = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
    completion = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
    # Reasoning tokens: some models return this under usage or choices
    reasoning  = (
        usage.get("reasoning_tokens", 0) or
        usage.get("thinking_tokens",  0) or
        resp_json.get("choices", [{}])[0]
            .get("message", {})
            .get("reasoning_tokens", 0)
    )
    return prompt, completion, reasoning


async def call_direct(
    client: httpx.AsyncClient,
    messages: list[dict],
    timeout: float = 300.0,
) -> tuple[int, int, int, float, str, str]:
    """Direct call to CHEXT. Returns (prompt, completion, reasoning, latency, response, error)."""
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{CHEXT_BASE}/chat/completions",
            headers={"Content-Type": "application/json"},
            json={"model": CHEXT_MODEL, "messages": messages, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        d = resp.json()
        p, c, r = _extract_usage(d)
        content = d["choices"][0]["message"]["content"]
        return p, c, r, round(time.monotonic() - t0, 2), content[:300], ""
    except Exception as exc:
        return 0, 0, 0, round(time.monotonic() - t0, 2), "", str(exc)[:120]


async def call_moe(
    client: httpx.AsyncClient,
    messages: list[dict],
    session_id: str,
    timeout: float = 600.0,
) -> tuple[int, int, int, float, str, str]:
    """MoE orchestrator call. Returns (prompt, completion, reasoning, latency, response, error)."""
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{MOE_BASE}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {MOE_KEY}",
                "Content-Type":  "application/json",
                "X-Session-Id":  session_id,
            },
            json={
                "model":    MOE_TEMPLATE,
                "messages": messages,
                "stream":   False,
                "no_cache": True,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        d = resp.json()
        p, c, r = _extract_usage(d)
        content = d["choices"][0]["message"]["content"]
        return p, c, r, round(time.monotonic() - t0, 2), content[:300], ""
    except Exception as exc:
        return 0, 0, 0, round(time.monotonic() - t0, 2), "", str(exc)[:120]


# ── Main runner ───────────────────────────────────────────────────────────────

async def main() -> None:
    dataset = json.loads(pathlib.Path(DATASET).read_text())
    prompts = dataset["prompts"]

    ts     = time.strftime("%Y%m%d-%H%M%S")
    result = BenchmarkResult(
        moe_template=MOE_TEMPLATE,
        direct_model=CHEXT_MODEL,
        timestamp=ts,
    )

    print(f"Overhead Benchmark — {CHEXT_MODEL} direct vs. {MOE_TEMPLATE}")
    print(f"Prompts: {len(prompts)}\n")
    print(f"{'ID':6} {'Cat':12} {'Drct-P':>8} {'Drct-C':>8} {'MoE-P':>8} {'MoE-C':>8} {'OHF':>6} {'ΔP':>7} {'ΔC':>7}")
    print("─" * 75)

    async with httpx.AsyncClient() as client:
        for p in prompts:
            messages = [{"role": "user", "content": p["prompt"]}]
            session_id = uuid.uuid4().hex

            # Direct call
            dp, dc, dr, dl, dresp, derr = await call_direct(client, messages)
            # MoE call
            mp, mc, mr, ml, mresp, merr = await call_moe(client, messages, session_id)

            pr = PromptResult(
                prompt_id=p["id"], category=p["category"], prompt_text=p["prompt"][:100],
                direct_prompt_tokens=dp, direct_completion_tokens=dc,
                direct_reasoning_tokens=dr, direct_latency_s=dl,
                direct_response=dresp, direct_error=derr,
                moe_prompt_tokens=mp, moe_completion_tokens=mc,
                moe_reasoning_tokens=mr, moe_latency_s=ml,
                moe_response=mresp, moe_error=merr,
            )
            result.results.append(pr)

            ohf = f"{pr.overhead_total_factor:.2f}×" if pr.overhead_total_factor else "ERR"
            print(
                f"{p['id']:6} {p['category']:12} "
                f"{dp:8} {dc:8} {mp:8} {mc:8} "
                f"{ohf:>6} {pr.overhead_prompt_absolute:+7} {pr.overhead_completion_absolute:+7}"
            )

    # Save results
    out_path = RESULTS_DIR / f"overhead_{MOE_TEMPLATE}_{ts}.json"
    out_path.write_text(json.dumps(
        {"meta": {"moe_template": MOE_TEMPLATE, "direct_model": CHEXT_MODEL, "timestamp": ts},
         "summary": result.summary(),
         "results": [asdict(r) for r in result.results]},
        indent=2, ensure_ascii=False,
    ))

    summary = result.summary()
    print(f"\n{'═'*75}")
    print(f"Ø Direct:  {summary.get('avg_direct_prompt_tokens')} prompt + {summary.get('avg_direct_completion_tokens')} completion tokens")
    print(f"Ø MoE:     {summary.get('avg_moe_prompt_tokens')} prompt + {summary.get('avg_moe_completion_tokens')} completion tokens")
    print(f"Ø Overhead-Faktor (total): {summary.get('avg_overhead_total_factor')}×")
    print(f"Ø Prompt-Overhead:         +{summary.get('avg_overhead_prompt_absolute')} Tokens")
    print(f"Ø Completion-Overhead:     +{summary.get('avg_overhead_completion_absolute')} Tokens")
    print(f"\nBy Category:")
    for cat, d in summary.get("by_category", {}).items():
        print(f"  {cat:20}: {d['avg_overhead_total_factor']:.2f}× (Ø +{d['avg_overhead_prompt_abs']} Prompt-Tokens)")
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

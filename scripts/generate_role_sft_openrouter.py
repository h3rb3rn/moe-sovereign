#!/usr/bin/env python3
"""scripts/generate_role_sft_openrouter.py — OpenRouter-backed supplement to
Phase 2 of the LUMI-G full-finetuning plan
(~/.claude/plans/zazzy-beaming-koala.md).

Generates additional role_sft training examples via OpenRouter-hosted
open-weight models (moonshotai/kimi-k3, z-ai/glm-5.3) whose own model
licenses explicitly permit distillation / training other models from their
outputs. This deliberately replaces an earlier idea (using Claude Code /
Codex CLI / Antigravity's frontier models directly) that was verified and
dropped: Anthropic ("we prohibit customers from using our services to train
or develop AI models without our written permission" -- explicitly excludes
"models designed for open-ended text generation"), OpenAI (Services
Agreement 3.3(e): Output may not be used to develop AI models that compete
with OpenAI's products, except a narrow Permitted Exception for
classifiers/embeddings), and Google (Gemini API terms: "You may not use the
Services to develop models that compete with the Services") all currently
prohibit exactly this use case for their own models.

Runs LOCALLY, not on LUMI-G/inside the Singularity container -- OpenRouter
is a hosted API, no local GPU needed. Reuses the exact same role system
prompts, delimited output format, parser, and quality guards already built
and empirically verified in scripts/generate_diverse_training_seeds.py's
--mode role_sft (imported directly here, safe because this runs as a normal
local script with the repo root on sys.path, unlike the LUMI-G container
case documented in that module).

Safety/cost design (see plan discussion, 2026-09-07/08):
  - Preflight resource check (RAM/disk) before spending any budget -- this
    host also runs the production moe-infra stack.
  - Resumable: counts already-written lines in --output at startup and only
    generates the remainder -- a crash or Ctrl-C never re-generates (and
    re-pays for) already-completed examples.
  - Every request includes "usage": {"include": true}, so OpenRouter returns
    the REAL per-request cost (not an estimate) at no extra token cost --
    this funds a live running total and a --max-cost-usd hard stop.
  - HTTP 402 (out of credits) is terminal and is never retried (an OpenRouter
    402 keeps failing and each retry still counts against rate limits).
    HTTP 429/5xx are retried with exponential backoff.
  - A `GET /api/v1/key` call (account metadata, not a model call, costs
    nothing) reports remaining OpenRouter balance alongside progress.

Usage:
    export OPENROUTER_API_KEY=...   # or set it in .env
    python3 scripts/generate_role_sft_openrouter.py \\
        --role coder --model moonshotai/kimi-k3 --count 1500 \\
        --output datasets/role_sft_openrouter/coder_kimi_k3.jsonl \\
        --max-cost-usd 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from scripts.generate_diverse_training_seeds import (
    _JUDGE_CRITIC_PATTERN_FOCUS,
    _JUDGE_CRITIC_SFT_GENERATION_TEMPLATE,
    _JUDGE_CRITIC_TRAINING_SYSTEM_PROMPT,
    _LOOM_GENERATION_PROMPT,
    _PLANNER_PATTERN_FOCUS,
    _PLANNER_SFT_GENERATION_TEMPLATE,
    _PLANNER_TRAINING_SYSTEM_PROMPT,
    _ROLE_SFT_GENERATION_TEMPLATE,
    _ROLE_SYSTEM_PROMPTS,
    parse_judge_critic_sft_output,
    parse_loom_output,
    parse_planner_sft_output,
    parse_role_sft_output,
    render_chatml,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OutOfCreditsError(Exception):
    """Raised on HTTP 402 -- terminal, must never be retried."""


# ---------------------------------------------------------------------------
# Preflight resource check (stdlib only -- psutil is not a project
# dependency and this needs exactly three numbers, not a full library).
# ---------------------------------------------------------------------------

def preflight_check(min_free_ram_mb: int = 500, min_free_disk_gb: float = 2.0,
                     max_load_per_core: float = 4.0) -> None:
    """Abort before spending any API budget if the host is critically
    resource-constrained. This script itself is lightweight (async HTTP +
    incremental JSONL writes, no local model) but commonly runs on the
    shared moe-infra production host -- observed 2026-09-08: 35GB RAM total,
    839MB immediately free, swap ~100% used, from the Langfuse/Neo4j/
    ClickHouse/app-container stack already resident. This is a warn-and-
    abort gate against "the host is already in trouble", not a guarantee
    the run will succeed.
    """
    problems = []

    mem_available_mb = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mem_available_mb = int(line.split()[1]) / 1024
                    break
    except OSError:
        pass
    if mem_available_mb is None:
        print("WARNING: could not read /proc/meminfo -- skipping RAM check.")
    elif mem_available_mb < min_free_ram_mb:
        problems.append(f"MemAvailable {mem_available_mb:.0f}MB < {min_free_ram_mb}MB threshold")

    free_gb = shutil.disk_usage(".").free / (1024 ** 3)
    if free_gb < min_free_disk_gb:
        problems.append(f"Free disk {free_gb:.1f}GB < {min_free_disk_gb}GB threshold")

    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 1
        if load1 / cores > max_load_per_core:
            print(f"WARNING: load average {load1:.2f} across {cores} cores is high -- "
                  f"this script is network-bound so it should still run fine, but the "
                  f"host itself may be under stress from other services.")
    except (OSError, AttributeError):
        pass

    if problems:
        print("PREFLIGHT CHECK FAILED:")
        for p in problems:
            print(f"  - {p}")
        print("Aborting before spending any OpenRouter budget. Free up resources, or "
              "pass different --min-free-ram-mb/--min-free-disk-gb, and retry.")
        raise SystemExit(1)

    ram_str = f"{mem_available_mb:.0f}MB" if mem_available_mb is not None else "unknown"
    print(f"Preflight OK: MemAvailable={ram_str}, FreeDisk={free_gb:.1f}GB")


# ---------------------------------------------------------------------------
# OpenRouter client
# ---------------------------------------------------------------------------

async def fetch_key_info(client: httpx.AsyncClient, api_key: str) -> dict:
    """Account-metadata call -- NOT a model call, costs nothing. Used both
    at startup and periodically during the run to show real remaining
    OpenRouter balance alongside progress."""
    resp = await client.get(f"{OPENROUTER_BASE_URL}/key", headers={"Authorization": f"Bearer {api_key}"})
    resp.raise_for_status()
    return resp.json().get("data", {})


async def generate_one(client: httpx.AsyncClient, api_key: str, model: str, prompt: str,
                        max_tokens: int, reasoning_effort: Optional[str], max_retries: int = 5) -> tuple[str, float]:
    """One OpenRouter chat completion call. Returns (raw_text, cost_usd).
    Raises OutOfCreditsError on HTTP 402 -- do not retry (every retry still
    counts against rate limits despite failing guaranteed). Retries
    429/5xx/timeouts with exponential backoff.

    reasoning_effort bounds the model's internal "thinking" phase via
    OpenRouter's standardized `reasoning` parameter (confirmed supported by
    moonshotai/kimi-k3, z-ai/glm-5.3, and deepseek/deepseek-v4-pro-0813 via
    GET /api/v1/models' supported_parameters, 2026-09-08). Root cause of
    every reasoning-model failure observed in this project's testing
    (GLM-5.3 and Kimi K3 returning content=null, DeepSeek-V4-Pro leaking
    reasoning text into content) was NOT that reasoning models are
    unusable -- it was that reasoning was left uncontrolled (unbounded
    effort competing with the answer for the same max_tokens budget).
    Pass e.g. "low" or "none" here rather than leaving it unset.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": max_tokens,
        "usage": {"include": True},  # real per-request cost in the response, at no extra cost
    }
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    headers = {"Authorization": f"Bearer {api_key}"}
    backoff = 2.0
    for _ in range(max_retries):
        try:
            resp = await client.post(f"{OPENROUTER_BASE_URL}/chat/completions",
                                      json=payload, headers=headers, timeout=120)
        except (httpx.TimeoutException, httpx.TransportError):
            await asyncio.sleep(backoff)
            backoff *= 2
            continue
        if resp.status_code == 402:
            raise OutOfCreditsError(resp.text[:500])
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            await asyncio.sleep(float(retry_after) if retry_after else backoff)
            backoff *= 2
            continue
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        cost = float(data.get("usage", {}).get("cost", 0.0) or 0.0)
        # Reasoning models (confirmed live: z-ai/glm-5.3) can return
        # content=null when max_tokens is exhausted by internal reasoning
        # before any real answer is produced -- the response is still a
        # normal 200 with a real (billed) cost, not an error. Treat this as
        # an empty completion (parse_role_sft_output cleanly returns None
        # for it) rather than letting `None` propagate into string
        # operations and crash -- a crash here was previously swallowed
        # silently by asyncio.gather(..., return_exceptions=True) with zero
        # diagnostic output (0 written, 0 failed, $0 spent looked like
        # nothing happened at all, when in fact real cost was being billed).
        text = message.get("content") or ""
        if not text and message.get("reasoning"):
            text = f"[reasoning-only, no content -- max_tokens too low]\n{message['reasoning']}"
        return text, cost
    raise RuntimeError(f"Exhausted {max_retries} retries against OpenRouter for model {model!r}")


# ---------------------------------------------------------------------------
# Budget tracking + resumable run loop
# ---------------------------------------------------------------------------

@dataclass
class BudgetTracker:
    max_cost_usd: Optional[float]
    total_cost_usd: float = 0.0
    examples_written: int = 0
    examples_failed_parse: int = 0
    requests_made: int = 0

    def record(self, cost_usd: float, parsed: bool) -> None:
        self.total_cost_usd += cost_usd
        self.requests_made += 1
        if parsed:
            self.examples_written += 1
        else:
            self.examples_failed_parse += 1

    def over_budget(self) -> bool:
        return self.max_cost_usd is not None and self.total_cost_usd >= self.max_cost_usd

    def avg_cost_per_example(self) -> float:
        return self.total_cost_usd / self.examples_written if self.examples_written else 0.0


def _count_existing_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _write_budget_sidecar(path: Path, role: str, model: str, tracker: BudgetTracker,
                           target_count: int, already_written_before_run: int,
                           key_remaining_usd: Optional[str]) -> None:
    remaining_to_target = max(0, target_count - already_written_before_run - tracker.examples_written)
    projected_additional_cost = remaining_to_target * tracker.avg_cost_per_example()
    summary = {
        "role": role,
        "model": model,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "examples_written_this_run": tracker.examples_written,
        "examples_written_total": already_written_before_run + tracker.examples_written,
        "examples_failed_parse": tracker.examples_failed_parse,
        "requests_made": tracker.requests_made,
        "total_cost_usd_this_run": round(tracker.total_cost_usd, 4),
        "avg_cost_per_example_usd": round(tracker.avg_cost_per_example(), 5),
        "target_count": target_count,
        "projected_additional_cost_usd_to_reach_target": round(projected_additional_cost, 2),
        "openrouter_key_remaining_usd": key_remaining_usd,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


async def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY not set (export it, or put it in .env -- this "
                          "script loads .env automatically via python-dotenv).")

    preflight_check(min_free_ram_mb=args.min_free_ram_mb, min_free_disk_gb=args.min_free_disk_gb)

    # Planner/judge need structured, schema-validated output (JSON task-array
    # with real MCP tool schemas; CONFIRMED/direct-correction critic
    # contract) that the generic template never exercises -- see
    # docs/experiments/lumig_openrouter_teacher_verification.md Teil 3.4.
    # Bug found live (2026-09-08): this script has its own generation loop
    # separate from generate_diverse_training_seeds.py's run_role_sft_mode,
    # and was never updated when the planner/judge-specific modes were
    # added there -- a --role planner run silently fell back to the generic
    # prose template with 0 structural validation, "8/8 written" while
    # every single example was markdown prose, not a JSON task array.
    is_loom = args.mode == "loom"
    is_planner = not is_loom and args.role == "planner"
    is_judge = not is_loom and args.role == "judge"
    system_prompt = _ROLE_SYSTEM_PROMPTS[args.role] if not is_loom else None
    if is_loom:
        prompt = _LOOM_GENERATION_PROMPT
    elif is_planner:
        pattern_names = list(_PLANNER_PATTERN_FOCUS.keys())
        prompts_by_pattern = {
            name: _PLANNER_SFT_GENERATION_TEMPLATE.format(
                system_prompt=_PLANNER_TRAINING_SYSTEM_PROMPT, pattern_description=desc)
            for name, desc in _PLANNER_PATTERN_FOCUS.items()
        }
    elif is_judge:
        pattern_names = list(_JUDGE_CRITIC_PATTERN_FOCUS.keys())
        prompts_by_pattern = {
            name: _JUDGE_CRITIC_SFT_GENERATION_TEMPLATE.format(
                system_prompt=_JUDGE_CRITIC_TRAINING_SYSTEM_PROMPT, pattern_description=desc)
            for name, desc in _JUDGE_CRITIC_PATTERN_FOCUS.items()
        }
    else:
        prompt = _ROLE_SFT_GENERATION_TEMPLATE.format(system_prompt=system_prompt)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = output_path.with_suffix(output_path.suffix + ".debug.log")
    budget_path = output_path.with_suffix(output_path.suffix + ".budget.json")

    already_written = _count_existing_lines(output_path)
    remaining = args.count - already_written
    if remaining <= 0:
        print(f"Already have {already_written}/{args.count} examples at {output_path} -- nothing to do.")
        return
    print(f"Resuming: {already_written}/{args.count} already present, generating up to {remaining} more.")

    tracker = BudgetTracker(max_cost_usd=args.max_cost_usd)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: (
            print("Received interrupt -- finishing in-flight requests, then stopping cleanly."),
            stop_event.set(),
        ))

    semaphore = asyncio.Semaphore(args.concurrency)
    file_lock = asyncio.Lock()

    async with httpx.AsyncClient() as client:
        key_info = await fetch_key_info(client, api_key)
        print(f"OpenRouter key: limit_remaining={key_info.get('limit_remaining')}, "
              f"usage={key_info.get('usage')}")

        async def worker(i: int) -> None:
            async with semaphore:
                if stop_event.is_set() or tracker.over_budget():
                    return
                if is_planner or is_judge:
                    this_prompt = prompts_by_pattern[pattern_names[i % len(pattern_names)]]
                else:
                    this_prompt = prompt
                try:
                    raw_text, cost = await generate_one(client, api_key, args.model, this_prompt,
                                                          args.max_tokens, args.reasoning_effort)
                except OutOfCreditsError:
                    print("OpenRouter returned 402 (out of credits) -- stopping cleanly, "
                          "no further requests will be sent. Nothing already written is lost; "
                          "re-run the same command after topping up to resume.")
                    stop_event.set()
                    return
                except Exception as exc:  # noqa: BLE001 -- one bad generation must not kill the batch
                    print(f"Request failed after retries: {exc!r} -- skipping this example.")
                    return

                if is_loom:
                    parsed = parse_loom_output(raw_text)
                elif is_planner:
                    parsed = parse_planner_sft_output(raw_text)
                elif is_judge:
                    parsed = parse_judge_critic_sft_output(raw_text)
                else:
                    parsed = parse_role_sft_output(raw_text)
                async with file_lock:
                    if parsed is not None:
                        if is_loom:
                            # Raw candidate for generate_loom_seed_examples.py
                            # --llm-scenarios-file -- NOT yet sandbox-verified,
                            # NOT ChatML, must not be used as training data
                            # directly (see docs/experiments/
                            # lumig_openrouter_teacher_verification.md).
                            record = parsed
                        else:
                            text = render_chatml(system_prompt, parsed["user_request"], parsed["assistant_response"])
                            record = {"text": text}
                        with open(output_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")
                            f.flush()
                    else:
                        with open(debug_path, "a", encoding="utf-8") as f:
                            f.write("=" * 80 + "\n" + raw_text[:4000] + "\n")
                    tracker.record(cost, parsed is not None)

                    if tracker.requests_made % 10 == 0 or tracker.over_budget():
                        key_remaining = None
                        try:
                            key_remaining = (await fetch_key_info(client, api_key)).get("limit_remaining")
                        except Exception:  # noqa: BLE001 -- a status check must never abort the run
                            pass
                        _write_budget_sidecar(budget_path, args.role, args.model, tracker,
                                               args.count, already_written, key_remaining)
                        print(f"[{args.role or args.mode}] {already_written + tracker.examples_written}/{args.count} written "
                              f"({tracker.examples_failed_parse} parse-failed this run), "
                              f"${tracker.total_cost_usd:.3f} spent "
                              f"(${tracker.avg_cost_per_example():.4f}/example avg), "
                              f"key remaining=${key_remaining}")

                    if tracker.over_budget():
                        print(f"Reached --max-cost-usd {args.max_cost_usd} -- stopping cleanly.")
                        stop_event.set()

        tasks = [asyncio.create_task(worker(i)) for i in range(remaining)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # return_exceptions=True means a worker() bug does not abort the
        # whole batch -- but it also means it fails SILENTLY unless the
        # results are inspected. A real bug (GLM-5.3 content=null crashing
        # inside parse_role_sft_output) was previously swallowed this way,
        # producing "0 written, 0 failed, $0 spent" with zero diagnostic
        # trail even though real, billed requests had gone out.
        crashed = [r for r in results if isinstance(r, BaseException)]
        if crashed:
            print(f"WARNING: {len(crashed)}/{len(results)} worker tasks raised an unhandled "
                  f"exception (not counted as parse failures above -- these never reached the "
                  f"tracker/file write). First exception: {crashed[0]!r}")

    _write_budget_sidecar(budget_path, args.role, args.model, tracker, args.count, already_written, None)
    print(f"Done: {tracker.examples_written} written this run "
          f"({already_written + tracker.examples_written}/{args.count} total), "
          f"{tracker.examples_failed_parse} parse-failed, ${tracker.total_cost_usd:.3f} spent.")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", default="role_sft", choices=["role_sft", "loom"],
                         help="role_sft: per-role training examples (default). loom: Candidate-1 "
                              "memory-ordering scenario pairs (--role is ignored in this mode, output "
                              "must still be sandbox-verified via generate_loom_seed_examples.py "
                              "--llm-scenarios-file before use -- this script only generates candidates)")
    parser.add_argument("--role", choices=sorted(_ROLE_SYSTEM_PROMPTS.keys()),
                         help="required for --mode role_sft, ignored for --mode loom")
    parser.add_argument("--model", required=True, help="OpenRouter model id, e.g. moonshotai/kimi-k3 or z-ai/glm-5.3")
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-tokens", type=int, default=3072)
    parser.add_argument("--reasoning-effort", default="low", choices=["none", "minimal", "low", "medium", "high", "max", "xhigh"],
                         help="bounds the model's internal reasoning phase via OpenRouter's standardized "
                              "reasoning parameter -- root cause of every reasoning-model failure observed "
                              "in this project (content=null, reasoning text leaking into content) was "
                              "leaving this uncontrolled, not that reasoning models are inherently unusable")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--max-cost-usd", type=float, default=None,
                         help="hard stop once cumulative real cost reaches this, in addition to "
                              "any OpenRouter-dashboard-side per-key spend limit")
    parser.add_argument("--min-free-ram-mb", type=int, default=500, help="preflight check threshold")
    parser.add_argument("--min-free-disk-gb", type=float, default=2.0, help="preflight check threshold")
    args = parser.parse_args()
    if args.mode == "role_sft" and not args.role:
        parser.error("--mode role_sft requires --role")
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

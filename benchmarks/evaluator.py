"""
evaluator.py -- MoE-Eval LLM-as-a-Judge Evaluator

Takes the results from runner.py and scores each answer using:
  1. Deterministic checks (keyword matching, numeric tolerance)
  2. An LLM judge for semantic quality scoring (0-10)

The LLM judge is called via the same MoE Sovereign API using a dedicated
template (ideally the strongest available, e.g. moe-reference-70b-deep).

Configuration via environment variables:
  MOE_API_BASE      Base URL (default: http://localhost:8002)
  MOE_API_KEY       API key (required)
  MOE_JUDGE_TEMPLATE Template for the judge LLM (default: moe-reference-30b-balanced)
  MOE_EVAL_RESULTS  Path to the runner's results JSON (default: auto-detect latest)

Usage:
  MOE_API_KEY=moe-sk-... python benchmarks/evaluator.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
import time
from typing import Any

import httpx

API_BASE        = os.environ.get("MOE_API_BASE", "http://localhost:8002")
API_KEY         = os.environ.get("MOE_API_KEY", "")
JUDGE_TEMPLATE  = os.environ.get("MOE_JUDGE_TEMPLATE", "moe-reference-30b-balanced")
RESULTS_DIR     = pathlib.Path(__file__).parent / "results"
DATASET_DIR     = pathlib.Path(__file__).parent / "datasets"

# Direct Ollama endpoint for the LLM judge — BYPASSES the orchestrator
# pipeline to get clean JSON scoring responses instead of full MoE answers.
# The orchestrator pipeline transforms every prompt through planner→experts→
# merger→judge, producing a full answer instead of a simple score. By calling
# Ollama directly, we get exactly what we ask for: a JSON score object.
JUDGE_OLLAMA_URL = os.environ.get("MOE_JUDGE_OLLAMA_URL", "")
JUDGE_MODEL      = os.environ.get("MOE_JUDGE_MODEL", "phi4:14b")

if not API_KEY:
    print("ERROR: MOE_API_KEY required.", file=sys.stderr)
    sys.exit(1)

if not JUDGE_OLLAMA_URL:
    print("ERROR: MOE_JUDGE_OLLAMA_URL required (e.g. http://<ollama-host>:11434).", file=sys.stderr)
    sys.exit(1)


def find_latest_results() -> pathlib.Path:
    """Find the most recent results JSON from the runner."""
    path = os.environ.get("MOE_EVAL_RESULTS")
    if path:
        return pathlib.Path(path)
    candidates = sorted(RESULTS_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        print("No results files found in benchmarks/results/", file=sys.stderr)
        sys.exit(1)
    return candidates[0]


def load_dataset() -> dict:
    """Load the evaluation dataset with expected answers.

    Respects MOE_EVAL_DATASET env var so overnight/empirical datasets work.
    """
    ds_name = os.environ.get("MOE_EVAL_DATASET", "moe_eval_v1.json")
    ds_path = DATASET_DIR / ds_name
    return json.loads(ds_path.read_text())


# --------------------------------------------------------------------------
# Deterministic scoring
# --------------------------------------------------------------------------

def score_keyword_match(response: str, scoring: dict) -> dict:
    """Score based on keyword presence in the response."""
    response_lower = response.lower()
    required = scoring.get("required_keywords", [])
    bonus = scoring.get("bonus_keywords", [])

    req_found = [k for k in required if k.lower() in response_lower]
    bonus_found = [k for k in bonus if k.lower() in response_lower]

    req_score = len(req_found) / len(required) if required else 1.0
    bonus_score = len(bonus_found) / len(bonus) if bonus else 0.0

    # Base score from required (0-7), bonus adds up to 3
    score = req_score * 7.0 + bonus_score * 3.0

    # Disclaimer check for medical
    if scoring.get("disclaimer_required"):
        disclaimer_kws = scoring.get("disclaimer_keywords", [])
        has_disclaimer = any(k.lower() in response_lower for k in disclaimer_kws)
        if not has_disclaimer:
            score = max(0, score - 2.0)  # penalty for missing disclaimer

    return {
        "score": round(min(score, 10.0), 1),
        "required_found": req_found,
        "required_missing": [k for k in required if k not in req_found],
        "bonus_found": bonus_found,
        "method": "keyword_match",
    }


def score_numeric_tolerance(response: str, scoring: dict) -> dict:
    """Score based on numeric values in the response matching expected values."""
    checks = scoring.get("checks", [])
    results = []
    total_score = 0.0

    for check in checks:
        expected = check["expected"]
        tolerance = check.get("tolerance_pct", 1.0)

        # Find numbers in the response
        numbers = re.findall(r"[\d.,]+", response.replace(".", "").replace(",", "."))
        # Also try with dots as decimal separators
        numbers2 = re.findall(r"\d+[.,]?\d*", response)
        all_nums = set()
        for n in numbers + numbers2:
            try:
                all_nums.add(float(n.replace(",", ".")))
            except ValueError:
                pass

        matched = False
        for num in all_nums:
            if abs(num - expected) / max(abs(expected), 1e-9) * 100 <= tolerance:
                matched = True
                break

        results.append({
            "field": check.get("field", "?"),
            "expected": expected,
            "matched": matched,
        })
        if matched:
            total_score += 10.0 / len(checks)

    return {
        "score": round(total_score, 1),
        "checks": results,
        "method": "numeric_tolerance",
    }


def score_exact_match(response: str, scoring: dict) -> dict:
    """Score based on exact value presence."""
    expected = str(scoring["expected_value"])
    found = expected in response
    return {
        "score": 10.0 if found else 0.0,
        "expected": expected,
        "found": found,
        "method": "exact_match",
    }


def score_combined(response: str, scoring: dict) -> dict:
    """Score using multiple check methods."""
    checks = scoring.get("checks", [])
    sub_scores = []
    for check in checks:
        method = check["type"]
        if method == "keyword_match":
            sub = score_keyword_match(response, check)
        elif method == "numeric_tolerance":
            sub = score_numeric_tolerance(response, check)
        elif method == "exact_match":
            sub = score_exact_match(response, check)
        else:
            sub = {"score": 0, "method": method, "error": "unknown method"}
        sub_scores.append(sub)

    avg_score = sum(s["score"] for s in sub_scores) / max(len(sub_scores), 1)
    return {
        "score": round(avg_score, 1),
        "sub_scores": sub_scores,
        "method": "combined",
    }


SCORING_METHODS = {
    "keyword_match": score_keyword_match,
    "numeric_tolerance": score_numeric_tolerance,
    "exact_match": score_exact_match,
    "combined": score_combined,
}


def score_deterministic(response: str, scoring: dict) -> dict:
    """Apply the appropriate deterministic scoring method."""
    method = scoring.get("type", "keyword_match")
    fn = SCORING_METHODS.get(method, score_keyword_match)
    return fn(response, scoring)


# --------------------------------------------------------------------------
# LLM-as-a-Judge scoring
# --------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = (
    "You are a strict, fair evaluator for AI-generated answers. "
    "Score the following answer to the given question. "
    "Scoring scale 0-10:\n"
    "  0-2: Wrong, misleading, or completely off-topic\n"
    "  3-4: Partially correct, significant errors or gaps\n"
    "  5-6: Acceptable, fundamentals correct, details missing\n"
    "  7-8: Good, all key aspects correct, minor weaknesses\n"
    "  9-10: Excellent, precise, complete, professional\n\n"
    "Reply ONLY with a JSON object:\n"
    '{"score": <0-10>, "reasoning": "<2-3 sentence justification>"}'
)

# Sanitised prompt used when the primary judge refuses (e.g. medical/legal content filters).
# Strips domain-specific framing and asks the model to assess structural quality only.
JUDGE_SYSTEM_PROMPT_FALLBACK = (
    "You are a quality assessor for text responses. "
    "Assess whether the given answer structurally covers the expected key points. "
    "Ignore domain-specific content — evaluate only: completeness, structure, precision.\n"
    "Scoring scale 0-10 (0=empty/irrelevant, 5=partial, 10=complete).\n"
    "Reply ONLY with: "
    '{"score": <0-10>, "reasoning": "<1-2 sentences>"}'
)


def _parse_judge_content(content: str) -> dict | None:
    """Extract score and reasoning from judge output. Returns None if unparseable."""
    m = re.search(r"\{[^{}]*\"score\"[^{}]*\}", content, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            score = max(0.0, min(10.0, float(parsed.get("score", 0))))
            return {"llm_score": score, "llm_reasoning": parsed.get("reasoning", "")[:500]}
        except (json.JSONDecodeError, ValueError):
            pass
    # Fallback: bare number
    m = re.search(r"\b(\d{1,2}(?:\.\d)?)\b", content)
    if m:
        score = max(0.0, min(10.0, float(m.group(1))))
        return {"llm_score": score, "llm_reasoning": content[:300]}
    return None


async def _call_judge(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_content: str,
) -> str:
    """Single Ollama /v1/chat/completions call; returns raw content string."""
    r = await client.post(
        f"{JUDGE_OLLAMA_URL}/v1/chat/completions",
        json={
            "model": JUDGE_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "max_tokens": 300,
            "temperature": 0.1,
        },
        timeout=600,
    )
    return r.json().get("choices", [{}])[0].get("message", {}).get("content", "")


async def judge_response(
    client: httpx.AsyncClient,
    question: str,
    answer: str,
    expected_info: str,
) -> dict:
    """Score an answer using a DIRECT Ollama call (bypassing the orchestrator).

    Why direct?  The orchestrator's pipeline transforms every prompt through
    planner → experts → merger → judge, producing a full expert-routed answer
    instead of a clean scoring JSON.  By calling Ollama directly via its
    OpenAI-compatible /v1/chat/completions endpoint, we get exactly what the
    system prompt asks for: a {"score": N, "reasoning": "..."} JSON object.

    Fallback chain when the primary judge refuses (empty response / content filter):
      1. Sanitised prompt — strips domain framing, asks structural quality only.
      2. det_only — judge unavailable; returns a sentinel so the caller can fall
         back to the deterministic score exclusively (no LLM penalty applied).
    """
    user_content = (
        f"FRAGE:\n{question}\n\n"
        f"ERWARTETE KERNPUNKTE:\n{expected_info}\n\n"
        f"ZU BEWERTENDE ANTWORT:\n{answer[:3000]}"  # cap to avoid token overflow
    )

    # --- Attempt 1: primary judge prompt ---
    try:
        content = await _call_judge(client, JUDGE_SYSTEM_PROMPT, user_content)
        result = _parse_judge_content(content)
        if result:
            return result
        print(f"      [judge] primary returned unparseable: {content[:80]!r}", flush=True)
    except Exception as e:
        print(f"      [judge] primary error: {e}", flush=True)

    # --- Attempt 2: sanitised fallback prompt (no domain context) ---
    print(f"      [judge] retrying with sanitised fallback prompt...", flush=True)
    fallback_content = (
        f"ERWARTETE KERNPUNKTE:\n{expected_info}\n\n"
        f"ANTWORT ZU PRÜFEN:\n{answer[:2000]}"
    )
    try:
        content = await _call_judge(client, JUDGE_SYSTEM_PROMPT_FALLBACK, fallback_content)
        result = _parse_judge_content(content)
        if result:
            result["llm_reasoning"] = "[fallback-prompt] " + result["llm_reasoning"]
            return result
        print(f"      [judge] fallback also unparseable: {content[:80]!r}", flush=True)
    except Exception as e:
        print(f"      [judge] fallback error: {e}", flush=True)

    # --- Attempt 3: judge fully unavailable — signal det_only to caller ---
    return {"llm_score": None, "llm_reasoning": "judge_refused: no score produced"}


# --------------------------------------------------------------------------
# Main evaluation pipeline
# --------------------------------------------------------------------------

async def main() -> int:
    results_path = find_latest_results()
    print(f"MoE-Eval Evaluator", flush=True)
    print(f"  Results:    {results_path.name}", flush=True)
    print(f"  Judge LLM:  {JUDGE_MODEL} @ {JUDGE_OLLAMA_URL}", flush=True)
    print(f"  (direct Ollama call, bypasses orchestrator pipeline)", flush=True)
    print(f"{'='*72}", flush=True)

    run_data = json.loads(results_path.read_text())
    dataset = load_dataset()
    tc_map = {tc["id"]: tc for tc in dataset["test_cases"]}

    evaluated = []

    async with httpx.AsyncClient() as client:
        for r in run_data["results"]:
            test_id = r["test_id"]
            tc = tc_map.get(test_id)
            if not tc:
                print(f"  [skip] {test_id}: not found in dataset", flush=True)
                continue

            print(f"\n  evaluating: {test_id}", flush=True)

            # Get the final response (last turn)
            turns = r.get("turns", [])
            if not turns:
                print(f"    [skip] no turns", flush=True)
                continue

            last_turn = turns[-1]
            response = last_turn.get("response", "")

            if not response:
                print(f"    [skip] empty response", flush=True)
                r["score"] = 0.0
                r["score_details"] = {"error": "empty response"}
                evaluated.append(r)
                continue

            _PIPELINE_ERROR_PREFIXES = (
                "[Judge unavailable",
                "[TURN FAILED",
                "[Error:",
            )
            if any(response.startswith(p) for p in _PIPELINE_ERROR_PREFIXES):
                print(f"    [skip] pipeline error: {response[:70]}", flush=True)
                r["score"] = 0.0
                r["score_details"] = {"error": "pipeline_error", "message": response[:200]}
                evaluated.append(r)
                continue

            # 1. Deterministic scoring
            scoring_def = None
            if tc.get("type") == "multi_turn":
                # For multi-turn, scoring is on the last turn
                last_turn_def = tc["turns"][-1]
                scoring_def = last_turn_def.get("scoring")
            else:
                scoring_def = tc.get("scoring")

            det_result = {}
            if scoring_def:
                det_result = score_deterministic(response, scoring_def)
                print(f"    deterministic: {det_result['score']}/10 "
                      f"({det_result['method']})", flush=True)

            # 2. LLM judge scoring
            expected_info = json.dumps(
                tc.get("expected_answer", tc.get("turns", [{}])[-1].get("expected_answer", {})),
                ensure_ascii=False, indent=2,
            )
            print(f"    calling LLM judge ({JUDGE_TEMPLATE})...", flush=True)
            llm_result = await judge_response(
                client, last_turn["prompt"], response, expected_info,
            )
            print(f"    LLM judge: {llm_result['llm_score']}/10 — "
                  f"{llm_result['llm_reasoning'][:100]}", flush=True)

            # 3. Combined score: 40% deterministic + 60% LLM judge.
            # When the judge refused entirely (llm_score=None), fall back to
            # det_score exclusively so a content-filter refusal does not zero
            # out an otherwise correct response.
            det_score = det_result.get("score", 5.0) if det_result else 5.0
            llm_score = llm_result.get("llm_score")  # may be None on refusal

            if llm_score is None:
                combined = round(det_score, 1)
                formula = "det_only (judge refused)"
                print(f"    ⚠ Judge refused — using det_only: {combined}/10", flush=True)
            else:
                combined = round(det_score * 0.4 + llm_score * 0.6, 1)
                formula = "0.4 * deterministic + 0.6 * llm_judge"
                print(f"    COMBINED: {combined}/10 "
                      f"(det={det_score:.1f} × 0.4 + llm={llm_score:.1f} × 0.6)", flush=True)

            r["score"] = combined
            r["score_details"] = {
                "deterministic": det_result,
                "llm_judge": llm_result,
                "combined_formula": formula,
            }

            evaluated.append(r)

    # Save evaluated results
    ts = time.strftime("%Y%m%d-%H%M%S")
    eval_out = {
        "source_run": results_path.name,
        "judge_template": JUDGE_TEMPLATE,
        "evaluated_at": ts,
        "results": evaluated,
    }
    eval_path = RESULTS_DIR / f"eval_{ts}.json"
    eval_path.write_text(json.dumps(eval_out, indent=2, ensure_ascii=False))
    (RESULTS_DIR / "latest_eval.json").write_text(
        json.dumps(eval_out, indent=2, ensure_ascii=False)
    )

    # Summary table
    print(f"\n{'='*72}", flush=True)
    print(f"{'Test ID':40s} {'Cat':15s} {'Det':>5s} {'LLM':>5s} {'Comb':>6s}", flush=True)
    print(f"{'-'*40} {'-'*15} {'-'*5} {'-'*5} {'-'*6}", flush=True)
    total_score = 0.0
    for r in evaluated:
        det = r.get("score_details", {}).get("deterministic", {}).get("score", "-")
        llm = r.get("score_details", {}).get("llm_judge", {}).get("llm_score", "-")
        comb = r.get("score", 0)
        total_score += comb
        print(f"{r['test_id']:40s} {r['category']:15s} "
              f"{det:>5} {llm:>5} {comb:6.1f}", flush=True)

    avg = total_score / max(len(evaluated), 1)
    print(f"\nAverage score: {avg:.1f}/10 across {len(evaluated)} test cases", flush=True)
    print(f"\nSaved: {eval_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

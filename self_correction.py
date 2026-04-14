"""
Self-Correction Loop — extracts discrepancies from judge outputs and
stores few-shot examples for the planner.

Trigger:
  1. Automatic: Judge-Merger detects numerical discrepancy (cross_domain_mismatch=True)
  2. Manual:    User sends negative feedback (rating <= FEEDBACK_NEGATIVE_THRESHOLD)

Storage (dual, for persistence):
  - Redis: Key "moe:few_shot:{category}" → LPUSH, max 20 entries (LRU rotation via LTRIM)
  - File: /opt/moe-infra/few_shot_examples/{category}.md (append-only, readable for sync agent)

Retrieval:
  get_few_shot_context(category, redis_client) → str for planner prompt injection
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN.SelfCorrection")

_FEW_SHOT_DIR = Path(os.getenv("FEW_SHOT_DIR", "/opt/moe-infra/few_shot_examples"))
_MAX_ENTRIES  = 20  # Max few-shot entries per category in Redis


# ── Numeric discrepancy detection ────────────────────────────────────────────

_NUMBER_RE = re.compile(r'\b(\d+(?:[.,]\d+)?)\s*([a-zA-Z%°]{0,5})\b')


def _extract_numbers(text: str) -> list[tuple[float, str]]:
    """Extracts (value, unit) pairs from text."""
    results = []
    for m in _NUMBER_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", "."))
            unit = m.group(2).strip()
            results.append((val, unit))
        except ValueError:
            pass
    return results


def detect_numeric_mismatch(
    original_query: str,
    expert_output: str,
    tolerance: float = 0.01,
) -> list[dict]:
    """Compares numeric values from the original query with expert output.

    Returns:
        List of discrepancies: [{"original": (val, unit), "expert": (val, unit)}]
    """
    orig_nums  = _extract_numbers(original_query)
    expert_nums = _extract_numbers(expert_output)
    if not orig_nums:
        return []

    mismatches = []
    for o_val, o_unit in orig_nums:
        if o_val == 0:
            continue
        # Search for matching unit in expert output
        for e_val, e_unit in expert_nums:
            if o_unit and e_unit and o_unit.lower() != e_unit.lower():
                continue  # Unit mismatch — skip comparison
            rel_diff = abs(o_val - e_val) / abs(o_val)
            if rel_diff > tolerance and rel_diff < 10.0:  # >1% but <1000% → real discrepancy
                mismatches.append({
                    "original": {"value": o_val, "unit": o_unit},
                    "expert":   {"value": e_val, "unit": e_unit},
                    "rel_diff": round(rel_diff, 3),
                })
    return mismatches


# ── Few-shot storage ─────────────────────────────────────────────────────────

def _few_shot_file(category: str) -> Path:
    """Returns the path to the few-shot file for a given category."""
    safe = re.sub(r'[^\w-]', '_', category.lower())
    return _FEW_SHOT_DIR / f"{safe}.md"


def _format_entry(
    query: str,
    wrong_output: str,
    correction: str,
    category: str,
    mismatches: list[dict],
) -> str:
    """Formats a few-shot entry as Markdown."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    mismatch_str = ""
    if mismatches:
        parts = [
            f"  - Expected: {m['original']['value']}{m['original']['unit']} | "
            f"Expert: {m['expert']['value']}{m['expert']['unit']} (Δ{m['rel_diff']:.1%})"
            for m in mismatches[:3]
        ]
        mismatch_str = "\nNumeric discrepancies:\n" + "\n".join(parts)

    return (
        f"### [{ts}] {category}\n"
        f"**Query:** {query[:300]}\n"
        f"**Error:** {wrong_output[:400]}\n"
        f"**Correction:** {correction[:400]}"
        f"{mismatch_str}\n\n---\n"
    )


async def save_few_shot(
    category: str,
    query: str,
    wrong_output: str,
    correction: str,
    mismatches: list[dict],
    redis_client=None,
) -> None:
    """Stores a few-shot entry in Redis and file (fire & forget suitable)."""
    entry_md = _format_entry(query, wrong_output, correction, category, mismatches)
    entry_json = json.dumps({
        "query":       query[:300],
        "wrong":       wrong_output[:400],
        "correction":  correction[:400],
        "mismatches":  mismatches[:3],
        "ts":          datetime.now().isoformat(),
    }, ensure_ascii=False)

    # 1. Redis
    if redis_client is not None:
        try:
            key = f"moe:few_shot:{category}"
            await redis_client.lpush(key, entry_json)
            await redis_client.ltrim(key, 0, _MAX_ENTRIES - 1)  # LRU: keep max 20 entries
            logger.info(f"📝 Few-shot saved (Redis): {key}")
        except Exception as e:
            logger.warning(f"Few-shot Redis error: {e}")

    # 2. File
    try:
        _FEW_SHOT_DIR.mkdir(parents=True, exist_ok=True)
        fpath = _few_shot_file(category)
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(entry_md)
        logger.info(f"📝 Few-shot saved (file): {fpath}")
    except Exception as e:
        logger.warning(f"Few-shot file error: {e}")


# ── Few-shot retrieval for planner ───────────────────────────────────────────

async def get_few_shot_context(
    categories: list[str],
    redis_client=None,
    max_per_cat: int = 3,
) -> str:
    """Returns few-shot context for the planner (top-N per category).

    Args:
        categories: List of plan categories for the current request.
        redis_client: Optional Redis client.
        max_per_cat: Max entries per category.

    Returns:
        Formatted string for planner prompt injection, or ''.
    """
    if redis_client is None:
        return ""
    blocks: list[str] = []
    for cat in categories:
        key = f"moe:few_shot:{cat}"
        try:
            entries_raw = await redis_client.lrange(key, 0, max_per_cat - 1)
            if not entries_raw:
                continue
            cat_blocks: list[str] = []
            for raw in entries_raw:
                try:
                    e = json.loads(raw)
                    cat_blocks.append(
                        f"  Q: {e.get('query', '')[:150]}\n"
                        f"  WRONG: {e.get('wrong', '')[:150]}\n"
                        f"  CORRECT: {e.get('correction', '')[:150]}"
                    )
                except Exception:
                    pass
            if cat_blocks:
                blocks.append(
                    f"CORRECTIONS [{cat}] (from self-correction loop — avoid these errors):\n"
                    + "\n".join(cat_blocks)
                )
        except Exception as e:
            logger.debug(f"Few-shot retrieval error [{cat}]: {e}")
    if not blocks:
        return ""
    return "\nKNOWN ERROR PATTERNS (avoid these):\n" + "\n\n".join(blocks) + "\n"


# ── Main function: called by merger_node ─────────────────────────────────

async def process_merger_output(
    query: str,
    expert_results: list[str],
    final_response: str,
    plan: list[dict],
    redis_client=None,
) -> None:
    """Analyzes merger output for discrepancies and stores few-shot examples.

    This function is called as a background task (asyncio.create_task) — non-blocking.
    """
    if not expert_results:
        return

    for result in expert_results:
        # Extract category from result header
        m = re.search(r'/\s*(\w+)\]:', result)
        category = m.group(1).lower() if m else "general"

        # Numerical discrepancy detection
        mismatches = detect_numeric_mismatch(query, result)
        if not mismatches:
            continue

        # Extract correct answer from final_response (judge output)
        # Search for DETAILS: section
        details_m = re.search(r'DETAILS:\s*\n(.*)', final_response, re.S | re.I)
        correction = details_m.group(1)[:400].strip() if details_m else final_response[:400]

        logger.info(
            f"🔄 Self-Correction [{category}]: "
            f"{len(mismatches)} numerical discrepancy/discrepancies detected"
        )

        await save_few_shot(
            category=category,
            query=query,
            wrong_output=result[:400],
            correction=correction,
            mismatches=mismatches,
            redis_client=redis_client,
        )

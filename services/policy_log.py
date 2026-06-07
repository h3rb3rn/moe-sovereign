"""
policy_log.py — Structured training-data collector for the MoE Meta-Policy.

Writes one JSONL record per completed pipeline request containing the full
(State, Action, Reward) tuple needed to train an expert-routing policy:

  State:  query complexity, plan categories, context flags
  Action: which experts were called, in which configuration
  Reward: confidence distribution, refinement cost, self-eval score

Log file: /opt/moe-infra/agent-logs/policy_training.jsonl
Each record is a single JSON line. The self-eval score arrives asynchronously
and is appended as an update to the same record via update_policy_event().
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LOG_PATH = Path(os.getenv("POLICY_LOG_PATH", "/opt/moe-infra/agent-logs/policy_training.jsonl"))
_LOG_ENABLED = os.getenv("POLICY_LOG_ENABLED", "1") not in ("0", "false", "False")

# In-memory index: chat_id → byte offset of that record's newline.
# Used by update_policy_event() to patch in the self-eval score without
# rewriting the file (append-only except for the targeted 1-byte seek).
# Only populated for records written in the current process lifetime.
_pending: dict[str, int] = {}
_lock = asyncio.Lock()


def _ensure_log_dir() -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("policy_log: cannot create log dir %s: %s", _LOG_PATH.parent, exc)


def _query_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


async def log_policy_event(
    *,
    chat_id: str,
    query: str,
    complexity: str,
    plan_categories: list[str],
    experts_called: list[str],           # ["model::category", ...]
    confidence_map: dict[str, str],      # {category: "high"|"medium"|"low"}
    judge_refined_cats: set[str],
    refinement_rounds: int,
    fast_path: bool,
    cache_hit: bool,
    web_research_used: bool,
    graphrag_used: bool,
    template_id: str,
    latency_s: float,
    tier_escalations: int = 0,
) -> None:
    """Write one training record. Call from synthesis.py after Thompson reward."""
    if not _LOG_ENABLED:
        return
    _ensure_log_dir()

    conf_counts = {"high": 0, "medium": 0, "low": 0}
    for v in confidence_map.values():
        if v in conf_counts:
            conf_counts[v] += 1

    record = {
        "v":                  2,                        # schema version
        "ts":                 time.time(),
        "chat_id":            chat_id,
        "query_hash":         _query_hash(query),
        "query_len":          len(query),
        "complexity":         complexity,
        "plan_categories":    sorted(plan_categories),
        "experts_called":     experts_called,
        "confidence_map":     confidence_map,
        "conf_high":          conf_counts["high"],
        "conf_medium":        conf_counts["medium"],
        "conf_low":           conf_counts["low"],
        "judge_refined_cats": sorted(judge_refined_cats),
        "refinement_rounds":  refinement_rounds,
        "refinement_cost":    len(judge_refined_cats),  # proxy: categories that needed work
        "fast_path":          fast_path,
        "cache_hit":          cache_hit,
        "web_research":       web_research_used,
        "graphrag":           graphrag_used,
        "template_id":        template_id,
        "latency_s":          round(latency_s, 3),
        "tier_escalations":   tier_escalations,
        "self_eval_score":    None,                     # filled by update_policy_event()
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"

    async with _lock:
        try:
            with open(_LOG_PATH, "ab") as fh:
                offset = fh.seek(0, 2)          # current EOF = start of this record
                fh.write(line.encode("utf-8"))
            _pending[chat_id] = offset
        except Exception as exc:
            logger.warning("policy_log: write failed: %s", exc)


async def update_policy_event(chat_id: str, self_eval_score: int) -> None:
    """Patch the self_eval_score field in-place after async evaluation completes."""
    if not _LOG_ENABLED:
        return
    offset = _pending.pop(chat_id, None)
    if offset is None:
        return                              # record not written in this session — skip

    async with _lock:
        try:
            with open(_LOG_PATH, "r+b") as fh:
                fh.seek(offset)
                raw = fh.readline()
                record = json.loads(raw.decode("utf-8"))
                record["self_eval_score"] = self_eval_score
                patched = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
                if len(patched) == len(raw):
                    # Same byte length → safe in-place overwrite
                    fh.seek(offset)
                    fh.write(patched)
                else:
                    # Length differs (shouldn't happen since score was None → int,
                    # but fall back to append with correction marker)
                    fh.seek(0, 2)
                    correction = {"_correction": True, "chat_id": chat_id,
                                  "self_eval_score": self_eval_score}
                    fh.write((json.dumps(correction) + "\n").encode("utf-8"))
        except Exception as exc:
            logger.debug("policy_log: update failed for %s: %s", chat_id, exc)

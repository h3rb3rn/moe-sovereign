"""services/rlsf_local_loop.py — Daily RLSF Closed Loop runner.

Evaluates dynamic routing decisions using local Qwen3.6:35b as judge, updates
Valkey Thompson sampling counters, and patches the self_eval_score in the logs
and PostgreSQL routing_telemetry database.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import psycopg
from psycopg_pool import AsyncConnectionPool
import redis.asyncio as redis
from langchain_core.messages import SystemMessage, HumanMessage

import config
import state
from services.inference import _record_expert_outcome
from telemetry import record_self_score

logger = logging.getLogger("moe.rlsf_loop")

_CONFIG_PATH = Path("/app/cleanup-config.json")
_HISTORY_PATH = Path("/app/cleanup-history.jsonl")
_LOG_PATH = Path(os.getenv("POLICY_LOG_PATH", "/app/logs/policy_training.jsonl"))
_AUDIT_LOG_DIR = Path("/app/logs/users")
_DPO_LOG_PATH = Path("/app/logs/dpo_preference_pairs.jsonl")

EVAL_SYSTEM_PROMPT = """You are the MoE Sovereign Meta-Policy Judge.
Your task is to evaluate whether the dynamic routing selection of expert models for a given user query was optimal, sub-optimal, or incorrect.

Evaluation Scale:
1 = Sub-optimal/Incorrect: The wrong expert(s) were selected (e.g. creative writer for python code), or extreme over-escalation (35B models called for trivial greetings), or latency/cost was completely disproportionate.
3 = Adequate: The experts selected were capable, but a more efficient path (e.g. local 7B, or fast-path cache) could have been used.
5 = Optimal: The perfect balance of cost, complexity, and expert capabilities (e.g. code reviewer only for coding, 35B reasoning only for hard math/logic).

Respond ONLY with a JSON object in this format (no markdown blocks, no conversational preamble):
{
  "score": 1-5,
  "optimal": true/false,
  "rationale": "Brief reason for the score",
  "better_routing": ["expert_name_1", ...] or null
}"""


def _read_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to read cleanup-config.json: %s", e)
        return {}


def is_enabled() -> bool:
    cfg = _read_config().get("rlsf_local_loop", {})
    return bool(cfg.get("enabled", True))


async def find_unevaluated_chats() -> list[dict]:
    """Parse policy_training.jsonl and identify entries missing self_eval_score."""
    if not _LOG_PATH.exists():
        logger.info("Policy log path %s does not exist", _LOG_PATH)
        return []

    records: dict[str, dict] = {}
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    chat_id = data.get("chat_id")
                    if not chat_id:
                        continue
                    # If it's a correction, apply the score update to the base record
                    if data.get("_correction"):
                        if chat_id in records:
                            records[chat_id]["self_eval_score"] = data.get("self_eval_score")
                    else:
                        records[chat_id] = data
                except Exception as e:
                    logger.debug("Failed to parse log line: %s", e)
    except Exception as e:
        logger.warning("Error reading policy training log: %s", e)
        return []

    unevaluated = [r for r in records.values() if r.get("self_eval_score") is None]
    # Sort by timestamp descending to process most recent first
    unevaluated.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return unevaluated


def index_user_audit_logs(target_chat_ids: set[str]) -> dict[str, tuple[str, str]]:
    """Scan recent user audit logs and index query prompts and responses."""
    indexed = {}
    if not _AUDIT_LOG_DIR.exists():
        logger.warning("Audit log directory %s does not exist", _AUDIT_LOG_DIR)
        return indexed

    # Search through all .jsonl files in the users log directory
    for f in _AUDIT_LOG_DIR.glob("*.jsonl"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        req_id = data.get("request_id")
                        if req_id and req_id in target_chat_ids:
                            # Extract prompt from messages
                            messages = data.get("messages", [])
                            prompt = ""
                            for msg in reversed(messages):
                                if msg.get("role") == "user":
                                    prompt = msg.get("content", "")
                                    break
                            response = data.get("response", "")
                            indexed[req_id] = (prompt, response)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Error reading audit log file %s: %s", f.name, e)
            
    return indexed


def _extract_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


async def evaluate_chat(
    chat_id: str,
    record: dict,
    prompt: str,
    response: str,
    judge_llm: Any
) -> Optional[dict]:
    """Call the Judge LLM to evaluate the routing decision."""
    experts_called = record.get("experts_called", [])
    plan_cats = record.get("plan_categories", [])
    complexity = record.get("complexity", "unknown")
    latency_s = record.get("latency_s", 0)

    user_content = (
        f"USER QUERY: {prompt[:1500]}\n\n"
        f"PLAN CATEGORIES: {plan_cats}\n"
        f"EXPERTS CALLED: {experts_called}\n"
        f"COMPLEXITY: {complexity}\n"
        f"LATENCY: {latency_s}s\n\n"
        f"FINAL RESPONSE (truncated): {response[:2000]}"
    )

    try:
        res = await judge_llm.ainvoke([
            SystemMessage(content=EVAL_SYSTEM_PROMPT),
            HumanMessage(content=user_content)
        ])
        eval_data = _extract_json(res.content)
        if eval_data:
            eval_data["chat_id"] = chat_id
            return eval_data
        else:
            logger.warning("Judge returned non-JSON response: %s", res.content)
    except Exception as e:
        logger.error("Failed to invoke judge for chat %s: %s", chat_id, e)
    return None


async def run_rlsf_loop(batch_size: int = 100, dry_run: bool = False) -> dict:
    """Run the main reinforcement learning feedback loop."""
    start_time = time.time()
    ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 1. Gather unevaluated entries
    unevaluated = await find_unevaluated_chats()
    if not unevaluated:
        return {
            "job": "rlsf_local_loop",
            "ts": ts_str,
            "duration_s": int(time.time() - start_time),
            "freed_bytes": 0,
            "details": {"message": "No unevaluated chats found."}
        }

    # Limit to batch size
    batch = unevaluated[:batch_size]
    target_ids = {r["chat_id"] for r in batch}
    logger.info("Found %d unevaluated chats. Processing batch of %d", len(unevaluated), len(batch))

    # 2. Index query & response from user audit logs
    audit_index = index_user_audit_logs(target_ids)

    # 3. Setup dependencies
    # Connect to database
    pg_url = os.environ.get("MOE_USERDB_URL")
    redis_url = os.environ.get("REDIS_URL")
    
    if not pg_url or not redis_url:
        logger.error("MOE_USERDB_URL or REDIS_URL not configured")
        return {"error": "Missing environment config"}

    # Initialize OpenAI Judge client
    from services.llm_instances import judge_llm

    evaluated_count = 0
    updated_counters = 0
    dpo_pairs_generated = 0
    errors = []

    # Run connection setup
    async with AsyncConnectionPool(conninfo=pg_url) as pool:
        # Create standalone redis client if state client not initialized
        if state.redis_client is None:
            state.redis_client = redis.from_url(redis_url, decode_responses=True)
            created_redis = True
        else:
            created_redis = False

        try:
            for record in batch:
                chat_id = record["chat_id"]
                # Look up prompt and response
                prompt_res = audit_index.get(chat_id)
                if not prompt_res:
                    # Try to fall back to Redis
                    try:
                        r_meta = await state.redis_client.hgetall(f"moe:response:{chat_id}")
                        if r_meta:
                            prompt = r_meta.get("input", "")
                            response = "[Full answer unavailable; retrieved from Valkey metadata cache]"
                            prompt_res = (prompt, response)
                    except Exception:
                        pass

                if not prompt_res:
                    logger.debug("Skipping chat %s: Prompt/response not found in audit logs or cache", chat_id)
                    continue

                prompt, response = prompt_res
                # Run evaluation
                eval_res = await evaluate_chat(chat_id, record, prompt, response, judge_llm)
                if not eval_res:
                    continue

                score = int(eval_res.get("score", 3))
                optimal = bool(eval_res.get("optimal", True))
                better_routing = eval_res.get("better_routing")

                evaluated_count += 1

                if not dry_run:
                    # A. Update Thompson sampling counters in Valkey
                    experts_called = record.get("experts_called", [])
                    for model_cat in experts_called:
                        if "::" in model_cat:
                            model, cat = model_cat.split("::", 1)
                            # If optimal (score >= 4), increment positive. Otherwise increment negative.
                            is_positive = (score >= 4)
                            await _record_expert_outcome(model, cat, is_positive)
                            updated_counters += 1

                    # B. Update PostgreSQL routing_telemetry
                    try:
                        await record_self_score(
                            pool,
                            chat_id,
                            score,
                            template_name=record.get("template_id", ""),
                            complexity=record.get("complexity", "")
                        )
                    except Exception as pg_exc:
                        logger.warning("Failed to update PG self_score for %s: %s", chat_id, pg_exc)

                    # C. Write correction marker to policy training log
                    try:
                        correction = {"_correction": True, "chat_id": chat_id, "self_eval_score": score}
                        line = json.dumps(correction) + "\n"
                        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                            fh.write(line)
                    except Exception as log_exc:
                        logger.warning("Failed to append correction to policy log: %s", log_exc)

                    # D. If suboptimal and better routing suggested, generate DPO preference pair
                    if not optimal and better_routing and isinstance(better_routing, list):
                        try:
                            dpo_pair = {
                                "chat_id": chat_id,
                                "ts": time.time(),
                                "prompt": prompt,
                                "chosen_routing": better_routing,
                                "rejected_routing": experts_called,
                                "score": score,
                                "rationale": eval_res.get("rationale", "")
                            }
                            with open(_DPO_LOG_PATH, "a", encoding="utf-8") as dfh:
                                dfh.write(json.dumps(dpo_pair, ensure_ascii=False) + "\n")
                            dpo_pairs_generated += 1
                        except Exception as dpo_exc:
                            logger.warning("Failed to write DPO pair: %s", dpo_exc)

        finally:
            if created_redis:
                await state.redis_client.aclose()
                state.redis_client = None

    duration = int(time.time() - start_time)
    result = {
        "job": "rlsf_local_loop",
        "ts": ts_str,
        "duration_s": duration,
        "freed_bytes": 0,
        "details": {
            "evaluated_records": evaluated_count,
            "updated_counters": updated_counters,
            "dpo_pairs_generated": dpo_pairs_generated,
            "batch_size": batch_size,
            "dry_run": dry_run,
            "status": "success" if not errors else "partial_error",
            "errors": errors[:5]
        }
    }

    # Write job history record
    if not dry_run:
        try:
            with open(_HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(result) + "\n")
        except Exception as e:
            logger.warning("Failed to write history record: %s", e)

    return result


if __name__ == "__main__":
    # Setup logger
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    
    # Read batch size from cleanup-config.json
    cfg = _read_config().get("rlsf_local_loop", {})
    enabled = cfg.get("enabled", True)
    batch_sz = int(cfg.get("batch_size", 100))

    if not enabled:
        print("RLSF local loop is disabled in cleanup-config.json.")
        exit(0)

    print(f"Running RLSF local loop (batch_size={batch_sz})...")
    res = asyncio.run(run_rlsf_loop(batch_size=batch_sz))
    print(json.dumps(res, indent=2))

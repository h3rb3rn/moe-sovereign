"""routes/feedback.py — User feedback submission and memory ingest endpoints."""

import asyncio
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

import state
import telemetry as _telemetry
from metrics import PROM_FEEDBACK
from config import (
    FEEDBACK_POSITIVE_THRESHOLD,
    FEEDBACK_NEGATIVE_THRESHOLD,
    KAFKA_TOPIC_FEEDBACK,
    KAFKA_TOPIC_INGEST,
)
from services.kafka import _kafka_publish

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    response_id: str            # chat_id from the response ("chatcmpl-...")
    rating: int                 # 1-5  (1-2=negativ, 3=neutral, 4-5=positiv)
    correction: Optional[str] = None


class MemoryIngestRequest(BaseModel):
    """Request body for /v1/memory/ingest."""
    session_summary: str
    key_decisions: List[str] = []
    domain: str = "session"
    source_model: str = "claude-code-hook"
    confidence: float = 0.8


# ---------------------------------------------------------------------------
# Helpers (lazy-imported from main to avoid circular import)
# ---------------------------------------------------------------------------

async def _record_expert_outcome(model: str, category: str, positive: bool) -> None:
    import main as _m
    await _m._record_expert_outcome(model, category, positive)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/v1/feedback")
async def submit_feedback(req: FeedbackRequest):
    if not 1 <= req.rating <= 5:
        return {"status": "error", "message": "Rating must be between 1 and 5"}
    if state.redis_client is None:
        return {"status": "error", "message": "Valkey not available"}

    meta = await state.redis_client.hgetall(f"moe:response:{req.response_id}")
    if not meta:
        return {"status": "error", "message": "Response ID not found or expired"}

    # Decode bytes key/values if necessary
    def decode_val(k):
        v = meta.get(k)
        if v is None:
            k_bytes = k.encode("utf-8") if isinstance(k, str) else k
            v = meta.get(k_bytes)
        if isinstance(v, bytes):
            return v.decode("utf-8")
        return str(v) if v is not None else ""

    expert_models_used_str = decode_val("expert_models_used") or "[]"
    chroma_doc_id = decode_val("chroma_doc_id")
    user_input = decode_val("input")
    plan_cats_str = decode_val("plan_cats") or "[]"
    template_id = decode_val("template_id")
    causal_intervention_str = decode_val("causal_intervention")

    positive = req.rating >= FEEDBACK_POSITIVE_THRESHOLD
    negative = req.rating <= FEEDBACK_NEGATIVE_THRESHOLD

    PROM_FEEDBACK.observe(req.rating)

    for model_cat in json.loads(expert_models_used_str):
        if "::" in model_cat:
            model, cat = model_cat.split("::", 1)
            await _record_expert_outcome(model, cat, positive)

    # ─── Causal Credit Assignment ──────────────────────────────────────────
    if causal_intervention_str:
        import re as _re
        try:
            intervention = json.loads(causal_intervention_str)
            if intervention and intervention.get("is_intervention"):
                intervened_model = intervention.get("intervened")
                default_model = intervention.get("default")
                category = intervention.get("expert")
                actual_rating = req.rating
                
                # Query default model's historical average rating
                historical_avg = None
                if state._userdb_pool is not None:
                    try:
                        async with state._userdb_pool.connection() as conn:
                            async with conn.cursor() as cur:
                                like_pattern = f'%"{default_model}::{category}"%'
                                await cur.execute(
                                    "SELECT AVG(user_rating) FROM routing_telemetry WHERE user_rating IS NOT NULL AND experts_used::text LIKE %s",
                                    (like_pattern,)
                                )
                                row = await cur.fetchone()
                                if row and row[0] is not None:
                                    historical_avg = float(row[0])
                    except Exception:
                        pass
                
                if historical_avg is None:
                    # Fall back to Valkey estimation
                    try:
                        safe_default = _re.sub(r"[^a-zA-Z0-9_\-]", "_", default_model)
                        default_key = f"moe:perf:{safe_default}:{category}"
                        data = await state.redis_client.hgetall(default_key)
                        dtot = int(data.get(b"total", data.get("total", 0)))
                        if dtot > 0:
                            dpos = int(data.get(b"positive", data.get("positive", 0)))
                            dneg = int(data.get(b"negative", data.get("negative", 0)))
                            dneu = dtot - dpos - dneg
                            historical_avg = (dpos * 4.5 + dneg * 1.5 + dneu * 3.0) / dtot
                        else:
                            historical_avg = 3.0
                    except Exception:
                        historical_avg = 3.0
                
                # Calculate difference
                diff = actual_rating - historical_avg
                is_positive_reward = diff > 0
                
                # Apply reward/penalty to intervened model in Valkey moe:perf
                from services.inference import _record_expert_outcome as _rec_outcome
                await _rec_outcome(intervened_model, category, is_positive_reward)
        except Exception:
            pass

    if negative and chroma_doc_id and state.cache_collection is not None:
        try:
            await asyncio.to_thread(
                state.cache_collection.update,
                ids=[chroma_doc_id],
                metadatas=[{"flagged": True}],
            )
        except Exception:
            pass

    if state.graph_manager is not None:
        if negative and user_input:
            await state.graph_manager.mark_triples_unverified(user_input)
        elif positive and user_input:
            await state.graph_manager.verify_triples(user_input)

    # Update dynamic template feedback rating in Postgres
    if template_id and template_id.startswith("moe-dyn-"):
        from admin_ui.database import update_dynamic_template_feedback_rating
        try:
            await update_dynamic_template_feedback_rating(template_id, req.rating)
        except Exception as e:
            pass

        # Write to retraining dataset JSONL file if positive or negative
        if positive or negative:
            import os
            try:
                retraining_file = os.getenv("RETRAINING_DATASET_PATH", "/app/logs/retraining_dataset.jsonl")
                try:
                    os.makedirs(os.path.dirname(retraining_file), exist_ok=True)
                except Exception:
                    retraining_file = "logs/retraining_dataset.jsonl"
                    os.makedirs(os.path.dirname(retraining_file), exist_ok=True)
                
                cats = json.loads(plan_cats_str)
                sample = {
                    "prompt": user_input,
                    "expert_domains": cats,
                    "rating": req.rating,
                    "moe_mode": "default"
                }
                with open(retraining_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(sample, ensure_ascii=False) + "\n")
            except Exception as e:
                pass

    asyncio.create_task(
        _telemetry.record_user_feedback(state._userdb_pool, req.response_id, req.rating)
    )
    asyncio.create_task(_kafka_publish(KAFKA_TOPIC_FEEDBACK, {
        "response_id": req.response_id,
        "rating":      req.rating,
        "positive":    positive,
        "ts":          datetime.now().isoformat(),
    }))
    return {"status": "ok", "response_id": req.response_id,
            "rating": req.rating, "positive": positive}


@router.post("/v1/memory/ingest")
async def memory_ingest(request: Request, body: MemoryIngestRequest):
    """Accept a session summary from external hooks and enqueue for GraphRAG ingest."""
    combined = body.session_summary
    if body.key_decisions:
        combined += "\n\nKey decisions:\n" + "\n".join(f"- {d}" for d in body.key_decisions)
    asyncio.create_task(_kafka_publish(KAFKA_TOPIC_INGEST, {
        "input":             "Claude Code session summary",
        "answer":            combined[:4000],
        "domain":            body.domain,
        "source_expert":     "session",
        "source_model":      body.source_model,
        "confidence":        min(max(float(body.confidence), 0.0), 1.0),
        "knowledge_type":    "factual",
        "synthesis_insight": None,
    }))
    return {"status": "queued", "domain": body.domain, "length": len(combined)}

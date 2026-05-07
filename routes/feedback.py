"""routes/feedback.py — User feedback submission and memory ingest endpoints."""

import asyncio
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

import state
import telemetry as _telemetry
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

def _prom_feedback():
    import main as _m
    return _m.PROM_FEEDBACK


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

    positive = req.rating >= FEEDBACK_POSITIVE_THRESHOLD
    negative = req.rating <= FEEDBACK_NEGATIVE_THRESHOLD

    _prom_feedback().observe(req.rating)

    for model_cat in json.loads(meta.get("expert_models_used", "[]")):
        if "::" in model_cat:
            model, cat = model_cat.split("::", 1)
            await _record_expert_outcome(model, cat, positive)

    chroma_doc_id = meta.get("chroma_doc_id", "")
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
        user_input = meta.get("input", "")
        if negative and user_input:
            await state.graph_manager.mark_triples_unverified(user_input)
        elif positive and user_input:
            await state.graph_manager.verify_triples(user_input)

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

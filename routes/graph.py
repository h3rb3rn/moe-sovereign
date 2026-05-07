"""routes/graph.py — Knowledge graph inspection, export, and import endpoints."""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import state

router = APIRouter()


async def _kafka_publish(topic: str, payload: dict) -> None:
    """Fire-and-forget Kafka publish; no-op when producer is unavailable."""
    if state.kafka_producer is None:
        return
    try:
        data = json.dumps(payload).encode()
        await state.kafka_producer.send_and_wait(topic, data)
    except Exception:
        pass


@router.get("/graph/stats")
async def graph_stats():
    if state.graph_manager is None:
        return {"status": "unavailable"}
    stats = await state.graph_manager.get_stats()
    return {"status": "ok", **stats}


@router.get("/graph/search")
async def graph_search(q: str, limit: int = 10):
    if state.graph_manager is None:
        return {"status": "unavailable", "results": []}
    results = await state.graph_manager.search_entities(q, limit)
    return {"status": "ok", "query": q, "results": results}


@router.get("/graph/knowledge/export")
async def graph_knowledge_export(
    domains: Optional[str] = None,
    min_trust: float = 0.3,
    include_syntheses: bool = True,
    strip_sensitive: bool = True,
):
    """Export knowledge graph as a community-shareable JSON-LD bundle."""
    if state.graph_manager is None:
        return JSONResponse(status_code=503, content={"error": "GraphRAG unavailable"})
    domain_list = [d.strip() for d in domains.split(",") if d.strip()] if domains else None
    bundle = await state.graph_manager.export_knowledge_bundle(
        domains=domain_list,
        min_trust=min_trust,
        include_syntheses=include_syntheses,
        strip_sensitive=strip_sensitive,
    )
    filename = f"moe-knowledge-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return Response(
        content=json.dumps(bundle, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/graph/knowledge/import")
async def graph_knowledge_import(raw_request: Request):
    """Import a knowledge bundle into the graph."""
    if state.graph_manager is None:
        return JSONResponse(status_code=503, content={"error": "GraphRAG unavailable"})
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    bundle = body.get("bundle", body)
    dry_run    = body.get("dry_run", False)
    source_tag = body.get("source_tag", "community_import")
    trust_floor = float(body.get("trust_floor", 0.5))
    if "@context" not in bundle and "entities" not in bundle:
        return JSONResponse(status_code=400, content={"error": "Not a valid knowledge bundle"})
    stats = await state.graph_manager.import_knowledge_bundle(
        bundle=bundle,
        source_tag=source_tag,
        trust_floor=trust_floor,
        dry_run=dry_run,
        kafka_publish_fn=_kafka_publish,
    )
    return {"status": "ok", "dry_run": dry_run, **stats}


@router.post("/graph/knowledge/import/validate")
async def graph_knowledge_validate(raw_request: Request):
    """Dry-run import to preview what would be imported."""
    if state.graph_manager is None:
        return JSONResponse(status_code=503, content={"error": "GraphRAG unavailable"})
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    bundle = body.get("bundle", body)
    stats = await state.graph_manager.import_knowledge_bundle(
        bundle=bundle, dry_run=True,
    )
    return {"status": "ok", "dry_run": True, **stats}

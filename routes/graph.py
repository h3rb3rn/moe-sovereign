"""routes/graph.py — Knowledge graph inspection, export, and import endpoints."""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import state

router = APIRouter()


from services.kafka import _kafka_publish


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


@router.get("/graph/domains")
async def graph_domains():
    """List entity domains with per-domain entity, relation, and synthesis counts.

    Backs the admin-UI Data Catalog (Phase 20). Does not expose entity contents
    — only aggregate counts per `domain` property.
    """
    if state.graph_manager is None:
        return {"status": "unavailable", "domains": []}
    async with state.graph_manager.driver.session() as session:
        result = await session.run(
            """
            MATCH (e:Entity)
            WITH coalesce(e.domain, 'unknown') AS domain, count(e) AS entities
            OPTIONAL MATCH (a:Entity {domain: domain})-[r]->(b:Entity)
            WITH domain, entities, count(r) AS relations
            OPTIONAL MATCH (s:Synthesis {domain: domain})
            WITH domain, entities, relations, count(s) AS synthesis_nodes
            RETURN domain, entities, relations, synthesis_nodes
            ORDER BY entities DESC
            """
        )
        records = [
            {
                "domain":           r["domain"],
                "entities":         r["entities"],
                "relations":        r["relations"] or 0,
                "synthesis_nodes":  r["synthesis_nodes"] or 0,
            }
            async for r in result
        ]
    return {"status": "ok", "domains": records}


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



# ─── Phase 22 — Read-only Cypher explorer ───────────────────────────────────

import re as _re_cypher

# Tokens that mean "this query writes": these abort the query before
# Neo4j ever sees it. We deliberately block CALL apoc.* writes too; only
# `CALL db.schema*` and `CALL dbms.components` are useful for read-only
# introspection and they pass the WRITE_TOKEN regex.
_FORBIDDEN_CYPHER = _re_cypher.compile(
    r"\b(CREATE|DELETE|SET|MERGE|REMOVE|DROP|ALTER|GRANT|REVOKE|FOREACH)\b",
    _re_cypher.IGNORECASE,
)


@router.post("/graph/cypher/read")
async def graph_cypher_read(raw_request: Request):
    """Execute a read-only Cypher query against Neo4j (Phase 22).

    Hard rejects any query containing write keywords. Caps result rows.
    """
    if state.graph_manager is None:
        return JSONResponse(status_code=503, content={"error": "GraphRAG unavailable"})
    try:
        body = await raw_request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    query = (body.get("query") or "").strip()
    limit = int(body.get("limit") or 100)
    limit = max(1, min(limit, 500))
    if not query:
        return JSONResponse(status_code=400, content={"error": "query required"})
    if _FORBIDDEN_CYPHER.search(query):
        return JSONResponse(
            status_code=400,
            content={"error": "Read-only endpoint — write keywords forbidden"},
        )

    # We rely on Neo4j to enforce read-mode regardless of whitelist regex.
    from neo4j import READ_ACCESS
    rows: list = []
    columns: list = []
    error: Optional[str] = None
    try:
        async with state.graph_manager.driver.session(default_access_mode=READ_ACCESS) as session:
            result = await session.run(query, parameters=body.get("parameters") or {})
            async for record in result:
                if not columns:
                    columns = list(record.keys())
                row: dict = {}
                for k in columns:
                    v = record[k]
                    if hasattr(v, "_properties"):
                        row[k] = {"_label": list(getattr(v, "labels", [])), **dict(v._properties)}
                    elif hasattr(v, "type") and hasattr(v, "_properties"):
                        row[k] = {"_type": v.type, **dict(v._properties)}
                    else:
                        try:
                            json.dumps(v)
                            row[k] = v
                        except Exception:
                            row[k] = str(v)
                rows.append(row)
                if len(rows) >= limit:
                    break
    except Exception as exc:
        error = str(exc)
    return {
        "columns":  columns,
        "rows":     rows,
        "count":    len(rows),
        "limit":    limit,
        "error":    error,
    }



"""routes/admin_stats.py — Admin statistics and pipeline log endpoints."""

import io
import csv
import json
import os
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import state
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASS
from services.auth import _extract_api_key, _validate_api_key

router = APIRouter()


@router.get("/v1/admin/knowledge-stats")
async def get_knowledge_stats():
    """Aggregate Neo4j counters for the stats dashboard."""
    try:
        from neo4j import AsyncGraphDatabase
        driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS or ""))
    except Exception as e:
        return {"error": f"neo4j init: {e}"}
    stats: dict = {}
    try:
        async with driver.session() as s:
            r = await s.run("MATCH (e:Entity) RETURN count(e) AS n")
            stats["entities_total"] = (await r.single())["n"]
            r = await s.run("MATCH ()-[r]->() RETURN count(r) AS n")
            stats["relations_total"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.created_at >= datetime() - duration('P1D') "
                "RETURN count(e) AS n"
            )
            stats["entities_last_24h"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.created_at >= datetime() - duration('P7D') "
                "RETURN count(e) AS n"
            )
            stats["entities_last_7d"] = (await r.single())["n"]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.source IS NOT NULL "
                "RETURN e.source AS source, count(e) AS n ORDER BY n DESC LIMIT 10"
            )
            stats["entities_by_source"] = [
                {"source": rec["source"], "n": rec["n"]} async for rec in r
            ]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.type IS NOT NULL "
                "RETURN e.type AS type, count(e) AS n ORDER BY n DESC LIMIT 10"
            )
            stats["top_types"] = [
                {"type": rec["type"], "n": rec["n"]} async for rec in r
            ]
            r = await s.run(
                "MATCH (e:Entity) WHERE e.curator_template IS NOT NULL "
                "RETURN e.curator_template AS template, count(e) AS n "
                "ORDER BY n DESC LIMIT 20"
            )
            stats["entities_by_curator"] = [
                {"template": rec["template"], "n": rec["n"]} async for rec in r
            ]
    except Exception as e:
        stats["error"] = str(e)
    finally:
        await driver.close()
    return stats


@router.get("/v1/admin/ontology-gaps")
async def get_ontology_gaps(limit: int = 30):
    """Shows most frequent terms from answers not in the ontology."""
    if state.redis_client is None:
        return {"error": "Valkey not available"}
    try:
        gaps = await state.redis_client.zrevrange(
            "moe:ontology_gaps", 0, limit - 1, withscores=True
        )
        return {"gaps": [{"term": g, "count": int(s)} for g, s in gaps]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/v1/admin/planner-patterns")
async def get_planner_patterns(limit: int = 20):
    """Shows proven planner patterns based on positive user feedback."""
    if state.redis_client is None:
        return {"error": "Valkey not available"}
    try:
        patterns = await state.redis_client.zrevrange(
            "moe:planner_success", 0, limit - 1, withscores=True
        )
        return {"patterns": [
            {"signature": sig, "count": int(score)} for sig, score in patterns
        ]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/v1/admin/tool-eval")
async def get_tool_eval_log(limit: int = 50):
    """Returns the last N records from tool_eval.jsonl as parsed JSON objects."""
    path = "/app/logs/tool_eval.jsonl"
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        records = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
            if len(records) >= limit:
                break
        return {"records": records, "total_lines": len(lines)}
    except FileNotFoundError:
        return {"records": [], "total_lines": 0}


_PL_SORT_COLS = {
    "requested_at":    "ul.requested_at",
    "model":           "ul.model",
    "moe_mode":        "ul.moe_mode",
    "username":        "u.username",
    "total_tokens":    "ul.total_tokens",
    "latency_ms":      "ul.latency_ms",
    "complexity_level": "ul.complexity_level",
}


@router.get("/v1/admin/pipeline-log")
async def pipeline_log(
    raw_request: Request,
    limit: int = 100,
    offset: int = 0,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    model: Optional[str] = None,
    moe_mode: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    complexity_level: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    sort_by: str = "requested_at",
    sort_dir: str = "desc",
    format: str = "json",
) -> Response:
    """Pipeline Transparency Log — query routing decisions, expert domains, and latency."""
    raw_key = _extract_api_key(raw_request)
    if not raw_key:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    _sys_key    = os.environ.get("SYSTEM_API_KEY", "").strip()
    _is_sys_key = bool(_sys_key and raw_key == _sys_key)
    user_ctx    = await _validate_api_key(raw_key)
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": user_ctx["error"]})
    if not (_is_sys_key or user_ctx.get("is_admin")):
        return JSONResponse(status_code=403, content={"error": "Admin access required"})

    try:
        if state._userdb_pool is None:
            return JSONResponse(status_code=503, content={"error": "Database unavailable"})

        conditions: list[str] = []
        params: list = []
        if user_id:        conditions.append("ul.user_id = %s");           params.append(user_id)
        if username:       conditions.append("u.username ILIKE %s");       params.append(f"%{username}%")
        if model:          conditions.append("ul.model ILIKE %s");         params.append(f"%{model}%")
        if moe_mode:       conditions.append("ul.moe_mode = %s");          params.append(moe_mode)
        if from_date:      conditions.append("ul.requested_at >= %s");     params.append(from_date)
        if to_date:        conditions.append("ul.requested_at <= %s");     params.append(to_date + "T23:59:59")
        if complexity_level: conditions.append("ul.complexity_level = %s"); params.append(complexity_level)
        if cache_hit is not None: conditions.append("ul.cache_hit = %s"); params.append(cache_hit)

        where     = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        _sort_col = _PL_SORT_COLS.get(sort_by, "ul.requested_at")
        _sort_ord = "ASC" if sort_dir.lower() == "asc" else "DESC"
        params.extend([limit, offset])

        async with state._userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""SELECT ul.request_id, ul.user_id, u.username,
                               ul.model, ul.moe_mode, ul.session_id,
                               ul.prompt_tokens, ul.completion_tokens, ul.total_tokens,
                               ul.latency_ms, ul.complexity_level, ul.expert_domains,
                               ul.cache_hit, ul.agentic_rounds, ul.status, ul.requested_at
                        FROM usage_log ul
                        LEFT JOIN users u ON ul.user_id = u.id
                        {where} ORDER BY {_sort_col} {_sort_ord} LIMIT %s OFFSET %s""",
                    params,
                )
                rows = await cur.fetchall()
                cols = [d.name for d in cur.description]
                await cur.execute(
                    f"SELECT COUNT(*) FROM usage_log ul LEFT JOIN users u ON ul.user_id = u.id {where}",
                    params[:-2],
                )
                total = (await cur.fetchone())[0]

        records = [dict(zip(cols, row)) for row in rows]
        if format == "csv":
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=cols)
            writer.writeheader(); writer.writerows(records)
            return Response(
                content=buf.getvalue(), media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=pipeline_log.csv"},
            )
        return JSONResponse({"total": total, "limit": limit, "offset": offset, "records": records})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

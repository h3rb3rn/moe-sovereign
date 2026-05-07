"""routes/admin_stats.py — Read-only admin statistics endpoints.

All handlers here are side-effect-free lookups that need at most Redis
or a direct Neo4j connection. No auth required (admin-UI internal use).
"""

import json
import os

from fastapi import APIRouter

import state
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASS

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

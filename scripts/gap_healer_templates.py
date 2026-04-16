#!/usr/bin/env python3
"""Ontology Gap Healer — via MoE Expert Templates with online learning.

Architecture:
- Uses the MoE Sovereign API (NOT direct Ollama)
- Rotates round-robin across 7 per-node templates:
    moe-ontology-curator-n04-rtx
    moe-ontology-curator-n06-m10-01
    moe-ontology-curator-n06-m10-02
    moe-ontology-curator-n06-m10-03
    moe-ontology-curator-n06-m10-04
    moe-ontology-curator-n07-gt
    moe-ontology-curator-n09-m60
    moe-ontology-curator-n11-m10-01
    moe-ontology-curator-n11-m10-02
    moe-ontology-curator-n11-m10-03
    moe-ontology-curator-n11-m10-04
- Each template pins planner/experts/judge to one GPU node → warm cache
- GraphRAG context injected automatically per request (learning loop)
- Confidence threshold filters out hallucinations

Learning mechanism:
- Every approved entity written to Neo4j via MoE ingest pipeline
- Next gap's GraphRAG lookup finds the freshly-written entities
- Context grows over time — implicit online learning

Compliance:
- All inference via MoE API (http://localhost:8000/v1/chat/completions)
- No direct Ollama calls (those are reserved for monitoring/pull/unload)
"""
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import redis.asyncio as redis
from neo4j import AsyncGraphDatabase


# ─── Configuration ────────────────────────────────────────────────────────────

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j-knowledge:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASSWORD", "") or os.environ.get("NEO4J_PASS", "")

MOE_API_BASE = os.environ.get("MOE_API_BASE", "http://localhost:8000")
MOE_API_KEY = os.environ.get("MOE_API_KEY", "")


def _auth_headers() -> dict:
    """Build headers, omitting Bearer if no API key is configured."""
    h = {"Content-Type": "application/json"}
    if MOE_API_KEY:
        h["Authorization"] = f"Bearer {MOE_API_KEY}"
    return h

REDIS_URL = os.environ.get("REDIS_URL", "")
GAP_ZSET_KEY = "moe:ontology_gaps"

BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "20"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))
MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "0.5"))
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1000"))
RUN_ONCE = "--once" in sys.argv
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "600"))

# Round-robin pool of pinned-node templates. Each template keeps all its
# inference on one physical node for warm-cache throughput.
_STATIC_FALLBACK_POOL = [
    "moe-ontology-curator-n04-rtx",
    "moe-ontology-curator-n06-m10-01",
    "moe-ontology-curator-n06-m10-02",
    "moe-ontology-curator-n06-m10-03",
    "moe-ontology-curator-n06-m10-04",
    "moe-ontology-curator-n07-gt",
    "moe-ontology-curator-n09-m60",
    "moe-ontology-curator-n11-m10-01",
    "moe-ontology-curator-n11-m10-02",
    "moe-ontology-curator-n11-m10-03",
    "moe-ontology-curator-n11-m10-04",
]


ONTOLOGY_ENABLED_SERVERS_KEY = "moe:ontology:enabled_servers"


def _discover_pool_from_env() -> list[str]:
    """Fallback: build pool from the INFERENCE_SERVERS env var at process start.

    Stale after admin UI toggles until orchestrator is restarted — kept only as
    a safety net. Prefer the Redis-backed discovery in `_discover_pool_async`.
    """
    override = os.environ.get("TEMPLATE_POOL", "").strip()
    if override:
        return [t.strip() for t in override.split(",") if t.strip()]

    raw = os.environ.get("INFERENCE_SERVERS", "").strip()
    if not raw:
        return list(_STATIC_FALLBACK_POOL)
    try:
        servers = json.loads(raw)
    except json.JSONDecodeError:
        return list(_STATIC_FALLBACK_POOL)

    pool = []
    for s in servers:
        if not isinstance(s, dict) or not s.get("ontology_enabled"):
            continue
        name = (s.get("name") or "").strip().lower()
        if name:
            pool.append(f"moe-ontology-curator-{name}")
    return pool or list(_STATIC_FALLBACK_POOL)


async def _discover_pool_async(redis_cli) -> list[str]:
    """Pick the template pool live from Redis, written by the admin UI on every
    ontology toggle. Falls back to env parsing if Redis is empty/unavailable.

    TEMPLATE_POOL env override still wins for reproducibility.
    """
    override = os.environ.get("TEMPLATE_POOL", "").strip()
    if override:
        return [t.strip() for t in override.split(",") if t.strip()]

    if redis_cli is not None:
        try:
            names = await redis_cli.smembers(ONTOLOGY_ENABLED_SERVERS_KEY)
            if names:
                return [f"moe-ontology-curator-{n.lower()}" for n in sorted(names)]
        except Exception:
            pass
    return _discover_pool_from_env()


TEMPLATE_POOL: list[str] = []  # populated in main() once Redis is reachable


USER_PROMPT = """Classify this unknown term for our knowledge graph:

TERM: "{term}"

{context_section}

The Judge will return a JSON entity entry. Do not respond with prose outside the JSON."""


async def fetch_gaps(client: httpx.AsyncClient, redis_cli=None) -> list[str]:
    """Pull top ontology gaps. Prefer Valkey (no HTTP dependency on busy orchestrator),
    fall back to the admin API."""
    if redis_cli is not None:
        try:
            rows = await redis_cli.zrevrange(GAP_ZSET_KEY, 0, BATCH_SIZE - 1, withscores=False)
            if rows:
                return rows if isinstance(rows[0], str) else [r.decode() for r in rows]
        except Exception as e:
            print(f"  [!] Valkey ZREVRANGE failed: {e}", flush=True)
    try:
        r = await client.get(
            f"{MOE_API_BASE}/v1/admin/ontology-gaps",
            headers=_auth_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            return [g["term"] for g in r.json().get("gaps", [])[:BATCH_SIZE]]
    except Exception as e:
        print(f"  [!] fetch_gaps admin API failed: {e}", flush=True)
    return []


async def fetch_graph_context(driver, term: str) -> str:
    """Find related entities already in Neo4j for this term."""
    async with driver.session() as s:
        result = await s.run(
            """
            MATCH (e:Entity)
            WHERE e.name IS NOT NULL
              AND NOT valueType(e.name) STARTS WITH 'LIST'
              AND (toLower(e.name) CONTAINS toLower($term)
                   OR toLower($term) CONTAINS toLower(e.name))
            RETURN e.name AS name, coalesce(e.type, 'Unknown') AS type
            LIMIT 5
            """,
            term=term,
        )
        rows = [record async for record in result]
    if not rows:
        return ""
    lines = [f"  - {r['name']} ({r['type']})" for r in rows]
    return "Related entities already in the graph:\n" + "\n".join(lines)


def parse_entity(content: str) -> Optional[dict]:
    """Extract a valid entity JSON from the MoE response content."""
    if not content:
        return None
    # Strip markdown fences if present
    c = re.sub(r"^```\w*\n?", "", content.strip())
    c = re.sub(r"\n?```$", "", c.strip())
    # Find first {...} block
    depth = 0
    start = None
    for i, ch in enumerate(c):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(c[start:i + 1])
                    if isinstance(obj, dict) and obj.get("name"):
                        return obj
                except json.JSONDecodeError:
                    pass
                start = None
    return None


async def classify_via_template(
    client: httpx.AsyncClient, template: str, term: str, context: str,
) -> Optional[dict]:
    """Send the term through the given MoE template and parse the result."""
    t0 = time.time()
    ctx_section = context if context else ""
    body = {
        "model": template,
        "messages": [
            {"role": "user", "content": USER_PROMPT.format(
                term=term, context_section=ctx_section,
            )},
        ],
        "stream": False,
        "max_tokens": 500,
        "temperature": 0.1,
    }
    try:
        r = await client.post(
            f"{MOE_API_BASE}/v1/chat/completions",
            headers=_auth_headers(),
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        dt = time.time() - t0
        if r.status_code != 200:
            print(f"  [{template[-10:]}] '{term}': HTTP {r.status_code} in {dt:.1f}s",
                  flush=True)
            return None

        content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        entity = parse_entity(content)
        if entity:
            conf = entity.get("confidence", 0.5)
            marker = "✓" if conf >= MIN_CONFIDENCE else "?"
            print(f"  [{template[-10:]}] {marker} '{term}' → "
                  f"{entity.get('entity_type','?')} (conf={conf:.2f}) in {dt:.1f}s",
                  flush=True)
            return entity
        print(f"  [{template[-10:]}] ✗ '{term}': could not parse JSON in {dt:.1f}s",
              flush=True)
        return None
    except asyncio.TimeoutError:
        print(f"  [{template[-10:]}] ✗ '{term}': TIMEOUT after {time.time()-t0:.1f}s",
              flush=True)
        return None
    except Exception as e:
        print(f"  [{template[-10:]}] ✗ '{term}': {type(e).__name__}: {str(e)[:80]}",
              flush=True)
        return None


async def write_entity(driver, entity: dict, source_term: str) -> bool:
    """Persist the classified entity to Neo4j (learning step)."""
    if not entity or not entity.get("name"):
        return False
    if entity.get("entity_type") == "Uncertain":
        return False  # Never pollute the graph with uncertain entries

    async with driver.session() as s:
        try:
            await s.run(
                """
                MERGE (e:Entity {name: $name})
                ON CREATE SET
                    e.type = $type,
                    e.source = 'gap_healer_templates',
                    e.created_at = datetime(),
                    e.description_en = $desc_en,
                    e.description_de = $desc_de,
                    e.aliases = $aliases,
                    e.confidence = $confidence,
                    e.source_term = $source_term,
                    e.curator_template = $template
                ON MATCH SET
                    e.last_updated = datetime(),
                    e.description_en = coalesce(e.description_en, $desc_en),
                    e.description_de = coalesce(e.description_de, $desc_de)
                """,
                name=entity["name"],
                type=entity["entity_type"],
                desc_en=entity.get("description_en", ""),
                desc_de=entity.get("description_de", ""),
                aliases=entity.get("aliases", []),
                confidence=float(entity.get("confidence", 0.5)),
                source_term=source_term,
                template=entity.get("_template", ""),
            )
            # Write relations if present
            for rel in entity.get("relations", []):
                rel_type = (rel.get("type") or "RELATED_TO").upper()
                target = rel.get("target")
                if not target:
                    continue
                try:
                    await s.run(
                        """
                        MERGE (a:Entity {name: $from_name})
                        MERGE (b:Entity {name: $to_name})
                        MERGE (a)-[r:""" + rel_type + """]->(b)
                        SET r.source = 'gap_healer_templates',
                            r.healed_at = datetime()
                        """,
                        from_name=entity["name"], to_name=target,
                    )
                except Exception:
                    pass  # Invalid rel_type, skip
            return True
        except Exception as e:
            print(f"  [neo4j] Write failed: {e}", flush=True)
            return False


async def process_gap(
    idx: int, total: int, term: str,
    client: httpx.AsyncClient, driver, template: str,
    redis_cli,
) -> Optional[dict]:
    """Full pipeline for one gap: context lookup → classify → write → remove from queue."""
    ctx = await fetch_graph_context(driver, term)
    entity = await classify_via_template(client, template, term, ctx)
    resolved = False
    if entity:
        entity["_template"] = template
        entity["_source_term"] = term
        etype = entity.get("entity_type", "")
        conf = entity.get("confidence", 0)
        if etype and etype != "Uncertain" and conf >= MIN_CONFIDENCE:
            if await write_entity(driver, entity, term):
                resolved = True
    if resolved and redis_cli is not None:
        try:
            await redis_cli.zrem(GAP_ZSET_KEY, term)
        except Exception as e:
            print(f"  [redis] ZREM '{term}' failed: {e}", flush=True)
    return entity


async def process_batch(gaps: list[str], client, driver, stats: dict, redis_cli):
    """Run gaps through the template pool with semaphore-limited concurrency."""
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _run(idx: int, term: str):
        template = TEMPLATE_POOL[(idx - 1) % len(TEMPLATE_POOL)]
        async with semaphore:
            entity = await process_gap(idx, len(gaps), term, client, driver, template, redis_cli)
            node_key = template.split("-")[-2] + "-" + template.split("-")[-1]
            stats.setdefault(node_key, {"processed": 0, "written": 0, "uncertain": 0, "failed": 0})
            s = stats[node_key]
            if entity is None:
                s["failed"] += 1
            else:
                s["processed"] += 1
                if entity.get("entity_type") == "Uncertain":
                    s["uncertain"] += 1
                elif entity.get("confidence", 0) >= MIN_CONFIDENCE:
                    s["written"] += 1

    await asyncio.gather(
        *[_run(i, t) for i, t in enumerate(gaps, 1)],
        return_exceptions=True,
    )


async def main() -> int:
    print("=" * 70, flush=True)
    print("Ontology Gap Healer — via MoE Expert Templates", flush=True)
    print(f"  API:           {MOE_API_BASE}", flush=True)
    print(f"  Concurrency:   {CONCURRENCY}", flush=True)
    print(f"  Batch size:    {BATCH_SIZE}", flush=True)
    print(f"  Min confidence: {MIN_CONFIDENCE}", flush=True)
    print("=" * 70, flush=True)

    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    redis_cli = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
    if redis_cli:
        try:
            await redis_cli.ping()
            print("  Valkey:         connected (ZREM on resolve enabled)", flush=True)
        except Exception as e:
            print(f"  Valkey:         ⚠ not available ({e}) — gaps will not auto-clear", flush=True)
            redis_cli = None

    global TEMPLATE_POOL
    TEMPLATE_POOL = await _discover_pool_async(redis_cli)
    if not TEMPLATE_POOL:
        TEMPLATE_POOL = list(_STATIC_FALLBACK_POOL)
    print(f"  Templates:     {len(TEMPLATE_POOL)} (round-robin)", flush=True)
    for t in TEMPLATE_POOL:
        print(f"    - {t}", flush=True)
    totals = {"processed": 0, "written": 0, "uncertain": 0, "failed": 0}
    iteration = 0
    t_start = time.time()

    async with httpx.AsyncClient() as client:
        while iteration < MAX_ITERATIONS:
            iteration += 1
            gaps = await fetch_gaps(client, redis_cli)
            if not gaps:
                print(f"\n[{time.strftime('%H:%M:%S')}] No more gaps. Done.", flush=True)
                break

            print(f"\n[{time.strftime('%H:%M:%S')}] Iteration {iteration}: "
                  f"{len(gaps)} gaps via template rotation", flush=True)
            t_batch = time.time()
            batch_stats: dict = {}
            await process_batch(gaps, client, driver, batch_stats, redis_cli)
            dt_batch = time.time() - t_batch

            for node_key, s in batch_stats.items():
                for key in ("processed", "written", "uncertain", "failed"):
                    totals[key] += s[key]
                print(f"    {node_key}: {s['processed']} proc, "
                      f"{s['written']} writ, {s['uncertain']} unc, {s['failed']} fail",
                      flush=True)
            print(f"[{time.strftime('%H:%M:%S')}] Batch done in {dt_batch:.1f}s — "
                  f"totals: {totals['written']} written / {totals['processed']} processed",
                  flush=True)

            if RUN_ONCE:
                print("[--once] Exiting after one iteration.", flush=True)
                break

    await driver.close()
    if redis_cli:
        try:
            await redis_cli.aclose()
        except Exception:
            pass
    dt_total = time.time() - t_start
    print("\n" + "=" * 70, flush=True)
    print("FINAL:", flush=True)
    print(f"  Iterations:    {iteration}", flush=True)
    print(f"  Processed:     {totals['processed']}", flush=True)
    print(f"  Written:       {totals['written']}", flush=True)
    print(f"  Uncertain:     {totals['uncertain']}", flush=True)
    print(f"  Failed:        {totals['failed']}", flush=True)
    print(f"  Duration:      {dt_total/60:.1f} min", flush=True)
    if totals["processed"]:
        print(f"  Throughput:    {totals['processed']/dt_total*60:.1f} gaps/min",
              flush=True)
    print("=" * 70, flush=True)
    return 0


if __name__ == "__main__":
    asyncio.run(main())

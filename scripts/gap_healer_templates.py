#!/usr/bin/env python3
"""Ontology Gap Healer — via MoE Expert Templates with online learning.

Architecture:
- Uses the MoE Sovereign API (NOT direct Ollama)
- Rotates round-robin across per-node templates:
    moe-ontology-curator-n04-rtx
    moe-ontology-curator-n06-m10-01..04
    moe-ontology-curator-n07-gt
    moe-ontology-curator-n09-m60
    moe-ontology-curator-n11-m10-01..04
- Each template pins planner/experts/judge to one GPU node → warm cache
- GraphRAG context injected automatically per request (learning loop)
- Confidence threshold filters out hallucinations

Concurrency model (per-node slot limiting):
- Each physical node gets its own asyncio.Semaphore + Redis active counter.
- Cold-start (< MIN_RUNS_CALIBRATE successful runs): exactly 1 slot per node.
- After every MIN_RUNS_CALIBRATE successes: unlock one additional slot.
- Each node type has a hardware-specific absolute cap (see _NODE_MAX_SLOTS).
- Redis counters (moe:healer:active:{node}) allow multiple healer processes
  to coordinate so they never collectively exceed the per-node cap.
- Gaps are claimed atomically via ZPOPMAX before processing and re-enqueued
  with lower priority on failure, preventing double-processing.

Learning mechanism:
- Every approved entity written to Neo4j via MoE ingest pipeline
- Next gap's GraphRAG lookup finds the freshly-written entities
- Context grows over time — implicit online learning
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

BATCH_SIZE     = int(os.environ.get("BATCH_SIZE",      "20"))
CONCURRENCY    = int(os.environ.get("CONCURRENCY",      "4"))   # global ceiling
MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "0.5"))
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1000"))
RUN_ONCE       = "--once" in sys.argv
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "600"))

# How many successful runs before unlocking an additional slot on a node.
MIN_RUNS_CALIBRATE = int(os.environ.get("MIN_RUNS_CALIBRATE", "5"))

# Absolute concurrent-task caps per node type.
# These are hardware-informed upper bounds — the actual allowed count starts
# at 1 and grows progressively as calibration data is collected.
_NODE_MAX_SLOTS: dict[str, int] = {
    "n04-rtx":    4,   # RTX 3060 — fast, 12 GB VRAM
    "n06-m10-01": 3,   # M10 — 8 GB VRAM per shard, decent throughput
    "n06-m10-02": 3,
    "n06-m10-03": 3,
    "n06-m10-04": 3,
    "n07-gt":     2,   # Lower-power GT — conservative
    "n09-m60":    1,   # Tesla M60 — slow, single-threaded inference only
    "n11-m10-01": 3,
    "n11-m10-02": 3,
    "n11-m10-03": 3,
    "n11-m10-04": 3,
}
_DEFAULT_MAX_SLOTS = 2

# Redis keys for per-node coordination
_NODE_ACTIVE_PREFIX = "moe:healer:active:"   # int: tasks currently running on node
_NODE_RUNS_PREFIX   = "moe:healer:runs:"     # int: total successful completions per node
_NODE_ACTIVE_TTL    = 3600                   # safety expiry so crashes don't freeze the system

# Legacy EWMA timing (kept for observability, not used for slot decisions anymore)
_TIMING_KEY_PREFIX = "moe:healer_timing:"
_TIMING_EWMA_ALPHA  = 0.3
_TIMING_FALLBACK_S  = 120.0

# Back-off between iterations when no tasks succeeded in the previous batch.
_BACKOFF_ON_ALL_FAIL_S = 30


# ─── Template pool discovery ──────────────────────────────────────────────────

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


def _node_name_from_template(template: str) -> str:
    """Extract the physical node identifier from a template name.

    'moe-ontology-curator-n06-m10-01' → 'n06-m10-01'
    """
    return template.replace("moe-ontology-curator-", "")


def _discover_pool_from_env() -> list[str]:
    """Fallback: build pool from INFERENCE_SERVERS env at process start."""
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
    """Pick the template pool live from Redis (written by admin UI on every toggle)."""
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


# ─── User prompt ─────────────────────────────────────────────────────────────

USER_PROMPT = """Classify this unknown term for our knowledge graph:

TERM: "{term}"

{context_section}

The Judge will return a JSON entity entry. Do not respond with prose outside the JSON."""


# ─── Per-node slot management ─────────────────────────────────────────────────

async def _get_allowed_slots(redis_cli, node_name: str) -> int:
    """Return how many concurrent tasks are currently allowed on this node.

    Starts at 1 (cold-start safety). Unlocks one additional slot for every
    MIN_RUNS_CALIBRATE successful completions on this node, up to the
    hardware-specific cap in _NODE_MAX_SLOTS.
    """
    hw_cap = _NODE_MAX_SLOTS.get(node_name, _DEFAULT_MAX_SLOTS)
    if redis_cli is None:
        return 1  # No Redis → cannot coordinate; stay conservative

    try:
        runs_raw = await redis_cli.get(_NODE_RUNS_PREFIX + node_name)
        runs = int(runs_raw) if runs_raw else 0
    except Exception:
        runs = 0

    # 0..4 runs → 1 slot; 5..9 → 2 slots; 10..14 → 3 slots; …
    allowed = 1 + (runs // MIN_RUNS_CALIBRATE)
    return min(allowed, hw_cap)


async def _try_acquire_node_slot(redis_cli, node_name: str, allowed: int) -> bool:
    """Atomically claim one active-task slot on the node.

    Returns True if the slot was acquired, False if the node is at capacity.
    Uses Redis INCR so multiple healer processes share the same counter.
    """
    if redis_cli is None:
        return True  # Fail open when Redis is unavailable
    key = _NODE_ACTIVE_PREFIX + node_name
    try:
        current = await redis_cli.incr(key)
        await redis_cli.expire(key, _NODE_ACTIVE_TTL)
        if current <= allowed:
            return True
        # Over limit — roll back
        val = await redis_cli.decr(key)
        if val < 0:
            await redis_cli.set(key, 0)
        return False
    except Exception:
        return True  # Fail open


async def _release_node_slot(redis_cli, node_name: str) -> None:
    """Release one active-task slot on the node."""
    if redis_cli is None:
        return
    try:
        val = await redis_cli.decr(_NODE_ACTIVE_PREFIX + node_name)
        if val < 0:
            await redis_cli.set(_NODE_ACTIVE_PREFIX + node_name, 0)
    except Exception:
        pass


async def _record_node_success(redis_cli, node_name: str) -> None:
    """Increment the successful-run counter for this node (used for slot calibration)."""
    if redis_cli is None:
        return
    try:
        await redis_cli.incr(_NODE_RUNS_PREFIX + node_name)
    except Exception:
        pass


# ─── Legacy EWMA timing (observability only) ─────────────────────────────────

async def _record_timing(redis_cli, template: str, duration_s: float) -> None:
    """Update EWMA of successful request duration for a template in Redis."""
    if redis_cli is None:
        return
    key = _TIMING_KEY_PREFIX + template
    try:
        raw = await redis_cli.get(key)
        if raw is None:
            new_avg = duration_s
        else:
            old_avg = float(raw)
            new_avg = _TIMING_EWMA_ALPHA * duration_s + (1 - _TIMING_EWMA_ALPHA) * old_avg
        await redis_cli.set(key, f"{new_avg:.2f}", ex=86400)
    except Exception:
        pass


async def _get_avg_duration(redis_cli, template: str) -> float:
    """Return the stored EWMA duration for a template, or the fallback."""
    if redis_cli is None:
        return _TIMING_FALLBACK_S
    try:
        raw = await redis_cli.get(_TIMING_KEY_PREFIX + template)
        if raw is not None:
            return float(raw)
    except Exception:
        pass
    return _TIMING_FALLBACK_S


# ─── Gap queue (atomic claim via ZPOPMAX) ─────────────────────────────────────

async def _pop_gaps(redis_cli, count: int) -> list[tuple[str, float]]:
    """Atomically pop up to `count` highest-priority gaps from the ZSet.

    Returns list of (term, original_score) tuples.
    On failure (no Redis), returns empty list — caller falls back to fetch_gaps().
    """
    if redis_cli is None:
        return []
    try:
        # ZPOPMAX returns list of (value, score) pairs in redis-py
        items = await redis_cli.zpopmax(GAP_ZSET_KEY, count)
        if not items:
            return []
        return [(item[0], item[1]) for item in items]
    except Exception as e:
        print(f"  [!] ZPOPMAX failed: {e}", flush=True)
        return []


async def _re_enqueue_gap(redis_cli, term: str, score: float) -> None:
    """Return a failed gap to the ZSet with a slightly reduced score (deprioritise)."""
    if redis_cli is None:
        return
    try:
        new_score = max(0.0, score - 1.0)
        await redis_cli.zadd(GAP_ZSET_KEY, {term: new_score})
    except Exception as e:
        print(f"  [!] Re-enqueue '{term}' failed: {e}", flush=True)


async def fetch_gaps(client: httpx.AsyncClient, redis_cli=None) -> list[str]:
    """Pull top ontology gaps via admin API (fallback when ZPOPMAX is not used)."""
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


# ─── Neo4j context ────────────────────────────────────────────────────────────

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


# ─── Entity parsing ───────────────────────────────────────────────────────────

def parse_entity(content: str) -> Optional[dict]:
    """Extract a valid entity JSON from the MoE response content."""
    if not content:
        return None
    c = re.sub(r"^```\w*\n?", "", content.strip())
    c = re.sub(r"\n?```$", "", c.strip())
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


# ─── Inference call ───────────────────────────────────────────────────────────

async def classify_via_template(
    client: httpx.AsyncClient, template: str, term: str, context: str,
    redis_cli=None,
) -> Optional[dict]:
    """Send the term through the given MoE template and parse the result.

    Records successful response duration in Redis for EWMA observability.
    """
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
            await _record_timing(redis_cli, template, dt)
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


# ─── Neo4j write ─────────────────────────────────────────────────────────────

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
                    pass
            return True
        except Exception as e:
            print(f"  [neo4j] Write failed: {e}", flush=True)
            return False


# ─── Per-gap pipeline ─────────────────────────────────────────────────────────

async def process_gap(
    idx: int, total: int, term: str, original_score: float,
    client: httpx.AsyncClient, driver, template: str,
    redis_cli,
) -> tuple[bool, bool]:
    """Full pipeline for one gap: context → classify → write → remove from queue.

    Returns (success, written) booleans.
    On failure the gap is re-enqueued with lower priority (not silently dropped).
    """
    node_name = _node_name_from_template(template)
    ctx = await fetch_graph_context(driver, term)
    entity = await classify_via_template(client, template, term, ctx, redis_cli=redis_cli)

    resolved = False
    if entity:
        entity["_template"] = template
        entity["_source_term"] = term
        etype = entity.get("entity_type", "")
        conf = entity.get("confidence", 0)
        if etype and etype != "Uncertain" and conf >= MIN_CONFIDENCE:
            if await write_entity(driver, entity, term):
                resolved = True
                # Count this success toward calibration for this node
                await _record_node_success(redis_cli, node_name)

    if not resolved:
        # Return gap to queue with reduced priority so other gaps get a turn
        await _re_enqueue_gap(redis_cli, term, original_score)

    written = resolved and entity is not None and entity.get("entity_type") != "Uncertain"
    return (entity is not None, written)


# ─── Batch processor (per-node slot limiting) ─────────────────────────────────

async def process_batch(
    gaps: list[tuple[str, float]], client, driver, stats: dict, redis_cli,
) -> int:
    """Process a batch of (term, score) pairs with per-node slot limiting.

    Each physical node gets its own asyncio.Semaphore whose size is derived
    from calibration data: 1 slot cold-start → +1 slot per MIN_RUNS_CALIBRATE
    successful completions → hard cap from _NODE_MAX_SLOTS.

    Redis active counters additionally coordinate across multiple healer
    processes so nodes are never over-subscribed cluster-wide.

    Returns the number of successfully resolved gaps.
    """
    if not gaps:
        return 0

    # Build per-node asyncio semaphores for this batch
    node_semaphores: dict[str, asyncio.Semaphore] = {}

    async def _get_sem(node_name: str) -> asyncio.Semaphore:
        if node_name not in node_semaphores:
            slots = await _get_allowed_slots(redis_cli, node_name)
            node_semaphores[node_name] = asyncio.Semaphore(slots)
            print(f"  [slot] {node_name}: {slots} slot(s) (calibration runs: "
                  f"{await redis_cli.get(_NODE_RUNS_PREFIX + node_name) if redis_cli else '?'})",
                  flush=True)
        return node_semaphores[node_name]

    resolved_count = 0

    async def _run(idx: int, term: str, score: float):
        nonlocal resolved_count
        template = TEMPLATE_POOL[(idx - 1) % len(TEMPLATE_POOL)]
        node_name = _node_name_from_template(template)

        sem = await _get_sem(node_name)
        allowed = await _get_allowed_slots(redis_cli, node_name)

        # Check Redis-level cross-process slot before entering asyncio semaphore
        slot_acquired = await _try_acquire_node_slot(redis_cli, node_name, allowed)
        if not slot_acquired:
            # Node is full across all healer processes; re-enqueue and skip
            print(f"  [slot] {node_name} at capacity (Redis), re-queueing '{term}'",
                  flush=True)
            await _re_enqueue_gap(redis_cli, term, score)
            return

        try:
            async with sem:
                success, written = await process_gap(
                    idx, len(gaps), term, score, client, driver, template, redis_cli,
                )
                node_key = node_name
                stats.setdefault(node_key, {"processed": 0, "written": 0, "failed": 0})
                s = stats[node_key]
                if not success:
                    s["failed"] += 1
                else:
                    s["processed"] += 1
                    if written:
                        s["written"] += 1
                        resolved_count += 1
        finally:
            await _release_node_slot(redis_cli, node_name)

    await asyncio.gather(
        *[_run(i, term, score) for i, (term, score) in enumerate(gaps, 1)],
        return_exceptions=True,
    )
    return resolved_count


# ─── Main loop ────────────────────────────────────────────────────────────────

async def main() -> int:
    print("=" * 70, flush=True)
    print("Ontology Gap Healer — per-node slot limiting", flush=True)
    print(f"  API:                {MOE_API_BASE}", flush=True)
    print(f"  Concurrency (max):  {CONCURRENCY}", flush=True)
    print(f"  Batch size:         {BATCH_SIZE}", flush=True)
    print(f"  Min confidence:     {MIN_CONFIDENCE}", flush=True)
    print(f"  Calibration runs:   {MIN_RUNS_CALIBRATE} per slot unlock", flush=True)
    print(f"  Cold-start slots:   1 per node", flush=True)
    print("=" * 70, flush=True)

    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    redis_cli = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
    if redis_cli:
        try:
            await redis_cli.ping()
            print("  Valkey:  connected (per-node slots + ZPOPMAX enabled)", flush=True)
        except Exception as e:
            print(f"  Valkey:  ⚠ not available ({e}) — cold-start mode (1 slot, no coordination)",
                  flush=True)
            redis_cli = None

    global TEMPLATE_POOL
    TEMPLATE_POOL = await _discover_pool_async(redis_cli)
    if not TEMPLATE_POOL:
        TEMPLATE_POOL = list(_STATIC_FALLBACK_POOL)

    print(f"  Templates: {len(TEMPLATE_POOL)} (round-robin)", flush=True)
    for t in TEMPLATE_POOL:
        node = _node_name_from_template(t)
        hw_cap = _NODE_MAX_SLOTS.get(node, _DEFAULT_MAX_SLOTS)
        runs_raw = await redis_cli.get(_NODE_RUNS_PREFIX + node) if redis_cli else None
        runs = int(runs_raw) if runs_raw else 0
        current_slots = min(1 + runs // MIN_RUNS_CALIBRATE, hw_cap)
        print(f"    - {t}  [slots: {current_slots}/{hw_cap}, runs: {runs}]", flush=True)

    totals = {"processed": 0, "written": 0, "failed": 0}
    iteration = 0
    consecutive_empty = 0
    t_start = time.time()

    async with httpx.AsyncClient() as client:
        while iteration < MAX_ITERATIONS:
            iteration += 1

            # Atomically claim a batch from the ZSet
            gaps = await _pop_gaps(redis_cli, BATCH_SIZE)
            if not gaps:
                print(f"\n[{time.strftime('%H:%M:%S')}] No more gaps. Done.", flush=True)
                break

            print(f"\n[{time.strftime('%H:%M:%S')}] Iteration {iteration}: "
                  f"claimed {len(gaps)} gaps via ZPOPMAX", flush=True)
            t_batch = time.time()
            batch_stats: dict = {}

            resolved = await process_batch(gaps, client, driver, batch_stats, redis_cli)
            dt_batch = time.time() - t_batch

            batch_processed = 0
            for node_key, s in batch_stats.items():
                for key in ("processed", "written", "failed"):
                    totals[key] += s[key]
                batch_processed += s["processed"]
                print(f"    {node_key}: {s['processed']} proc, "
                      f"{s['written']} written, {s['failed']} fail",
                      flush=True)
            print(f"[{time.strftime('%H:%M:%S')}] Batch done in {dt_batch:.1f}s — "
                  f"resolved: {resolved} | totals: {totals['written']} written "
                  f"/ {totals['processed']} processed",
                  flush=True)

            if batch_processed == 0:
                consecutive_empty += 1
                wait = _BACKOFF_ON_ALL_FAIL_S * consecutive_empty
                print(f"  [backoff] All tasks failed ({consecutive_empty}× in a row). "
                      f"Waiting {wait}s before next iteration.", flush=True)
                await asyncio.sleep(wait)
            else:
                consecutive_empty = 0

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
    print(f"  Failed:        {totals['failed']}", flush=True)
    print(f"  Duration:      {dt_total/60:.1f} min", flush=True)
    if totals["processed"]:
        print(f"  Throughput:    {totals['processed']/dt_total*60:.1f} gaps/min", flush=True)
    print("=" * 70, flush=True)
    return 0


if __name__ == "__main__":
    asyncio.run(main())

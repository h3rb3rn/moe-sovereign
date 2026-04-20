#!/usr/bin/env python3
"""
close_ontology_gaps.py — Autonomous Knowledge Healing

Reads the top ontology gaps (terms the system encountered but could not
match to a Neo4j entity label), uses the MoE orchestrator itself to
research each term, and writes the resulting entities + relations back
into the Neo4j knowledge graph.

This implements the "Level 3 Compounding Loop":
  Level 1 (request):  SYNTHESIS_INSIGHT triples from each request
  Level 2 (session):  few-shot corrections from the critic node
  Level 3 (nightly):  autonomous gap closure via self-research

Schedule:
  Run as a nightly cron job when inference nodes are idle:
    0 2 * * * MOE_API_KEY=... python3 /opt/moe-infra/scripts/close_ontology_gaps.py

Configuration via environment variables:
  MOE_API_BASE         Orchestrator URL (default: http://localhost:8002)
  MOE_API_KEY          API key for the bench/admin user (required)
  MOE_TEMPLATE         Template for the research calls (default: moe-reference-30b-balanced)
  NEO4J_URI            Neo4j bolt URI (default: bolt://neo4j-knowledge:7687)
  NEO4J_USER           Neo4j username (default: neo4j)
  NEO4J_PASSWORD       Neo4j password (from env or .env file)
  MAX_GAPS_PER_RUN     How many gaps to close per invocation (default: 20)
  DRY_RUN              Set to "1" to print what would be written without touching Neo4j

Usage:
  # Live run (closes up to 20 gaps):
  MOE_API_KEY=moe-sk-... python3 scripts/close_ontology_gaps.py

  # Dry run (research only, no Neo4j writes):
  MOE_API_KEY=moe-sk-... DRY_RUN=1 python3 scripts/close_ontology_gaps.py

  # Custom limits:
  MAX_GAPS_PER_RUN=5 MOE_API_KEY=moe-sk-... python3 scripts/close_ontology_gaps.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any

import httpx

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

API_BASE     = os.environ.get("MOE_API_BASE", "http://localhost:8000")
API_KEY      = os.environ.get("MOE_API_KEY", "")
TEMPLATE     = os.environ.get("MOE_TEMPLATE", "moe-reference-30b-balanced")

# Template rotation: each gap uses a different template to spread load
# across multiple GPU nodes. Comma-separated list overrides single TEMPLATE.
TEMPLATE_ROTATION = [
    t.strip() for t in os.environ.get("MOE_TEMPLATE_ROTATION", "").split(",")
    if t.strip()
]
NEO4J_URI    = os.environ.get("NEO4J_URI", "bolt://neo4j-knowledge:7687")
NEO4J_USER   = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS   = os.environ.get("NEO4J_PASSWORD", "") or os.environ.get("NEO4J_PASS", "")
MAX_GAPS     = int(os.environ.get("MAX_GAPS_PER_RUN", "20"))
DRY_RUN      = os.environ.get("DRY_RUN", "0") == "1"

LOG_DIR = pathlib.Path("/app/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gap-healer] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "ontology_gap_healing.log"),
    ],
)
log = logging.getLogger(__name__)

if not API_KEY:
    log.error("MOE_API_KEY is required")
    sys.exit(1)

# --------------------------------------------------------------------------
# Prompt for the research call
# --------------------------------------------------------------------------

RESEARCH_SYSTEM_PROMPT = (
    "You are a knowledge management expert. Your task is to classify an "
    "unknown technical term for a knowledge graph (Neo4j). "
    "Reply ONLY with a JSON object in the following format — "
    "no explanations, no markdown, just the JSON:\n\n"
    '{\n'
    '  "name": "<Canonical name of the term>",\n'
    '  "entity_type": "<Entity|Concept|Law|Drug|Software|Protocol|'
    'Organization|Method|Standard>",\n'
    '  "description_de": "<1-sentence description in German>",\n'
    '  "description_en": "<1-sentence description in English>",\n'
    '  "aliases": ["<Alias1>", "<Alias2>"],\n'
    '  "relations": [\n'
    '    {"type": "<RELATES_TO|PART_OF|REQUIRES|IMPLEMENTS|'
    'USED_BY|REGULATES>", "target": "<Name of target entity>"}\n'
    '  ]\n'
    '}'
)

RESEARCH_USER_TEMPLATE = (
    "Classify the following technical term for our knowledge graph. "
    "The term was identified as an ontology gap — it appears in LLM responses "
    "but has not yet been catalogued in the graph.\n\n"
    'Term: "{term}"\n\n'
    "Reply ONLY with the JSON object. No markdown, no explanation."
)


# --------------------------------------------------------------------------
# Gap reading
# --------------------------------------------------------------------------

async def fetch_gaps(client: httpx.AsyncClient) -> list[str]:
    """Fetch the top ontology gaps from the orchestrator's admin API."""
    try:
        r = await client.get(
            f"{API_BASE}/v1/admin/ontology-gaps",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            # The endpoint returns a list of {term, count} objects
            if isinstance(data, list):
                gaps = [item.get("term", item) if isinstance(item, dict) else str(item)
                        for item in data]
                return gaps[:MAX_GAPS]
            elif isinstance(data, dict) and "gaps" in data:
                return [g.get("term", g) if isinstance(g, dict) else str(g)
                        for g in data["gaps"][:MAX_GAPS]]
        log.warning(f"ontology-gaps endpoint returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"Could not fetch gaps from API: {e}")

    # Fallback: read from Prometheus metrics endpoint
    try:
        r = await client.get(f"{API_BASE}/metrics", timeout=10)
        # Parse moe_ontology_gaps_total — but this only gives the COUNT,
        # not the actual terms. Log a warning and return empty.
        log.warning("API gap endpoint unavailable; Prometheus only gives counts, not terms.")
    except Exception:
        pass

    return []


# --------------------------------------------------------------------------
# JSON extraction helpers
# --------------------------------------------------------------------------

# Map of alternative key names the LLM might use for each required field
_KEY_ALIASES = {
    "name": ["name", "concept_name", "begriff", "term", "canonical_name",
             "kanonischer_name", "entity_name", "title"],
    "entity_type": ["entity_type", "type", "entity_type_classification",
                    "kategorie", "category", "classification", "label"],
}


def _normalize_entity(raw: dict, term: str) -> dict | None:
    """Normalize a parsed JSON dict into the expected entity schema.

    Handles LLM output that uses alternative key names or wraps the
    payload inside a nested object.
    """
    # If the dict has a single top-level key whose value is also a dict,
    # unwrap it (e.g. {"Ontology_Entry": {...}})
    if len(raw) == 1:
        only_val = next(iter(raw.values()))
        if isinstance(only_val, dict):
            raw = only_val

    # Build a canonical dict by resolving key aliases
    result: dict[str, Any] = {}
    lower_map = {k.lower().replace(" ", "_").replace("-", "_"): v for k, v in raw.items()}

    for canon, aliases in _KEY_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                result[canon] = lower_map[alias]
                break

    # Fallback: use the original term as name if not found
    if "name" not in result:
        result["name"] = term
    if "entity_type" not in result:
        result["entity_type"] = "Concept"

    # Copy optional fields
    for key in ("description_de", "description_en", "aliases", "relations"):
        lk = key.lower()
        if lk in lower_map and lk not in result:
            result[key] = lower_map[lk]

    # Ensure entity_type is a plain string, not a dict
    if not isinstance(result.get("entity_type"), str):
        result["entity_type"] = "Concept"

    # Ensure required fields have sane values
    if not isinstance(result.get("aliases"), list):
        result["aliases"] = []
    if not isinstance(result.get("relations"), list):
        result["relations"] = []

    return result


def _extract_entity_json(content: str, term: str) -> dict | None:
    """Try multiple strategies to extract a valid entity dict from LLM output."""
    # Strategy 1: parse the entire content as JSON
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            normalized = _normalize_entity(parsed, term)
            if normalized:
                return normalized
    except json.JSONDecodeError:
        pass

    # Strategy 2: find the outermost { ... } block (allows nested braces)
    depth = 0
    start = None
    for i, ch in enumerate(content):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    parsed = json.loads(content[start:i + 1])
                    if isinstance(parsed, dict):
                        normalized = _normalize_entity(parsed, term)
                        if normalized:
                            return normalized
                except json.JSONDecodeError:
                    pass
                start = None

    return None


# --------------------------------------------------------------------------
# Research via MoE orchestrator
# --------------------------------------------------------------------------

async def research_term(
    client: httpx.AsyncClient, term: str,
    template: Optional[str] = None,
) -> dict | None:
    """Use the MoE orchestrator to research an unknown term.

    Args:
        template: Override the default template (for rotation across nodes).
    """
    model = template or TEMPLATE
    try:
        r = await client.post(
            f"{API_BASE}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                    {"role": "user", "content": RESEARCH_USER_TEMPLATE.format(term=term)},
                ],
                "stream": False,
                "max_tokens": 500,
                "temperature": 0.1,
            },
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=1800,
        )
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Parse JSON from response (might be wrapped in markdown code blocks)
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```\w*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        # Try to parse the full JSON first, then look for nested structures
        parsed = _extract_entity_json(content, term)
        if parsed:
            return parsed

        log.warning(f"  Could not parse JSON for '{term}': {content[:200]}")
        return None
    except Exception as e:
        log.error(f"  Research failed for '{term}': {e}")
        return None


# --------------------------------------------------------------------------
# Neo4j writing
# --------------------------------------------------------------------------

def write_to_neo4j(entities: list[dict]) -> int:
    """Write researched entities to Neo4j. Returns count of entities written."""
    if not entities:
        return 0

    try:
        from neo4j import GraphDatabase
    except ImportError:
        log.error("neo4j Python driver not installed. Install with: pip install neo4j")
        return 0

    if not NEO4J_PASS:
        # Try to read from .env — search relative to this script, then CWD
        env_path = next(
            (p for p in [
                pathlib.Path(__file__).parent.parent / ".env",
                pathlib.Path.cwd() / ".env",
            ] if p.exists()),
            None,
        )
        if env_path is not None:
            for line in env_path.read_text().splitlines():
                if line.startswith("NEO4J_PASSWORD="):
                    neo4j_pass = line.split("=", 1)[1].strip().strip('"')
                    break
            else:
                neo4j_pass = ""
        else:
            neo4j_pass = ""
    else:
        neo4j_pass = NEO4J_PASS

    if not neo4j_pass:
        log.error("NEO4J_PASSWORD not set and not found in .env")
        return 0

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, neo4j_pass))
    count = 0

    with driver.session() as session:
        for entity in entities:
            name = entity["name"]
            etype = entity.get("entity_type", "Concept")
            desc_de = entity.get("description_de", "")
            desc_en = entity.get("description_en", "")
            aliases = entity.get("aliases", [])

            # MERGE the entity (idempotent)
            session.run(
                """
                MERGE (e:Entity {name: $name})
                SET e.type = $type,
                    e.description_de = $desc_de,
                    e.description_en = $desc_en,
                    e.aliases = $aliases,
                    e.source = 'ontology_gap_healer',
                    e.healed_at = datetime()
                """,
                name=name, type=etype, desc_de=desc_de, desc_en=desc_en,
                aliases=aliases,
            )
            count += 1

            # MERGE relations
            for rel in entity.get("relations", []):
                rel_type = rel.get("type", "RELATES_TO").upper().replace(" ", "_")
                target = rel.get("target", "")
                if target:
                    session.run(
                        f"""
                        MERGE (a:Entity {{name: $from_name}})
                        MERGE (b:Entity {{name: $to_name}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.source = 'ontology_gap_healer',
                            r.healed_at = datetime()
                        """,
                        from_name=name, to_name=target,
                    )

    driver.close()
    return count


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

async def main() -> int:
    log.info("=" * 60)
    log.info("Autonomous Knowledge Healing — Ontology Gap Closer")
    log.info(f"  Template:  {TEMPLATE}")
    log.info(f"  Max gaps:  {MAX_GAPS}")
    log.info(f"  Dry run:   {DRY_RUN}")
    log.info(f"  Neo4j:     {NEO4J_URI}")
    log.info("=" * 60)

    # Phase 0: Stale triple cleanup — remove decayed, unverified single-assertion triples
    log.info("Phase 0: Stale triple cleanup (confidence decay)...")
    decay_deleted = 0
    if not DRY_RUN:
        try:
            from neo4j import GraphDatabase as _SyncGraphDatabase
            _drv = _SyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
            with _drv.session() as _sess:
                # Compute trust scores in-place
                _rels = _sess.run("""
                    MATCH ()-[r]->()
                    WHERE (r.verified IS NULL OR r.verified = false)
                      AND (r.version IS NULL OR r.version = 1)
                      AND r.confidence IS NOT NULL
                      AND r.valid_from IS NOT NULL
                    RETURN id(r) AS rid, r.confidence AS conf,
                           r.source AS src, r.valid_from AS vf,
                           startNode(r).name AS subj, type(r) AS rel,
                           endNode(r).name AS tgt
                    LIMIT 200
                """).data()
                import time as _t
                _now = _t.time() * 1000
                for _row in _rels:
                    _src_w = {"ontology": 1.0, "ontology_gap_healer": 0.9}.get(
                        _row.get("src", ""), 0.6
                    )
                    _days = max(0, (_now - (_row.get("vf") or _now)) / (86400 * 1000))
                    _decay = max(0.3, 1.0 - (_days / 365))
                    _trust = (_row.get("conf") or 0.5) * _src_w * _decay
                    if _trust < 0.2:
                        _sess.run("MATCH ()-[r]->() WHERE id(r) = $rid DELETE r",
                                  rid=_row["rid"])
                        decay_deleted += 1
                        log.info(f"  Decay-deleted: ({_row['subj']})-[{_row['rel']}]->({_row['tgt']}) "
                                f"trust={_trust:.3f}")
            _drv.close()
            log.info(f"  Phase 0 complete: {decay_deleted} stale triples removed")
        except Exception as _e:
            log.warning(f"  Phase 0 failed: {_e}")
    else:
        log.info("  Phase 0 skipped (dry run)")

    async with httpx.AsyncClient() as client:
        # 1. Fetch gaps
        log.info("Phase 1: Reading ontology gaps...")
        gaps = await fetch_gaps(client)
        if not gaps:
            log.info("No gaps found or API unavailable. Nothing to do.")
            return 0
        log.info(f"  Found {len(gaps)} gaps to research")

        # 2. Research gaps in parallel (default concurrency = 5 workers)
        concurrency = int(os.environ.get("GAP_HEALER_CONCURRENCY", "5"))
        templates = TEMPLATE_ROTATION if TEMPLATE_ROTATION else [TEMPLATE]
        log.info(f"Phase 2: Researching {len(gaps)} gaps via MoE orchestrator "
                 f"(concurrency={concurrency}, templates={templates})...")
        researched: list[dict] = []
        semaphore = asyncio.Semaphore(concurrency)

        async def _research_one(idx: int, term: str) -> Optional[dict]:
            # Round-robin template rotation to spread load across nodes
            tmpl = templates[(idx - 1) % len(templates)]
            async with semaphore:
                log.info(f"  [{idx}/{len(gaps)}] Researching: '{term}' via {tmpl}")
                result = await research_term(client, term, template=tmpl)
                if result:
                    log.info(f"    ✓ [{idx}] {result['name']} → {result['entity_type']} "
                             f"({len(result.get('relations', []))} relations)")
                else:
                    log.info(f"    ✗ [{idx}] Could not classify '{term}'")
                return result

        tasks = [_research_one(i, term) for i, term in enumerate(gaps, 1)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, dict):
                researched.append(r)
            elif isinstance(r, Exception):
                log.error(f"  Task exception: {r}")

        log.info(f"  Successfully researched: {len(researched)}/{len(gaps)}")

    # 3. Write to Neo4j
    if DRY_RUN:
        log.info("Phase 3: DRY RUN — would write the following entities:")
        for e in researched:
            log.info(f"  MERGE (:{e['entity_type']} {{name: '{e['name']}', "
                    f"aliases: {e.get('aliases', [])}}}) "
                    f"+ {len(e.get('relations', []))} relations")
        written = 0
    else:
        log.info("Phase 3: Writing to Neo4j...")
        written = write_to_neo4j(researched)
        log.info(f"  Written: {written} entities to Neo4j")

    # 4. Summary
    log.info("")
    log.info("=" * 60)
    log.info("Summary:")
    log.info(f"  Gaps read:       {len(gaps)}")
    log.info(f"  Researched:      {len(researched)}")
    log.info(f"  Written to Neo4j: {written}")
    log.info(f"  Success rate:    {len(researched)/len(gaps)*100:.0f}%")
    log.info("=" * 60)

    # 5. Save results for auditing
    results_path = LOG_DIR / f"gap_healing_{time.strftime('%Y%m%d-%H%M%S')}.json"
    results_path.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S%z"),
        "template": TEMPLATE,
        "dry_run": DRY_RUN,
        "gaps_read": len(gaps),
        "gaps_researched": len(researched),
        "gaps_written": written,
        "entities": researched,
    }, indent=2, ensure_ascii=False))
    log.info(f"Results saved: {results_path}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

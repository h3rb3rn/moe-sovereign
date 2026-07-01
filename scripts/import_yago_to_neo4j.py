#!/usr/bin/env python3
"""
scripts/import_yago_to_neo4j.py — YAGO 4 Knowledge Import (TASK-19).

Imports YAGO 4 RDF/TTL entities into Neo4j GraphRAG, mapping YAGO schema types
to the MoE ontology (Tech_Concept, Algorithm, Framework, Tool).

Usage:
    python import_yago_to_neo4j.py --input yago-software.ttl [--limit 10000] [--dry-run]

YAGO 4 dumps: https://yago-knowledge.org/data/yago4/
Relevant schema types:
    schema:SoftwareApplication, schema:SoftwareSourceCode → Tech_Concept
    schema:ComputerLanguage, wikidata:Q9143 (Programming Language) → Tech_Concept
    schema:Algorithm → Tech_Concept (Algorithmus)
    schema:WebAPI, schema:Dataset → Tool
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── YAGO schema → MoE ontology type mapping ──────────────────────────────────
_YAGO_TYPE_MAP = {
    "schema:SoftwareApplication":  "Framework",
    "schema:SoftwareSourceCode":   "Tech_Concept",
    "schema:ComputerLanguage":     "Tech_Concept",
    "schema:Algorithm":            "Tech_Concept",
    "schema:WebAPI":               "Tool",
    "schema:Dataset":              "Tool",
    "schema:CreativeWork":         "Tech_Concept",
    "wd:Q9143":                    "Tech_Concept",   # Programming Language
    "wd:Q7397":                    "Tech_Concept",   # Software
    "wd:Q28640":                   "Tech_Concept",   # Profession
}

_BATCH_SIZE   = 500
_LOG_INTERVAL = 10_000
_SOURCE_WEIGHT = 0.8  # Higher than "extracted" (0.6), lower than ontology (1.0)


def _map_yago_type(yago_type: str) -> str | None:
    """Map a YAGO schema type URL to a MoE ontology type string."""
    for pattern, moe_type in _YAGO_TYPE_MAP.items():
        if pattern.lower() in yago_type.lower():
            return moe_type
    return None


def _extract_label(subj, graph) -> str:
    """Extract a human-readable label from an RDF subject."""
    from rdflib import RDFS, SKOS, URIRef
    label_preds = [RDFS.label, SKOS.prefLabel]
    for pred in label_preds:
        for obj in graph.objects(subj, pred):
            s = str(obj)
            if "@en" in s or not any(c in s for c in ("@de", "@fr", "@es")):
                return s.split("@")[0].strip('"')
    # Fallback: derive label from URI local name
    uri = str(subj)
    return uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].replace("_", " ")


def import_yago(ttl_path: str, neo4j_uri: str, neo4j_user: str, neo4j_pass: str,
                limit: int = 0, dry_run: bool = False) -> int:
    """Parse TTL and import entities into Neo4j. Returns count of imported entities."""
    try:
        from rdflib import Graph, RDF
    except ImportError:
        logger.error("rdflib not installed. Run: pip install rdflib")
        sys.exit(1)

    logger.info("Loading RDF graph from %s …", ttl_path)
    g = Graph()
    g.parse(ttl_path, format="turtle")
    logger.info("Parsed %d RDF triples", len(g))

    if not dry_run:
        try:
            from neo4j import GraphDatabase
        except ImportError:
            logger.error("neo4j driver not installed. Run: pip install neo4j")
            sys.exit(1)
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    else:
        driver = None

    batch: list[dict] = []
    total = 0
    skipped = 0

    for subj, pred, obj in g.triples((None, RDF.type, None)):
        moe_type = _map_yago_type(str(obj))
        if not moe_type:
            skipped += 1
            continue

        label = _extract_label(subj, g)
        if not label or len(label) < 2:
            skipped += 1
            continue

        batch.append({
            "name":          label,
            "type":          moe_type,
            "source":        "yago",
            "source_weight": _SOURCE_WEIGHT,
            "yago_uri":      str(subj),
        })
        total += 1

        if len(batch) >= _BATCH_SIZE:
            if not dry_run:
                _flush_batch(driver, batch)
            batch.clear()

        if total % _LOG_INTERVAL == 0:
            logger.info("Processed %d entities (skipped %d) …", total, skipped)

        if limit and total >= limit:
            logger.info("Limit %d reached, stopping.", limit)
            break

    # Flush remainder
    if batch and not dry_run:
        _flush_batch(driver, batch)

    if driver:
        driver.close()

    logger.info("Import complete: %d entities imported, %d skipped.", total, skipped)
    return total


def _flush_batch(driver, batch: list[dict]):
    """Write a batch of entities to Neo4j using MERGE (no overwrite of existing nodes)."""
    cypher = """
    UNWIND $rows AS row
    MERGE (e:Entity {name: row.name, type: row.type})
    ON CREATE SET
        e.source        = row.source,
        e.source_weight = row.source_weight,
        e.yago_uri      = row.yago_uri,
        e.confidence    = row.source_weight
    """
    with driver.session() as session:
        session.run(cypher, rows=batch)


def main():
    parser = argparse.ArgumentParser(description="Import YAGO 4 TTL into Neo4j GraphRAG")
    parser.add_argument("--input",     required=True,     help="Path to YAGO TTL/NT file")
    parser.add_argument("--limit",     type=int, default=0, help="Max entities to import (0=all)")
    parser.add_argument("--dry-run",   action="store_true",  help="Parse but do not write to Neo4j")
    parser.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-pass", default="neo4j")
    args = parser.parse_args()

    if not Path(args.input).exists():
        logger.error("Input file not found: %s", args.input)
        sys.exit(1)

    if args.dry_run:
        logger.info("DRY-RUN mode: no data will be written to Neo4j")

    count = import_yago(
        ttl_path=args.input,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_pass=args.neo4j_pass,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    logger.info("Done. Entities processed: %d", count)


if __name__ == "__main__":
    main()

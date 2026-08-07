#!/usr/bin/env python3
"""
scripts/cron_habe_rebuild.py — Periodically rebuilds the Holographic Ambient Vector (HAV)
by compiling active GraphRAG triples from Neo4j.
"""

import asyncio
import logging
import os
import sys
import tempfile
import numpy as np

# Adjust path to find services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.vsa_background import HolographicBackgroundEngine
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASS

# Setup logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HABE-REBUILD")

async def fetch_neo4j_triples() -> list[tuple[str, str, str]]:
    """Queries all active Entity relationships from Neo4j."""
    from neo4j import AsyncGraphDatabase
    
    uri = NEO4J_URI or "bolt://neo4j-knowledge:7687"
    user = NEO4J_USER or "neo4j"
    password = NEO4J_PASS
    
    if not password:
        logger.warning("NEO4J_PASS is not set. Skipping database fetch.")
        return []
        
    logger.info("Connecting to Neo4j at %s...", uri)
    triples = []
    try:
        async with AsyncGraphDatabase.driver(uri, auth=(user, password)) as driver:
            async with driver.session() as session:
                query = (
                    "MATCH (s:Entity)-[r]->(o:Entity) "
                    "RETURN s.name AS subject, type(r) AS predicate, o.name AS object"
                )
                result = await session.run(query)
                records = await result.data()
                for rec in records:
                    triples.append((rec["subject"], rec["predicate"], rec["object"]))
        logger.info("Successfully fetched %d triples from Neo4j.", len(triples))
    except Exception as e:
        logger.error("Failed to fetch triples from Neo4j: %s", e)
    return triples

def write_habe_outputs(
    triples: list[tuple[str, str, str]],
    models_dir: str,
) -> tuple[str, str]:
    """Compile and atomically publish the vector/vocabulary pair."""
    os.makedirs(models_dir, exist_ok=True)

    # The reader in graph/tool_nodes.py expects this exact filename. ``np.save``
    # appends ".npy" when the target lacks it, which previously produced the
    # unreachable file ``habe_vector.bin.npy``.
    vector_path = os.path.join(models_dir, "habe_vector.npy")
    vocab_path = os.path.join(models_dir, "habe_vocab.json")

    engine = HolographicBackgroundEngine(dimension=2048)

    if engine.load_vocab(vocab_path):
        logger.info("Loaded existing vocabulary to preserve vector semantics.")
    else:
        logger.info("No existing vocabulary found. Initializing new mapping.")

    hav = engine.compile_graph_to_vsa(triples)

    vector_fd, vector_tmp = tempfile.mkstemp(
        prefix=".habe_vector-", suffix=".npy", dir=models_dir
    )
    os.close(vector_fd)
    vocab_fd, vocab_tmp = tempfile.mkstemp(
        prefix=".habe_vocab-", suffix=".json", dir=models_dir
    )
    os.close(vocab_fd)
    try:
        np.save(vector_tmp, hav)
        engine.save_vocab(vocab_tmp)
        os.replace(vector_tmp, vector_path)
        os.replace(vocab_tmp, vocab_path)
    finally:
        for tmp_path in (vector_tmp, vocab_tmp):
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    logger.info("Saved Holographic Ambient Vector (HAV) to %s.", vector_path)
    logger.info("Successfully finished HABE rebuild process.")
    return vector_path, vocab_path


async def main() -> bool:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    models_dir = os.getenv("HABE_MODELS_DIR", os.path.join(repo_root, "models"))

    triples = await fetch_neo4j_triples()
    if not triples:
        if os.getenv("HABE_ALLOW_BOOTSTRAP", "0") != "1":
            logger.error(
                "No Neo4j triples retrieved; preserving the last valid HABE "
                "snapshot. Set HABE_ALLOW_BOOTSTRAP=1 only for development."
            )
            return False
        logger.warning("Bootstrapping HABE with development ontology triples.")
        triples = [
            ("Model", "optimized_on", "Node04-RTX"),
            ("Tesla-K80", "reserved_for", "Float64-Scientific"),
            ("LUMI-G", "used_for", "SFT-DPO-Training"),
            ("HABE", "compiles_to", "VSA-Vector"),
            ("Dreyfus", "argued", "Background-Knowledge"),
        ]

    try:
        write_habe_outputs(triples, models_dir)
        return True
    except Exception as exc:
        logger.critical("Failed to save HABE outputs: %s", exc)
        return False

if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 2)

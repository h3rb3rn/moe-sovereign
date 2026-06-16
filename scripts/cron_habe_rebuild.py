#!/usr/bin/env python3
"""
scripts/cron_habe_rebuild.py — Periodically rebuilds the Holographic Ambient Vector (HAV)
by compiling active GraphRAG triples from Neo4j.
"""

import asyncio
import logging
import os
import sys
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

async def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    models_dir = os.path.join(repo_root, "models")
    os.makedirs(models_dir, exist_ok=True)
    
    vector_path = os.path.join(models_dir, "habe_vector.bin")
    vocab_path = os.path.join(models_dir, "habe_vocab.json")
    
    # 1. Fetch graph triples
    triples = await fetch_neo4j_triples()
    
    # 2. Fallback to basic sample triples if database is empty/offline
    # (keeps system functional for testing/bootstrapping)
    if not triples:
        logger.info("No triples retrieved. Bootstrapping with fallback default ontology triples...")
        triples = [
            ("Model", "optimized_on", "Node04-RTX"),
            ("Tesla-K80", "reserved_for", "Float64-Scientific"),
            ("LUMI-G", "used_for", "SFT-DPO-Training"),
            ("HABE", "compiles_to", "VSA-Vector"),
            ("Dreyfus", "argued", "Background-Knowledge"),
        ]
        
    # 3. Compile VSA Holographic Ambient Vector
    engine = HolographicBackgroundEngine(dimension=2048)
    
    # Try to load existing vocabulary first to maintain concept mapping consistency
    if engine.load_vocab(vocab_path):
        logger.info("Loaded existing vocabulary to preserve vector semantics.")
    else:
        logger.info("No existing vocabulary found. Initializing new mapping.")
        
    # Compile
    hav = engine.compile_graph_to_vsa(triples)
    
    # 4. Save results
    try:
        # Save VSA vector as a binary file
        np.save(vector_path, hav)
        logger.info("Saved Holographic Ambient Vector (HAV) to %s.", vector_path)
        # Save vocabulary mapping
        engine.save_vocab(vocab_path)
        logger.info("Successfully finished HABE rebuild process.")
    except Exception as e:
        logger.critical("Failed to save HABE outputs: %s", e)

if __name__ == "__main__":
    asyncio.run(main())

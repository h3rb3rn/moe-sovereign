#!/usr/bin/env python3
"""
scripts/ingest_corpora_batch.py — Batch Knowledge & Wikimedia Dump Ingestion Tool.

Idempotently ingests JSONL, JSON, Markdown and Text corpora from data/corpora/
into:
  1. Neo4j Knowledge Graph (`neo4j-knowledge` / :Document, :Topic, :Entity nodes)
  2. ChromaDB Vector Store (`chromadb-vector` / collection: moe_knowledge_corpora)

Usage:
  python3 scripts/ingest_corpora_batch.py [--corpora-dir data/corpora] [--batch-size 100] [--dry-run]
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("ingest_corpora_batch")

# ── Load .env if present ───────────────────────────────────────────────────────
def _load_env_file():
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k not in os.environ:
                        os.environ[k] = v

_load_env_file()

# ── Connection Configuration ──────────────────────────────────────────────────
def _get_neo4j_credentials():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD") or os.getenv("NEO4J_PASS") or ""
    return uri, user, password

def _get_chroma_config():
    host = os.getenv("CHROMA_HOST", "localhost")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    return host, port

# ── Extraction & Chunking Helpers ──────────────────────────────────────────────
def _hash_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

def _chunk_text(text: str, max_chars: int = 1500, overlap: int = 200) -> List[str]:
    if not text or len(text) <= max_chars:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start += max_chars - overlap
    return chunks

def _extract_simple_entities(text: str) -> List[str]:
    """Basic heuristic entity extraction (capitalized phrases/terms)."""
    matches = re.findall(r'\b[A-Z][a-zA-Z0-9_\-]{2,}(?:\s+[A-Z][a-zA-Z0-9_\-]{2,})*\b', text)
    stopwords = {"The", "This", "That", "Here", "What", "When", "Where", "Which", "How", "From", "Into", "With", "After", "Before", "Step", "Note"}
    return sorted(list({m for m in matches if m not in stopwords}))[:10]

# ── Parsers ──────────────────────────────────────────────────────────────────
def parse_corpus_file(file_path: Path, max_items: int = 0) -> List[Dict[str, Any]]:
    records = []
    ext = file_path.suffix.lower()

    if ext == ".jsonl":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for idx, line in enumerate(f):
                if max_items > 0 and idx >= max_items:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    text = obj.get("text") or obj.get("content") or obj.get("answer") or obj.get("question") or json.dumps(obj)
                    topic = obj.get("topic") or obj.get("title") or file_path.stem
                    records.append({
                        "id": f"{file_path.stem}_{idx}_{_hash_id(text)}",
                        "topic": topic,
                        "text": text,
                        "metadata": {
                            "source_file": file_path.name,
                            "topic": topic,
                            "type": "jsonl_record"
                        }
                    })
                except Exception:
                    continue

    elif ext == ".json":
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for idx, obj in enumerate(data):
                        if max_items > 0 and idx >= max_items:
                            break
                        if isinstance(obj, dict):
                            text = obj.get("text") or obj.get("content") or obj.get("answer") or json.dumps(obj)
                            topic = obj.get("topic") or obj.get("title") or file_path.stem
                            records.append({
                                "id": f"{file_path.stem}_{idx}_{_hash_id(text)}",
                                "topic": topic,
                                "text": text,
                                "metadata": {"source_file": file_path.name, "topic": topic, "type": "json_record"}
                            })
        except Exception as e:
            logger.warning("Could not parse JSON file %s: %s", file_path.name, e)

    elif ext in (".md", ".txt"):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                chunks = _chunk_text(content)
                for idx, chunk in enumerate(chunks):
                    if max_items > 0 and idx >= max_items:
                        break
                    records.append({
                        "id": f"{file_path.stem}_{idx}_{_hash_id(chunk)}",
                        "topic": file_path.stem,
                        "text": chunk,
                        "metadata": {"source_file": file_path.name, "topic": file_path.stem, "type": "markdown_chunk"}
                    })
        except Exception as e:
            logger.warning("Could not parse text file %s: %s", file_path.name, e)

    return records

# ── Ingestion Handlers ───────────────────────────────────────────────────────
def ingest_into_chroma(records: List[Dict[str, Any]], host: str, port: int, dry_run: bool = False) -> int:
    if dry_run or not records:
        return len(records)
    try:
        import chromadb
        client = chromadb.HttpClient(host=host, port=port)
        collection = client.get_or_create_collection(name="moe_knowledge_corpora")
        
        ids = [r["id"] for r in records]
        documents = [r["text"] for r in records]
        metadatas = [r["metadata"] for r in records]
        
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(records)
    except Exception as e:
        logger.warning("ChromaDB ingestion skipped/failed: %s", e)
        return 0

def ingest_into_neo4j(records: List[Dict[str, Any]], uri: str, user: str, password: str, dry_run: bool = False) -> int:
    if dry_run or not records or not password:
        if not password:
            logger.info("Neo4j password unconfigured — skipping Neo4j graph seeding")
        return 0
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(user, password))
        
        query = """
        UNWIND $batch AS item
        MERGE (d:Document {name: item.source_file})
        MERGE (t:Topic {name: item.topic})
        MERGE (d)-[:HAS_TOPIC]->(t)
        MERGE (c:Chunk {id: item.id})
        SET c.text = substring(item.text, 0, 500), c.updated_at = timestamp()
        MERGE (t)-[:CONTAINS]->(c)
        WITH item, c
        UNWIND item.entities AS entity_name
        MERGE (e:Entity {name: entity_name})
        MERGE (c)-[:MENTIONS]->(e)
        """
        
        batch_data = []
        for r in records:
            entities = _extract_simple_entities(r["text"])
            batch_data.append({
                "id": r["id"],
                "topic": r["topic"],
                "source_file": r["metadata"]["source_file"],
                "text": r["text"],
                "entities": entities
            })
            
        with driver.session() as session:
            session.run(query, batch=batch_data)
            
        driver.close()
        return len(records)
    except Exception as e:
        logger.warning("Neo4j graph ingestion skipped/failed: %s", e)
        return 0

# ── Main Runner ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Batch Knowledge & Wikimedia Ingestion Tool")
    parser.add_argument("--corpora-dir", default="data/corpora", help="Directory containing corpora files")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for database upserts")
    parser.add_argument("--limit-per-file", type=int, default=0, help="Limit records per file (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true", help="Parse files without writing to DBs")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    corpora_path = Path(args.corpora_dir)
    if not corpora_path.is_absolute():
        corpora_path = repo_root / corpora_path

    if not corpora_path.exists():
        logger.error("Corpora directory not found: %s", corpora_path)
        sys.exit(1)

    files = sorted([f for f in corpora_path.glob("*") if f.suffix.lower() in (".jsonl", ".json", ".md", ".txt")])
    if not files:
        logger.info("No corpora files found in %s", corpora_path)
        sys.exit(0)

    neo4j_uri, neo4j_user, neo4j_pass = _get_neo4j_credentials()
    chroma_host, chroma_port = _get_chroma_config()

    logger.info("Starting Batch Ingestion Pipeline:")
    logger.info("  Corpora Dir : %s (%d files)", corpora_path, len(files))
    logger.info("  Neo4j URI   : %s", neo4j_uri)
    logger.info("  Chroma Host : %s:%d", chroma_host, chroma_port)
    logger.info("  Dry Run     : %s", args.dry_run)
    logger.info("------------------------------------------------------------------")

    total_records = 0
    total_chroma = 0
    total_neo4j = 0
    start_time = time.time()

    for f in files:
        logger.info("Processing corpus file: %s ...", f.name)
        records = parse_corpus_file(f, max_items=args.limit_per_file)
        if not records:
            logger.info("  -> 0 records parsed")
            continue

        total_records += len(records)
        logger.info("  -> %d records parsed. Ingesting in batches of %d...", len(records), args.batch_size)

        for i in range(0, len(records), args.batch_size):
            batch = records[i:i + args.batch_size]
            c_cnt = ingest_into_chroma(batch, chroma_host, chroma_port, dry_run=args.dry_run)
            n_cnt = ingest_into_neo4j(batch, neo4j_uri, neo4j_user, neo4j_pass, dry_run=args.dry_run)
            total_chroma += c_cnt
            total_neo4j += n_cnt

    elapsed = time.time() - start_time
    logger.info("------------------------------------------------------------------")
    logger.info("Batch Ingestion Completed in %.2fs!", elapsed)
    logger.info("  Parsed Records   : %d", total_records)
    logger.info("  ChromaDB Chunks  : %d", total_chroma)
    logger.info("  Neo4j Graph Items: %d", total_neo4j)

if __name__ == "__main__":
    main()

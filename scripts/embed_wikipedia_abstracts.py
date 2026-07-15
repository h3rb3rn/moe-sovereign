#!/usr/bin/env python3
"""
scripts/embed_wikipedia_abstracts.py — Wikipedia Abstracts Chunking + Embedding (TASK-20).

Reads Neo4j entities with source="yago", fetches Wikipedia summaries,
chunks them with tiktoken (200 tokens, 40 token overlap), embeds with
all-MiniLM-L6-v2, and stores in ChromaDB collection "moe_wikipedia_abstracts".

Usage:
    python embed_wikipedia_abstracts.py [--limit 1000] [--concurrency 10]
    python embed_wikipedia_abstracts.py --entity "FastAPI" --entity "GraphQL"

Dependencies (already in requirements.txt):
    httpx, tiktoken, sentence-transformers, chromadb, neo4j
"""

import argparse
import asyncio
import hashlib
import logging
import os
import sys
from typing import Iterator, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_CHUNK_TOKENS   = int(os.getenv("WIKI_CHUNK_TOKENS", "200"))
_CHUNK_OVERLAP  = int(os.getenv("WIKI_CHUNK_OVERLAP", "40"))
_RATE_LIMIT     = int(os.getenv("WIKI_RATE_LIMIT", "5"))        # requests/second
_CHROMA_COLL    = "moe_wikipedia_abstracts"
_EMBED_MODEL    = "all-MiniLM-L6-v2"
_WIKI_API       = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
_USER_AGENT     = "MoE-Sovereign/1.0 (https://github.com/moe-sovereign; contact@moe.local)"


# ── Chunking ──────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[int]:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    return enc.encode(text)


def _detokenize(tokens: list[int]) -> str:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    return enc.decode(tokens)


def chunk_text(text: str, chunk_size: int = _CHUNK_TOKENS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping token chunks."""
    if not text or not text.strip():
        return []
    tokens = _tokenize(text)
    if len(tokens) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunks.append(_detokenize(tokens[start:end]))
        if end == len(tokens):
            break
        start += chunk_size - overlap
    return chunks


# ── Wikipedia Fetch ───────────────────────────────────────────────────────────

async def fetch_summary(session, entity_name: str, semaphore: asyncio.Semaphore) -> str | None:
    """Fetch Wikipedia page summary for entity_name."""
    title = entity_name.replace(" ", "_")
    url   = _WIKI_API.format(title=title)
    async with semaphore:
        try:
            resp = await session.get(url, headers={"User-Agent": _USER_AGENT}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("extract") or data.get("description") or ""
            elif resp.status_code == 404:
                logger.debug("Wikipedia: no page for '%s'", entity_name)
            else:
                logger.debug("Wikipedia: HTTP %d for '%s'", resp.status_code, entity_name)
        except Exception as e:
            logger.debug("Wikipedia fetch failed for '%s': %s", entity_name, e)
    return None


# ── Neo4j Entity Fetch ────────────────────────────────────────────────────────

def fetch_yago_entities(neo4j_uri: str, neo4j_user: str, neo4j_pass: str, limit: int = 0) -> list[dict]:
    """Fetch all Neo4j entities with source='yago'."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        logger.error("neo4j driver not installed. Run: pip install neo4j")
        sys.exit(1)
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    lim_clause = f"LIMIT {limit}" if limit else ""
    cypher = f"MATCH (e:Entity {{source: 'yago'}}) RETURN e.name AS name, e.type AS type, id(e) AS neo4j_id {lim_clause}"
    with driver.session() as session:
        result = list(session.run(cypher))
    driver.close()
    return [{"name": r["name"], "type": r["type"], "neo4j_id": str(r["neo4j_id"])} for r in result]


# ── ChromaDB Storage ──────────────────────────────────────────────────────────

def get_chroma_collection(chroma_url: str):
    import chromadb
    client = chromadb.HttpClient(host=chroma_url.split("//")[-1].split(":")[0],
                                 port=int(chroma_url.split(":")[-1]))
    return client.get_or_create_collection(_CHROMA_COLL)


def upsert_chunks(collection, entity: dict, chunks: list[str], embeddings: list[list[float]]):
    """Idempotent upsert: chunk_id = hash(entity_name + chunk_index)."""
    ids       = []
    documents = []
    metas     = []
    embs      = []
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        chunk_id = hashlib.sha256(f"{entity['name']}::{i}".encode()).hexdigest()[:32]
        ids.append(chunk_id)
        documents.append(chunk)
        metas.append({"entity_name": entity["name"], "entity_type": entity["type"],
                       "neo4j_id": entity.get("neo4j_id", ""), "chunk_index": i,
                       "source": "wikipedia"})
        embs.append(emb)
    collection.upsert(ids=ids, documents=documents, metadatas=metas, embeddings=embs)


# ── Embedding ─────────────────────────────────────────────────────────────────

def load_embedder():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(_EMBED_MODEL)
    except ImportError:
        logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
        sys.exit(1)


# ── Main Pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(entities: list[dict], collection, embedder,
                       concurrency: int = _RATE_LIMIT) -> int:
    import httpx
    semaphore = asyncio.Semaphore(concurrency)
    total = 0

    async with httpx.AsyncClient() as session:
        for i in range(0, len(entities), concurrency * 4):
            batch = entities[i:i + concurrency * 4]
            tasks = [fetch_summary(session, e["name"], semaphore) for e in batch]
            summaries = await asyncio.gather(*tasks)

            for entity, summary in zip(batch, summaries):
                if not summary:
                    continue
                chunks = chunk_text(summary)
                if not chunks:
                    continue
                embeddings = embedder.encode(chunks, show_progress_bar=False).tolist()
                upsert_chunks(collection, entity, chunks, embeddings)
                total += len(chunks)

            if i % 100 == 0:
                logger.info("Progress: %d/%d entities processed, %d chunks embedded",
                            min(i + len(batch), len(entities)), len(entities), total)

    return total


def main():
    parser = argparse.ArgumentParser(description="Embed Wikipedia abstracts for YAGO entities")
    parser.add_argument("--limit",       type=int, default=0,   help="Max entities to process")
    parser.add_argument("--concurrency", type=int, default=_RATE_LIMIT)
    parser.add_argument("--entity",      action="append", default=[], help="Specific entity name(s)")
    parser.add_argument("--neo4j-uri",   default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user",  default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-pass",  default=os.getenv("NEO4J_PASS", "neo4j"))
    parser.add_argument("--chroma-url",  default=os.getenv("CHROMADB_URL", "http://localhost:8000"))
    args = parser.parse_args()

    if args.entity:
        entities = [{"name": n, "type": "Tech_Concept", "neo4j_id": ""} for n in args.entity]
    else:
        logger.info("Fetching YAGO entities from Neo4j …")
        entities = fetch_yago_entities(args.neo4j_uri, args.neo4j_user, args.neo4j_pass, args.limit)
        logger.info("Found %d YAGO entities", len(entities))

    if not entities:
        logger.warning("No entities to embed. Run import_yago_to_neo4j.py first.")
        return

    collection = get_chroma_collection(args.chroma_url)
    embedder   = load_embedder()

    total_chunks = asyncio.run(run_pipeline(entities, collection, embedder, args.concurrency))
    logger.info("Done. %d chunks embedded in ChromaDB collection '%s'", total_chunks, _CHROMA_COLL)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/ingest_pdf_knowledge.py — PDF Developer Documentation & Tutorial Batch Importer.

Extracts text, code blocks, and architectural concepts from PDF files and ingests them into:
  1. Neo4j Knowledge Graph (:Document, :Page, :Chunk, :CodeEntity, :Concept nodes)
  2. ChromaDB Vector Store (collection: moe_pdf_knowledge)

Usage:
  # Ingest single PDF file:
  python3 scripts/ingest_pdf_knowledge.py --path /path/to/fastapi_guide.pdf

  # Ingest directory of PDFs:
  python3 scripts/ingest_pdf_knowledge.py --path /path/to/pdf_docs_folder/

  # Dry-run preview:
  python3 scripts/ingest_pdf_knowledge.py --path /path/to/doc.pdf --dry-run
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
logger = logging.getLogger("ingest_pdf_knowledge")

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

def _get_neo4j_credentials():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD") or os.getenv("NEO4J_PASS") or ""
    return uri, user, password

def _get_chroma_config():
    host = os.getenv("CHROMA_HOST", "localhost")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    return host, port

def _hash_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

# ── Code & Concept Extraction ──────────────────────────────────────────────────
def extract_code_entities(text: str) -> List[Dict[str, str]]:
    """Extracts classes, functions, and decorators from code blocks in text."""
    entities = []
    
    # Class definitions
    classes = re.findall(r'\bclass\s+([A-Za-z0-9_]+)\b', text)
    for c in classes:
        entities.append({"name": c, "type": "class"})
        
    # Function & Method definitions
    funcs = re.findall(r'\b(?:async\s+)?def\s+([A-Za-z0-9_]+)\b', text)
    for f in funcs:
        if f not in ("__init__", "__str__", "__repr__"):
            entities.append({"name": f, "type": "function"})
            
    # Framework Decorators (e.g. @app.get, @router.post)
    decorators = re.findall(r'@[A-Za-z0-9_.]+', text)
    for d in decorators[:5]:
        entities.append({"name": d, "type": "decorator"})
        
    return entities

def extract_concepts(text: str) -> List[str]:
    """Extracts key architectural concepts and technology keywords."""
    keywords = [
        "FastAPI", "Pydantic", "SQLAlchemy", "PostgreSQL", "Redis", "Valkey", "Neo4j", "ChromaDB",
        "Docker", "Kubernetes", "GraphQL", "REST", "JWT", "OAuth2", "Middleware", "Dependency Injection",
        "Asyncio", "Celery", "Kafka", "GraphRAG", "Vector Search", "Prometheus", "Grafana", "Caddy",
        "DeepSpeed", "PyTorch", "Transformers", "LoRA", "QLoRA", "SFT", "DPO", "GGUF"
    ]
    found = set()
    for kw in keywords:
        if re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE):
            found.add(kw)
    return sorted(list(found))

# ── PDF Extraction Engine ─────────────────────────────────────────────────────
def parse_pdf_document(pdf_path: Path, max_chars_per_chunk: int = 1200) -> Dict[str, Any]:
    """Parses PDF page by page into structured chunks with code & concept tags."""
    try:
        import pypdf
    except ImportError:
        logger.error("Package 'pypdf' not found. Run: pip install pypdf")
        sys.exit(1)
        
    reader = pypdf.PdfReader(str(pdf_path))
    doc_info = {
        "file_name": pdf_path.name,
        "file_path": str(pdf_path.resolve()),
        "total_pages": len(reader.pages),
        "chunks": []
    }
    
    chunk_index = 0
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            continue
            
        # Split page text into chunks while preserving paragraph boundaries
        paragraphs = text.split("\n\n")
        current_chunk = ""
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
                
            if len(current_chunk) + len(para) <= max_chars_per_chunk:
                current_chunk += ("\n\n" if current_chunk else "") + para
            else:
                if current_chunk:
                    c_id = f"{pdf_path.stem}_p{page_num}_c{chunk_index}_{_hash_id(current_chunk)}"
                    doc_info["chunks"].append({
                        "id": c_id,
                        "page_number": page_num,
                        "chunk_index": chunk_index,
                        "text": current_chunk,
                        "code_entities": extract_code_entities(current_chunk),
                        "concepts": extract_concepts(current_chunk)
                    })
                    chunk_index += 1
                current_chunk = para
                
        if current_chunk:
            c_id = f"{pdf_path.stem}_p{page_num}_c{chunk_index}_{_hash_id(current_chunk)}"
            doc_info["chunks"].append({
                "id": c_id,
                "page_number": page_num,
                "chunk_index": chunk_index,
                "text": current_chunk,
                "code_entities": extract_code_entities(current_chunk),
                "concepts": extract_concepts(current_chunk)
            })
            chunk_index += 1

    return doc_info

# ── Database Ingestion ─────────────────────────────────────────────────────────
def ingest_pdf_to_chroma(doc_info: Dict[str, Any], host: str, port: int, dry_run: bool = False) -> int:
    chunks = doc_info["chunks"]
    if dry_run or not chunks:
        return len(chunks)
        
    try:
        import chromadb
        client = chromadb.HttpClient(host=host, port=port)
        collection = client.get_or_create_collection(name="moe_pdf_knowledge")
        
        ids = [c["id"] for c in chunks]
        documents = [c["text"] for c in chunks]
        metadatas = [{
            "source_file": doc_info["file_name"],
            "page_number": c["page_number"],
            "chunk_index": c["chunk_index"],
            "concepts": ", ".join(c["concepts"]),
            "code_entities": ", ".join([e["name"] for e in c["code_entities"]])
        } for c in chunks]
        
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(chunks)
    except Exception as e:
        logger.warning("ChromaDB ingestion failed for %s: %s", doc_info["file_name"], e)
        return 0

def ingest_pdf_to_neo4j(doc_info: Dict[str, Any], uri: str, user: str, password: str, dry_run: bool = False) -> int:
    chunks = doc_info["chunks"]
    if dry_run or not chunks or not password:
        if not password:
            logger.info("Neo4j password unconfigured — skipping Neo4j graph write")
        return 0
        
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(user, password))
        
        query = """
        MERGE (d:Document {name: $doc.file_name})
        SET d.file_path = $doc.file_path, d.total_pages = $doc.total_pages, d.updated_at = timestamp()
        
        WITH d
        UNWIND $chunks AS item
        MERGE (p:Page {doc: $doc.file_name, number: item.page_number})
        MERGE (d)-[:HAS_PAGE]->(p)
        
        MERGE (c:Chunk {id: item.id})
        SET c.text = substring(item.text, 0, 500), c.updated_at = timestamp()
        MERGE (p)-[:CONTAINS_CHUNK]->(c)
        
        WITH c, item
        UNWIND item.code_entities AS entity
        MERGE (e:CodeEntity {name: entity.name})
        SET e.type = entity.type
        MERGE (c)-[:DEFINES_CODE]->(e)
        
        WITH c, item
        UNWIND item.concepts AS concept_name
        MERGE (k:Concept {name: concept_name})
        MERGE (c)-[:MENTIONS_CONCEPT]->(k)
        """
        
        with driver.session() as session:
            session.run(query, doc={
                "file_name": doc_info["file_name"],
                "file_path": doc_info["file_path"],
                "total_pages": doc_info["total_pages"]
            }, chunks=chunks)
            
        driver.close()
        return len(chunks)
    except Exception as e:
        logger.warning("Neo4j ingestion failed for %s: %s", doc_info["file_name"], e)
        return 0

# ── Main CLI Runner ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="PDF Knowledge & Developer Documentation Importer")
    parser.add_argument("--path", "-p", required=True, help="Path to a PDF file or directory containing PDFs")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for DB upserts")
    parser.add_argument("--dry-run", action="store_true", help="Parse PDF without writing to DBs")
    args = parser.parse_args()
    
    target_path = Path(args.path).resolve()
    if not target_path.exists():
        logger.error("Specified path does not exist: %s", target_path)
        sys.exit(1)
        
    pdf_files: List[Path] = []
    if target_path.is_file():
        if target_path.suffix.lower() == ".pdf":
            pdf_files.append(target_path)
        else:
            logger.error("Specified file is not a .pdf: %s", target_path)
            sys.exit(1)
    elif target_path.is_dir():
        pdf_files = sorted(list(target_path.glob("**/*.pdf")))
        
    if not pdf_files:
        logger.info("No PDF files found at path: %s", target_path)
        sys.exit(0)
        
    neo4j_uri, neo4j_user, neo4j_pass = _get_neo4j_credentials()
    chroma_host, chroma_port = _get_chroma_config()
    
    logger.info("Starting PDF Knowledge Ingestion Pipeline:")
    logger.info("  Target Path  : %s (%d PDF files)", target_path, len(pdf_files))
    logger.info("  Neo4j URI    : %s", neo4j_uri)
    logger.info("  Chroma Host  : %s:%d", chroma_host, chroma_port)
    logger.info("  Dry Run      : %s", args.dry_run)
    logger.info("------------------------------------------------------------------")
    
    total_pages = 0
    total_chunks = 0
    start_time = time.time()
    
    for pdf_file in pdf_files:
        logger.info("Parsing PDF: %s ...", pdf_file.name)
        doc_info = parse_pdf_document(pdf_file)
        
        c_cnt = len(doc_info["chunks"])
        total_pages += doc_info["total_pages"]
        total_chunks += c_cnt
        
        logger.info("  -> %d pages, %d chunks parsed.", doc_info["total_pages"], c_cnt)
        
        if c_cnt > 0:
            c_res = ingest_pdf_to_chroma(doc_info, chroma_host, chroma_port, dry_run=args.dry_run)
            n_res = ingest_pdf_to_neo4j(doc_info, neo4j_uri, neo4j_user, neo4j_pass, dry_run=args.dry_run)
            logger.info("  -> Ingested: %d chunks to ChromaDB, %d graph nodes to Neo4j.", c_res, n_res)

    elapsed = time.time() - start_time
    logger.info("------------------------------------------------------------------")
    logger.info("PDF Ingestion Completed in %.2fs!", elapsed)
    logger.info("  Total PDF Files : %d", len(pdf_files))
    logger.info("  Total Pages     : %d", total_pages)
    logger.info("  Total Chunks    : %d", total_chunks)

if __name__ == "__main__":
    main()

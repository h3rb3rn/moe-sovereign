#!/usr/bin/env python3
"""
GraphRAG Pipeline Worker — Ingests Markdown documentation into the Neo4j knowledge graph.

Usage:
  python graphrag_pipeline_worker.py [--sources-dir /path/to/docs] [--dry-run]

Default sources (when --sources-dir is not provided):
  - ./SYSTEM.md, ./KAFKA.md, ./CHANGELOG.md
  - ./docs/**/*.md

Uses existing graph_rag/manager.py:extract_and_ingest() — no new Neo4j code.
"""

from __future__ import annotations
import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

# Load .env so NEO4J_* and judge config are available
try:
    from dotenv import load_dotenv
    load_dotenv("/app/.env", override=True)
except ImportError:
    pass

from langchain_openai import ChatOpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("graphrag-pipeline")

# ── Configuration ────────────────────────────────────────────────────────────
NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://neo4j-knowledge:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "")

JUDGE_MODEL    = os.getenv("JUDGE_MODEL", "")
JUDGE_ENDPOINT = os.getenv("JUDGE_ENDPOINT", "")

import json as _json
_inf_raw = os.getenv("INFERENCE_SERVERS", "")
_inf_servers = _json.loads(_inf_raw) if _inf_raw.strip() else []
_judge_url = next(
    (s["url"] for s in _inf_servers if s.get("name") == JUDGE_ENDPOINT),
    os.getenv("JUDGE_URL", ""),
)

# Default sources — resolved relative to MOE_PROJECT_ROOT env var
_PROJECT_ROOT = Path(os.getenv("MOE_PROJECT_ROOT", "/opt/moe-infra"))
DEFAULT_SOURCES = [
    str(_PROJECT_ROOT / "SYSTEM.md"),
    str(_PROJECT_ROOT / "KAFKA.md"),
    str(_PROJECT_ROOT / "SECURITY_TASKS.md"),
    str(_PROJECT_ROOT / "CHANGELOG.md"),
]
EXPERT_PROMPTS_DIR = os.getenv("EXPERT_PROMPTS_DIR", str(_PROJECT_ROOT / "prompts" / "systemprompt"))

# ── Markdown chunking ────────────────────────────────────────────────────────
_HEADING_RE = re.compile(r'^#{1,4}\s+', re.MULTILINE)

def chunk_markdown(text: str, max_words: int = 400) -> list[str]:
    """Splits Markdown at headings into chunks of at most max_words each."""
    parts = _HEADING_RE.split(text)
    # Re-attach headings
    headings = [""] + _HEADING_RE.findall(text)
    chunks: list[str] = []
    for h, p in zip(headings, parts):
        combined = (h + p).strip()
        if not combined:
            continue
        words = combined.split()
        # Split oversized chunks further
        while len(words) > max_words:
            chunks.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if words:
            chunks.append(" ".join(words))
    return [c for c in chunks if len(c.split()) >= 10]  # Discard too-short snippets


def collect_sources(sources_dir: str | None) -> list[tuple[str, str]]:
    """Returns a list of (filepath, content) tuples."""
    files: list[Path] = []

    if sources_dir:
        p = Path(sources_dir)
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = list(p.rglob("*.md"))
    else:
        # Default sources + expert prompts
        for src in DEFAULT_SOURCES:
            fp = Path(src)
            if fp.exists():
                files.append(fp)
        ep = Path(EXPERT_PROMPTS_DIR)
        if ep.exists():
            files.extend(ep.glob("*.md"))

    result = []
    for fp in sorted(files):
        try:
            content = fp.read_text(encoding="utf-8")
            result.append((str(fp), content))
            logger.info(f"  Source: {fp.name} ({len(content.split())} words)")
        except Exception as e:
            logger.warning(f"  Skipping {fp}: {e}")
    return result


async def ingest_file(
    path: str,
    content: str,
    manager,
    llm,
    dry_run: bool = False,
) -> int:
    """Processes a single file: chunking → extract & ingest."""
    source_name = Path(path).name
    chunks = chunk_markdown(content)
    logger.info(f"  {source_name}: {len(chunks)} Chunks")
    ingested = 0
    for i, chunk in enumerate(chunks):
        if dry_run:
            logger.info(f"    [DRY-RUN] Chunk {i+1}/{len(chunks)}: {chunk[:80]}…")
            continue
        try:
            await manager.extract_and_ingest(
                question=f"[DOC:{source_name}] Chunk {i+1}",
                answer=chunk,
                llm=llm,
                domain=source_name.replace(".md", ""),
            )
            ingested += 1
        except Exception as e:
            logger.warning(f"    Chunk {i+1} ingest error: {e}")
        # Short pause to avoid overloading the judge LLM
        await asyncio.sleep(0.5)
    return ingested


async def run(sources_dir: str | None, dry_run: bool) -> None:
    from graph_rag import GraphRAGManager

    logger.info("=== GraphRAG Pipeline Worker ===")
    logger.info(f"Neo4j: {NEO4J_URI}")
    logger.info(f"Judge: {JUDGE_MODEL} @ {_judge_url}")
    logger.info(f"Dry-Run: {dry_run}")

    # Initialize manager
    manager = GraphRAGManager(NEO4J_URI, NEO4J_USER, NEO4J_PASS)
    await manager.setup()

    # Judge LLM for triple extraction
    llm = ChatOpenAI(
        model=JUDGE_MODEL,
        base_url=_judge_url,
        api_key="ollama",
        timeout=120,
    )

    # Load sources
    sources = collect_sources(sources_dir)
    if not sources:
        logger.error("No source files found — aborting.")
        await manager.close()
        return

    total = 0
    for path, content in sources:
        n = await ingest_file(path, content, manager, llm, dry_run)
        total += n
        logger.info(f"  ✓ {Path(path).name}: {n} Tripel ingested")

    stats = await manager.get_stats()
    logger.info(
        f"\n=== Done ===\n"
        f"  {total} triples processed\n"
        f"  Neo4j: {stats['entities']} entities, {stats['relations']} relations"
    )
    await manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GraphRAG Pipeline Worker")
    parser.add_argument(
        "--sources-dir",
        default=None,
        help="Path to Markdown files/directory (default: built-in sources)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show chunks, do not write to Neo4j",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.sources_dir, args.dry_run))
    except KeyboardInterrupt:
        logger.info("Abgebrochen.")
        sys.exit(0)

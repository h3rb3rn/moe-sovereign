#!/usr/bin/env python3
"""
scripts/cron_knowledge_ingestion.py — Scheduled Cron Worker for User Uploaded Documents.

Scans the document uploads directory (/app/data/user_uploads), identifies un-ingested files
(PDF, TXT, JSON, JSONL, MD), and ingests them into Neo4j Knowledge Graph & ChromaDB Vector Store.

Can be run:
  1. Manually / via Cron schedule: `python3 scripts/cron_knowledge_ingestion.py`
  2. As a continuous loop service: `python3 scripts/cron_knowledge_ingestion.py --loop --interval 300`
"""

import argparse
import json
import logging
import os
import sys
import subprocess
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("cron_knowledge_ingestion")

UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "/app/data/user_uploads"))
MANIFEST_FILE = UPLOADS_DIR / ".ingestion_manifest.json"

def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_manifest(manifest: dict):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

def run_ingestion_for_file(file_path: Path) -> dict:
    """Invokes the appropriate ingestion script depending on file extension."""
    script_dir = Path(__file__).resolve().parent
    ext = file_path.suffix.lower()
    
    logger.info("Processing file for ingestion: %s (%s)", file_path.name, ext)
    
    if ext == ".pdf":
        cmd = [sys.executable, str(script_dir / "ingest_pdf_knowledge.py"), "--path", str(file_path)]
    elif ext in (".txt", ".json", ".jsonl", ".md"):
        cmd = [sys.executable, str(script_dir / "ingest_corpora_batch.py"), "--file", str(file_path)]
    else:
        logger.warning("Unsupported file type: %s", ext)
        return {"status": "unsupported", "error": f"Extension {ext} not supported"}

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info("  ✓ Successfully ingested %s", file_path.name)
        return {
            "status": "ingested",
            "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "output": res.stdout[-500:] if res.stdout else ""
        }
    except subprocess.CalledProcessError as e:
        logger.error("  ✗ Ingestion failed for %s: %s", file_path.name, e.stderr)
        return {
            "status": "error",
            "error": e.stderr[-500:] if e.stderr else str(e)
        }

def run_cron_cycle():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    
    files = [f for f in UPLOADS_DIR.iterdir() if f.is_file() and not f.name.startswith(".")]
    if not files:
        logger.info("No documents found in %s", UPLOADS_DIR)
        return

    processed = 0
    for file_path in files:
        fname = file_path.name
        record = manifest.get(fname, {})
        
        # Ingest if status is 'pending' or not recorded
        if record.get("status") in ("ingested", "failed"):
            continue
            
        logger.info("Starting scheduled ingestion for: %s", fname)
        result = run_ingestion_for_file(file_path)
        
        manifest[fname] = {
            "file_name": fname,
            "size_bytes": file_path.stat().st_size,
            "status": result["status"],
            "ingested_at": result.get("ingested_at"),
            "error": result.get("error")
        }
        processed += 1

    save_manifest(manifest)
    logger.info("Cron ingestion cycle finished. Processed %d file(s).", processed)

def main():
    parser = argparse.ArgumentParser(description="Scheduled Cron Ingestion Worker for User Uploaded Documents")
    parser.add_argument("--loop", action="store_true", help="Run continuously as a periodic worker loop")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval in seconds (default: 300s / 5 min)")
    args = parser.parse_args()
    
    if args.loop:
        logger.info("Starting continuous Cron Knowledge Ingestion loop (interval: %ds) ...", args.interval)
        while True:
            try:
                run_cron_cycle()
            except Exception as e:
                logger.error("Error in cron cycle: %s", e)
            time.sleep(args.interval)
    else:
        run_cron_cycle()

if __name__ == "__main__":
    main()

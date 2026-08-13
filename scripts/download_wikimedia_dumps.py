#!/usr/bin/env python3
"""
scripts/download_wikimedia_dumps.py — Download & Parse Official Wikimedia Abstracts Dumps.

Downloads Wikimedia abstract XML dumps (e.g. German dewiki or English enwiki),
parses the <title>, <abstract>, and <url> fields into clean JSONL corpora in
data/corpora/wikimedia_<lang>_abstracts.jsonl, and optionally triggers batch
ingestion into Neo4j and ChromaDB.

Usage:
  python3 scripts/download_wikimedia_dumps.py --lang de --max-articles 50000
  python3 scripts/download_wikimedia_dumps.py --lang en --max-articles 50000 --ingest
"""

import argparse
import gzip
import json
import logging
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("wikimedia_downloader")

DUMP_URL_TEMPLATE = "https://dumps.wikimedia.org/{lang}wiki/latest/{lang}wiki-latest-abstract.xml.gz"

def download_dump(lang: str, target_file: Path):
    url = DUMP_URL_TEMPLATE.format(lang=lang)
    logger.info("Downloading Wikimedia %s dump from: %s ...", lang.upper(), url)
    
    headers = {"User-Agent": "MoE-Sovereign/1.0 (https://github.com/moe-sovereign; contact@moe.local)"}
    req = urllib.request.Request(url, headers=headers)
    
    with urllib.request.urlopen(req) as resp, open(target_file, "wb") as out_f:
        shuffled = 0
        block_size = 1024 * 1024  # 1 MB
        while True:
            buffer = resp.read(block_size)
            if not buffer:
                break
            out_f.write(buffer)
            shuffled += len(buffer)
            logger.info("Downloaded %.2f MB ...", shuffled / (1024 * 1024))
            
    logger.info("Download completed: %s (%.2f MB)", target_file, target_file.stat().st_size / (1024 * 1024))

def parse_abstract_xml(gz_file: Path, out_jsonl: Path, max_articles: int = 50000) -> int:
    logger.info("Parsing XML dump %s -> %s (max_articles=%d) ...", gz_file.name, out_jsonl.name, max_articles)
    count = 0
    
    with gzip.open(gz_file, "rb") as f, open(out_jsonl, "w", encoding="utf-8") as out_f:
        # Context iterparse for memory-efficient XML parsing of large dumps
        context = ET.iterparse(f, events=("end",))
        for event, elem in context:
            if elem.tag == "doc":
                title_elem = elem.find("title")
                abstract_elem = elem.find("abstract")
                url_elem = elem.find("url")
                
                title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                abstract = abstract_elem.text.strip() if abstract_elem is not None and abstract_elem.text else ""
                url = url_elem.text.strip() if url_elem is not None and url_elem.text else ""
                
                # Clean title ("Wikipedia: Title" -> "Title")
                if ":" in title:
                    title = title.split(":", 1)[1].strip()
                    
                if title and abstract and len(abstract) > 30:
                    rec = {
                        "topic": title,
                        "title": title,
                        "text": f"# {title}\n\n{abstract}",
                        "url": url,
                        "source": "wikimedia_abstracts"
                    }
                    out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    count += 1
                    
                    if count % 5000 == 0:
                        logger.info("Parsed %d articles ...", count)
                        
                    if max_articles > 0 and count >= max_articles:
                        break
                        
                elem.clear()  # Free memory
                
    logger.info("Parsed %d Wikipedia abstract articles into %s", count, out_jsonl)
    return count

def main():
    parser = argparse.ArgumentParser(description="Wikimedia Dump Downloader & Extractor")
    parser.add_argument("--lang", default="de", choices=["de", "en", "fr", "es"], help="Wikipedia language (default: de)")
    parser.add_argument("--max-articles", type=int, default=50000, help="Maximum articles to parse (0 = all)")
    parser.add_argument("--ingest", action="store_true", help="Automatically trigger batch ingestion into DBs after parse")
    args = parser.parse_args()
    
    repo_root = Path(__file__).resolve().parent.parent
    corpora_dir = repo_root / "data" / "corpora"
    raw_dir = repo_root / "data" / "raw_dumps"
    
    corpora_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    gz_path = raw_dir / f"{args.lang}wiki-latest-abstract.xml.gz"
    out_jsonl = corpora_dir / f"wikimedia_{args.lang}_abstracts.jsonl"
    
    if not gz_path.exists():
        download_dump(args.lang, gz_path)
    else:
        logger.info("Using existing dump file: %s", gz_path)
        
    count = parse_abstract_xml(gz_path, out_jsonl, max_articles=args.max_articles)
    
    if args.ingest and count > 0:
        logger.info("Triggering batch ingestion script...")
        ingest_script = repo_root / "scripts" / "ingest_corpora_batch.py"
        os.system(f"python3 {ingest_script}")

if __name__ == "__main__":
    main()

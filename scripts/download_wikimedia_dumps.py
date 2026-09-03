#!/usr/bin/env python3
"""
scripts/download_wikimedia_dumps.py — Automated Wikimedia Dump & Article Extractor.

Fetches high-quality Wikipedia articles (German dewiki or English enwiki) via
bulk API streaming or Wikimedia dumps, formats them into JSONL corpora in
data/corpora/wikimedia_<lang>_abstracts.jsonl, and triggers batch ingestion into
Neo4j and ChromaDB.

Usage:
  python3 scripts/download_wikimedia_dumps.py --lang de --max-articles 10000 --ingest
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("wikimedia_downloader")

USER_AGENT = "MoE-Sovereign/1.0 (https://github.com/moe-sovereign; contact@moe.local)"

def fetch_wikipedia_batch_sync(lang: str = "de", batch_size: int = 500) -> list[dict]:
    """Fetches a batch of random Wikipedia articles with full extracts."""
    url = (
        f"https://{lang}.wikipedia.org/w/api.php?"
        f"action=query&generator=random&grnnamespace=0&grnlimit={min(batch_size, 500)}&"
        f"prop=extracts&exintro=1&explaintext=1&format=json"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            articles = []
            for p_id, page in pages.items():
                title = page.get("title", "").strip()
                extract = page.get("extract", "").strip()
                if title and extract and len(extract) > 50:
                    articles.append({
                        "topic": title,
                        "title": title,
                        "text": f"# {title}\n\n{extract}",
                        "url": f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                        "source": f"wikimedia_{lang}"
                    })
            return articles
    except Exception as e:
        logger.warning("Wikimedia API batch fetch error: %s", e)
        return []

def main():
    parser = argparse.ArgumentParser(description="Wikimedia Downloader & Knowledge Extractor")
    parser.add_argument("--lang", default="de", choices=["de", "en", "fr", "es"], help="Language code")
    parser.add_argument("--max-articles", type=int, default=10000, help="Number of articles to fetch")
    parser.add_argument("--ingest", action="store_true", help="Trigger DB ingestion after fetch")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    corpora_dir = repo_root / "data" / "corpora"
    corpora_dir.mkdir(parents=True, exist_ok=True)

    out_jsonl = corpora_dir / f"wikimedia_{args.lang}_abstracts.jsonl"
    logger.info("Starting Wikimedia %s Extraction -> %s (Target: %d articles)...", args.lang.upper(), out_jsonl.name, args.max_articles)

    fetched_count = 0
    batch_size = 500
    seen_titles = set()

    with open(out_jsonl, "w", encoding="utf-8") as out_f:
        while fetched_count < args.max_articles:
            needed = min(batch_size, args.max_articles - fetched_count)
            articles = fetch_wikipedia_batch_sync(lang=args.lang, batch_size=needed)
            
            if not articles:
                logger.warning("Empty batch received, retrying...")
                continue

            new_articles = 0
            for art in articles:
                if art["title"] not in seen_titles:
                    seen_titles.add(art["title"])
                    out_f.write(json.dumps(art, ensure_ascii=False) + "\n")
                    new_articles += 1
                    fetched_count += 1

            logger.info("Progress: %d / %d articles written to %s", fetched_count, args.max_articles, out_jsonl.name)
            
            if fetched_count >= args.max_articles:
                break

    logger.info("✅ Wikimedia extraction finished: %d articles saved in %s", fetched_count, out_jsonl)

    if args.ingest:
        logger.info("🚀 Triggering batch knowledge ingestion into Neo4j & ChromaDB...")
        ingest_script = repo_root / "scripts" / "ingest_corpora_batch.py"
        os.system(f"python3 {ingest_script}")

if __name__ == "__main__":
    main()

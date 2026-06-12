#!/usr/bin/env python3
"""
moe_knowledge_bundle.py — Unified export and import tool for MoE Sovereign state.
Handles Postgres databases, Valkey Thompson scores, ChromaDB caches, GraphRAG, and router models.

Usage (inside docker container moe-admin/langgraph-orchestrator):
  python scripts/moe_knowledge_bundle.py export /app/logs/moe-knowledge-bundle.tar.gz
  python scripts/moe_knowledge_bundle.py import /app/logs/moe-knowledge-bundle.tar.gz
"""

import os
import sys
import json
import shutil
import tarfile
import argparse
import asyncio
import subprocess
from datetime import datetime, timezone
from redis import Redis
import chromadb

# Ensure we can import graph_rag and config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config
from graph_rag import GraphRAGManager

TMP_DIR = "moe_bundle_tmp"

def get_chroma_client():
    chroma_host = os.getenv("CHROMA_HOST", "chromadb-vector")
    return chromadb.HttpClient(host=chroma_host, port=8000)

async def export_graph(neo4j_uri, neo4j_user, neo4j_pass, output_path):
    print("🕸️ Exporting GraphRAG knowledge base...")
    try:
        mgr = GraphRAGManager(neo4j_uri, neo4j_user, neo4j_pass)
        bundle = await mgr.export_knowledge_bundle(min_trust=0.0, strip_sensitive=False)
        with open(output_path, "w") as f:
            json.dump(bundle, f, indent=2)
        await mgr.close()
        print(f"✅ GraphRAG exported ({len(bundle.get('entities', []))} entities, {len(bundle.get('relations', []))} relations)")
    except Exception as e:
        print(f"⚠️ GraphRAG export failed: {e}")

async def import_graph(neo4j_uri, neo4j_user, neo4j_pass, input_path):
    print("🕸️ Importing GraphRAG knowledge base...")
    if not os.path.exists(input_path):
        print("➡️ Skipped (no graph dump in bundle)")
        return
    try:
        with open(input_path) as f:
            bundle = json.load(f)
        mgr = GraphRAGManager(neo4j_uri, neo4j_user, neo4j_pass)
        stats = await mgr.import_knowledge_bundle(bundle=bundle, dry_run=False)
        await mgr.close()
        print(f"✅ GraphRAG imported: {stats}")
    except Exception as e:
        print(f"⚠️ GraphRAG import failed: {e}")

def run_export(archive_path):
    print(f"📦 Creating Knowledge Bundle at: {archive_path}")
    if os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR)
    os.makedirs(TMP_DIR)

    # 1. Postgres Database Dump
    print("🗄️ Exporting PostgreSQL database...")
    userdb_url = os.getenv("MOE_USERDB_URL")
    if userdb_url:
        try:
            sql_file = os.path.join(TMP_DIR, "postgres_dump.sql")
            # Dump using pg_dump (assumed installed in environment)
            cmd = ["pg_dump", "-d", userdb_url, "-f", sql_file, "--clean", "--if-exists"]
            subprocess.run(cmd, check=True)
            print("✅ PostgreSQL database exported successfully")
        except Exception as e:
            print(f"⚠️ PostgreSQL export failed: {e}")
    else:
        print("⚠️ MOE_USERDB_URL not set, skipping database dump")

    # 2. Valkey Thompson Sampling Scores
    print("🧠 Exporting Valkey Thompson routing feedback loops...")
    try:
        redis_url = os.getenv("REDIS_URL", "redis://terra_cache:6379")
        r = Redis.from_url(redis_url)
        keys = r.keys("moe:routing:feedback:*")
        feedback_data = {}
        for k in keys:
            val = r.get(k)
            if val:
                feedback_data[k.decode()] = val.decode()
        
        with open(os.path.join(TMP_DIR, "valkey_feedback.json"), "w") as f:
            json.dump(feedback_data, f, indent=2)
        print(f"✅ Valkey routing feedback loops exported ({len(feedback_data)} keys)")
    except Exception as e:
        print(f"⚠️ Valkey feedback export failed: {e}")

    # 3. ChromaDB Semantic Cache
    print("🔎 Exporting ChromaDB semantic template cache...")
    try:
        chroma_client = get_chroma_client()
        collection = chroma_client.get_collection("moe_template_cache")
        cache_data = collection.get(include=["documents", "metadatas"])
        
        with open(os.path.join(TMP_DIR, "chroma_cache.json"), "w") as f:
            json.dump({
                "ids": cache_data["ids"],
                "documents": cache_data["documents"],
                "metadatas": cache_data["metadatas"]
            }, f, indent=2)
        print(f"✅ ChromaDB template cache exported ({len(cache_data['ids'])} items)")
    except Exception as e:
        print(f"⚠️ ChromaDB cache export skipped or empty: {e}")

    # 4. Neo4j GraphRAG Knowledge Graph
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://neo4j-knowledge:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_pass = os.getenv("NEO4J_PASS")
    graph_json = os.path.join(TMP_DIR, "graph_knowledge.json")
    asyncio.run(export_graph(neo4j_uri, neo4j_user, neo4j_pass, graph_json))

    # 5. Router Weights
    print("🤖 Exporting Sovereign Router ONNX weights...")
    router_src = "models/sovereign_router.onnx"
    if os.path.exists(router_src):
        shutil.copy(router_src, os.path.join(TMP_DIR, "sovereign_router.onnx"))
        print("✅ Router ONNX model weights added to bundle")
    else:
        print("➡️ Skipped (no local sovereign_router.onnx model found)")

    # 6. Tar and Gzip Package
    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(TMP_DIR, arcname=".")
        print(f"🎉 Knowledge Bundle successfully packed: {archive_path}")
    except Exception as e:
        print(f"❌ Packing archive failed: {e}")
    finally:
        shutil.rmtree(TMP_DIR)


def run_import(archive_path):
    print(f"📦 Importing Knowledge Bundle from: {archive_path}")
    if not os.path.exists(archive_path):
        print(f"❌ Import archive path does not exist: {archive_path}")
        sys.exit(1)

    if os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR)
    os.makedirs(TMP_DIR)

    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(TMP_DIR)
        print("✅ Archive unpacked successfully")
    except Exception as e:
        print(f"❌ Unpacking archive failed: {e}")
        shutil.rmtree(TMP_DIR)
        sys.exit(1)

    # 1. Postgres Database Restore
    print("🗄️ Restoring PostgreSQL database...")
    userdb_url = os.getenv("MOE_USERDB_URL")
    sql_file = os.path.join(TMP_DIR, "postgres_dump.sql")
    if userdb_url and os.path.exists(sql_file):
        try:
            cmd = ["psql", "-d", userdb_url, "-f", sql_file]
            subprocess.run(cmd, check=True)
            print("✅ PostgreSQL database restored successfully")
        except Exception as e:
            print(f"⚠️ PostgreSQL import failed: {e}")
    else:
        print("➡️ Skipped (no database dump in bundle or MOE_USERDB_URL unset)")

    # 2. Valkey Thompson Sampling Scores Restore
    print("🧠 Restoring Valkey Thompson routing feedback loops...")
    valkey_file = os.path.join(TMP_DIR, "valkey_feedback.json")
    if os.path.exists(valkey_file):
        try:
            with open(valkey_file) as f:
                feedback_data = json.load(f)
            redis_url = os.getenv("REDIS_URL", "redis://terra_cache:6379")
            r = Redis.from_url(redis_url)
            for k, v in feedback_data.items():
                r.set(k, v)
            print(f"✅ Valkey routing feedback loops restored ({len(feedback_data)} keys)")
        except Exception as e:
            print(f"⚠️ Valkey feedback import failed: {e}")
    else:
        print("➡️ Skipped (no Valkey keys in bundle)")

    # 3. ChromaDB Semantic Cache Restore
    print("🔎 Restoring ChromaDB semantic template cache...")
    chroma_file = os.path.join(TMP_DIR, "chroma_cache.json")
    if os.path.exists(chroma_file):
        try:
            with open(chroma_file) as f:
                cache_data = json.load(f)
            chroma_client = get_chroma_client()
            collection = chroma_client.get_or_create_collection("moe_template_cache")
            if cache_data["ids"]:
                # Clear existing cache entries to avoid duplication key collisions
                existing = collection.get()
                if existing["ids"]:
                    collection.delete(ids=existing["ids"])
                
                collection.add(
                    ids=cache_data["ids"],
                    documents=cache_data["documents"],
                    metadatas=cache_data["metadatas"]
                )
                print(f"✅ ChromaDB template cache populated ({len(cache_data['ids'])} items)")
            else:
                print("➡️ Skipped (empty ChromaDB cache data)")
        except Exception as e:
            print(f"⚠️ ChromaDB cache import failed: {e}")
    else:
        print("➡️ Skipped (no ChromaDB cache in bundle)")

    # 4. Neo4j GraphRAG Knowledge Graph Restore
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://neo4j-knowledge:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_pass = os.getenv("NEO4J_PASS")
    graph_json = os.path.join(TMP_DIR, "graph_knowledge.json")
    asyncio.run(import_graph(neo4j_uri, neo4j_user, neo4j_pass, graph_json))

    # 5. Router Weights Restore
    print("🤖 Restoring Sovereign Router ONNX weights...")
    router_src = os.path.join(TMP_DIR, "sovereign_router.onnx")
    if os.path.exists(router_src):
        try:
            os.makedirs("models", exist_ok=True)
            shutil.copy(router_src, "models/sovereign_router.onnx")
            print("✅ Router ONNX model weights restored successfully")
        except Exception as e:
            print(f"⚠️ Router ONNX model import failed: {e}")
    else:
        print("➡️ Skipped (no model weights in bundle)")

    # Clean up
    shutil.rmtree(TMP_DIR)
    print("🎉 Import and synchronization process completed successfully!")


def main():
    parser = argparse.ArgumentParser(description="MoE Sovereign Knowledge Bundle Export & Import Utility")
    parser.add_argument("action", choices=["export", "import"], help="Action to perform")
    parser.add_argument("archive", help="Path to the .tar.gz bundle file")
    
    args = parser.parse_args()
    
    if args.action == "export":
        run_export(args.archive)
    elif args.action == "import":
        run_import(args.archive)

if __name__ == "__main__":
    main()

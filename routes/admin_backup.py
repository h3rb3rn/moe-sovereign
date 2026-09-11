"""routes/admin_backup.py — Orchestrator API route for the Admin UI's Autobackup job.

Handles the GraphRAG (Neo4j), ChromaDB semantic-cache and Valkey
routing-feedback portions of a knowledge-bundle backup — the state this
container has the drivers for. PostgreSQL itself is dumped separately by
moe-admin, which has psql/pg_dump but not the neo4j/chromadb drivers; see
admin_ui/app.py::_run_system_backup for the combined job.

Both containers bind-mount the same host directory at /app/backups, so a
plain filename here is enough to hand the resulting archive back to moe-admin
for retention enforcement.
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

import config
from graph_rag import GraphRAGManager
from services.auth import require_admin_or_system

router = APIRouter()

_BACKUP_DIR = Path("/app/backups")


@router.post("/v1/admin/backup/run", dependencies=[Depends(require_admin_or_system)])
async def run_backup(body: dict = None):
    """Exports the GraphRAG knowledge graph, ChromaDB semantic cache and
    Valkey routing-feedback state into one archive under the shared backups
    directory. Each component is best-effort: a missing/empty store is
    reported as a warning, not a failure, so one down dependency doesn't
    block backing up the others."""
    body = body or {}
    filename = body.get("filename") or f"knowledge-bundle-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.tar.gz"
    if filename != Path(filename).name or not filename.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = _BACKUP_DIR / filename
    tmp_dir = _BACKUP_DIR / f".tmp-{filename}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    start = time.monotonic()
    warnings: list[str] = []

    try:
        try:
            mgr = GraphRAGManager(config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASS)
            bundle = await mgr.export_knowledge_bundle(min_trust=0.0, strip_sensitive=False)
            await mgr.close()
            (tmp_dir / "graph_knowledge.json").write_text(json.dumps(bundle))
        except Exception as exc:
            warnings.append(f"graphrag export failed: {exc}")

        try:
            import chromadb
            client = chromadb.HttpClient(host=os.getenv("CHROMA_HOST", "chromadb-vector"), port=8000)
            collection = client.get_collection("moe_template_cache")
            cache = collection.get(include=["documents", "metadatas"])
            (tmp_dir / "chroma_cache.json").write_text(json.dumps({
                "ids": cache["ids"], "documents": cache["documents"], "metadatas": cache["metadatas"],
            }))
        except Exception as exc:
            warnings.append(f"chromadb export skipped: {exc}")

        try:
            from redis import Redis
            r = Redis.from_url(os.getenv("REDIS_URL", "redis://terra_cache:6379"))
            feedback = {
                k.decode(): v.decode()
                for k in r.keys("moe:routing:feedback:*")
                if (v := r.get(k)) is not None
            }
            (tmp_dir / "valkey_feedback.json").write_text(json.dumps(feedback))
        except Exception as exc:
            warnings.append(f"valkey export failed: {exc}")

        router_src = Path("models/sovereign_router.onnx")
        if router_src.exists():
            shutil.copy(router_src, tmp_dir / "sovereign_router.onnx")

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(tmp_dir, arcname=".")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    size_bytes = archive_path.stat().st_size if archive_path.exists() else 0
    return {
        "ok": True,
        "filename": filename,
        "size_bytes": size_bytes,
        "duration_s": round(time.monotonic() - start, 1),
        "warnings": warnings,
    }

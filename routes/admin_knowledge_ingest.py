"""routes/admin_knowledge_ingest.py — Orchestrator trigger for the uploaded-document
cron ingestion cycle.

moe-admin's Docker image never copies scripts/ (only a fixed list of admin_ui
files is baked in), so the previous implementation — moe-admin spawning
`python3 /app/scripts/cron_knowledge_ingestion.py` as a local subprocess —
always failed with FileNotFoundError, silently (the failure was never checked
or logged). This container has scripts/ and the neo4j/chromadb drivers it
needs, so the Admin UI now delegates here instead, the same way it already
delegates the Autobackup GraphRAG/ChromaDB export (routes/admin_backup.py).
"""
from fastapi import APIRouter, BackgroundTasks

router = APIRouter()


def _run_cron_cycle() -> None:
    from scripts.cron_knowledge_ingestion import run_cron_cycle
    run_cron_cycle()


@router.post("/v1/admin/knowledge/documents/ingest")
async def trigger_document_ingestion(background_tasks: BackgroundTasks):
    """Runs one cron-ingestion cycle over UPLOADS_DIR in the background."""
    background_tasks.add_task(_run_cron_cycle)
    return {"ok": True, "message": "Document ingestion cycle triggered"}

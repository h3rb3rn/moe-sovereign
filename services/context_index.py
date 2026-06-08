"""services/context_index.py — Infrastructure-level 1M+ context window.

When a Claude Code session carries a large system_prompt (file context, codebase,
document), this module chunks it into ChromaDB under a session-scoped collection
so that every expert can retrieve only the semantically relevant slice.

Architecture:
  1. chunk_and_index_context()  — called once on first large request
  2. retrieve_context_for_task() — called per expert with the task text as query
  3. get_context_toc()           — short TOC injected into the planner prompt

Redis flags:
  cc:ctx:{session_id}:indexed   → "1" when indexing is complete
  cc:ctx:{session_id}:toc       → compressed table of contents (for planner)
  cc:ctx:{session_id}:size      → original char count of the indexed context

ChromaDB collection: context_index_{session_id}  (TTL 4h via metadata expire_at)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN")

# ── Config ────────────────────────────────────────────────────────────────────

# Minimum system_prompt size (chars) to trigger indexing instead of direct pass-through.
# Below this threshold the full context is passed verbatim (no overhead).
CONTEXT_INDEX_THRESHOLD: int = int(os.getenv("CONTEXT_INDEX_THRESHOLD", "20000"))

# Chunk parameters
CHUNK_SIZE:    int = int(os.getenv("CONTEXT_CHUNK_SIZE",   "1500"))  # chars per chunk
CHUNK_OVERLAP: int = int(os.getenv("CONTEXT_CHUNK_OVERLAP", "200"))  # overlap between chunks

# How many chunks to retrieve per expert call
RETRIEVAL_TOP_K: int = int(os.getenv("CONTEXT_RETRIEVAL_TOP_K", "8"))

# TTL for the session index (seconds). Defaults to 4 hours.
INDEX_TTL_SECS: int = int(os.getenv("CONTEXT_INDEX_TTL_SECS", "14400"))

# Fallback: when no index exists, how many chars of context to pass verbatim
FALLBACK_CONTEXT_CHARS: int = int(os.getenv("CONTEXT_FALLBACK_CHARS", "8000"))


# ── ChromaDB collection helper ────────────────────────────────────────────────

def _get_chromadb_client():
    """Return a ChromaDB HttpClient connected to chromadb-vector."""
    import chromadb
    host = os.getenv("CHROMA_HOST", "chromadb-vector")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    return chromadb.HttpClient(host=host, port=port)


def _collection_name(session_id: str) -> str:
    # Sanitise: ChromaDB collection names must be 3-63 chars, alphanumeric/hyphen/underscore
    safe = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return f"ctx_{safe}"


def _get_embedding_function():
    """Reuse the embedding function from memory_retrieval if available."""
    try:
        from memory_retrieval import _get_ef
        return _get_ef()
    except Exception:
        try:
            import chromadb.utils.embedding_functions as ef
            return ef.DefaultEmbeddingFunction()
        except Exception:
            return None


# ── Chunker ───────────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks. Prefers splitting on newlines."""
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        # Try to extend to a natural boundary (newline) within 200 chars
        if end < length:
            boundary = text.rfind("\n", end - 200, end)
            if boundary > start:
                end = boundary + 1
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
    return [c for c in chunks if c.strip()]


def _build_toc(text: str, max_chars: int = 2000) -> str:
    """Extract a table of contents / summary from the first portion of the context.

    Looks for headings (markdown # or SCREAMING CAPS lines), file paths, class/function
    definitions. Returns a compact overview for the planner.
    """
    import re
    lines = text.splitlines()
    toc_lines: list[str] = []
    seen: set = set()
    patterns = [
        re.compile(r"^#{1,4}\s+(.+)"),                          # Markdown headings
        re.compile(r"^(class|def|async def|function)\s+\w+"),   # Code definitions
        re.compile(r"^(File|PATH|FILE):\s*(.+)", re.I),         # File markers
        re.compile(r"^([A-Z][A-Z0-9 _]{4,}):?\s*$"),           # SCREAMING CAPS sections
        re.compile(r"^\s*[\/\\][\w\/\\.\-]+\.(py|ts|js|go|java|cs|rs|cpp|h|sql|yaml|yml|json|md)"),  # File paths
    ]
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 3:
            continue
        for pat in patterns:
            m = pat.match(stripped)
            if m:
                entry = stripped[:120]
                if entry not in seen:
                    seen.add(entry)
                    toc_lines.append(entry)
                break
        if len("\n".join(toc_lines)) >= max_chars:
            break
    if not toc_lines:
        # Fallback: first 10 non-empty lines
        toc_lines = [l.strip()[:120] for l in lines if l.strip()][:10]
    return "\n".join(toc_lines)


# ── Public API ────────────────────────────────────────────────────────────────

async def chunk_and_index_context(
    session_id: str,
    content: str,
    redis_client,
) -> bool:
    """Chunk content into ChromaDB. Stores TOC + size in Redis. Returns True on success."""
    if not content or not session_id:
        return False
    try:
        return await asyncio.to_thread(
            _index_sync, session_id, content, redis_client
        )
    except Exception as exc:
        logger.warning("context_index: indexing failed for session %s: %s", session_id[:8], exc)
        return False


def _index_sync(session_id: str, content: str, redis_client) -> bool:
    """Synchronous indexing — runs in thread pool."""
    import asyncio as _asyncio
    chunks = _chunk_text(content)
    if not chunks:
        return False

    client = _get_chromadb_client()
    ef = _get_embedding_function()
    coll_name = _collection_name(session_id)

    try:
        try:
            client.delete_collection(coll_name)
        except Exception:
            pass
        kw: dict = {"name": coll_name, "metadata": {"hnsw:space": "cosine"}}
        if ef is not None:
            kw["embedding_function"] = ef
        coll = client.get_or_create_collection(**kw)
    except Exception as exc:
        logger.warning("context_index: collection create failed: %s", exc)
        return False

    expire_at = int(time.time()) + INDEX_TTL_SECS
    docs, ids, metas = [], [], []
    for i, chunk in enumerate(chunks):
        fp = hashlib.sha256(f"{session_id}:{i}:{chunk[:60]}".encode()).hexdigest()[:32]
        docs.append(chunk)
        ids.append(fp)
        metas.append({
            "session_id": session_id,
            "chunk_index": i,
            "expire_at": expire_at,
            "source": "system_prompt",
        })

    try:
        coll.upsert(documents=docs, ids=ids, metadatas=metas)
    except Exception as exc:
        logger.warning("context_index: upsert failed: %s", exc)
        return False

    toc = _build_toc(content)

    # Store flags in Redis synchronously via asyncio.run_coroutine_threadsafe if needed
    # We use a simple fire-and-forget pattern here
    if redis_client is not None:
        async def _set_flags():
            try:
                await redis_client.set(f"cc:ctx:{session_id}:indexed", "1", ex=INDEX_TTL_SECS)
                await redis_client.set(f"cc:ctx:{session_id}:toc", toc[:3000], ex=INDEX_TTL_SECS)
                await redis_client.set(f"cc:ctx:{session_id}:size", str(len(content)), ex=INDEX_TTL_SECS)
                await redis_client.set(f"cc:ctx:{session_id}:chunks", str(len(chunks)), ex=INDEX_TTL_SECS)
            except Exception:
                pass
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(_set_flags())
            else:
                loop.run_until_complete(_set_flags())
        except Exception:
            pass

    logger.info(
        "context_index: indexed %d chunks (%d chars) for session %s",
        len(chunks), len(content), session_id[:12],
    )
    return True


async def is_context_indexed(session_id: str, redis_client) -> bool:
    """Check if a context index exists for this session."""
    if not redis_client or not session_id:
        return False
    try:
        return bool(await redis_client.get(f"cc:ctx:{session_id}:indexed"))
    except Exception:
        return False


async def get_context_toc(session_id: str, redis_client) -> str:
    """Return the table of contents for this session's indexed context."""
    if not redis_client or not session_id:
        return ""
    try:
        raw = await redis_client.get(f"cc:ctx:{session_id}:toc")
        return raw or ""
    except Exception:
        return ""


async def retrieve_context_for_task(
    session_id: str,
    task_text: str,
    redis_client,
    n_results: int = RETRIEVAL_TOP_K,
) -> str:
    """Retrieve semantically relevant context chunks for a specific task.

    Returns a formatted string ready to be injected into an expert's system prompt.
    Falls back to empty string on any error.
    """
    if not session_id or not task_text:
        return ""
    try:
        return await asyncio.to_thread(
            _retrieve_sync, session_id, task_text, n_results
        )
    except Exception as exc:
        logger.debug("context_index: retrieval failed: %s", exc)
        return ""


def _retrieve_sync(session_id: str, query: str, n_results: int) -> str:
    client = _get_chromadb_client()
    ef = _get_embedding_function()
    coll_name = _collection_name(session_id)

    try:
        kw: dict = {"name": coll_name}
        if ef is not None:
            kw["embedding_function"] = ef
        coll = client.get_collection(**kw)
    except Exception:
        return ""

    try:
        results = coll.query(
            query_texts=[query[:500]],
            n_results=min(n_results, coll.count()),
            where={"session_id": session_id},
        )
    except Exception as exc:
        logger.debug("context_index: query failed: %s", exc)
        return ""

    docs = results.get("documents", [[]])[0]
    if not docs:
        return ""

    # Sort by chunk_index to preserve document order
    metas = results.get("metadatas", [[]])[0]
    paired = sorted(zip(metas, docs), key=lambda x: x[0].get("chunk_index", 0))
    chunks = [doc for _, doc in paired]

    return "\n\n".join(chunks)

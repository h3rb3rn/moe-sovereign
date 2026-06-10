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

# Hard cap on chunks indexed per session — bounds embedding/upsert memory and
# request time for pathologically large system prompts.
MAX_CHUNKS_PER_INDEX: int = int(os.getenv("CONTEXT_MAX_CHUNKS", "200"))

# Upsert chunks in batches to bound peak memory during embedding.
INDEX_BATCH_SIZE: int = int(os.getenv("CONTEXT_INDEX_BATCH_SIZE", "16"))


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
    """Embedding helper backed by the moe-embed sidecar (HttpxOllamaEF).

    Calls nomic-embed-text via Ollama HTTP (HttpxOllamaEF from
    memory_retrieval.py) instead of loading an in-process ONNX model. This
    keeps the embedding model out of the 4G-limited orchestrator container,
    fixing the OOM that previously forced CC_CONTEXT_INDEX_ENABLED=false.

    NOTE: this is called DIRECTLY (e.g. ``ef(texts)``) to precompute
    embeddings in the calling thread — it is intentionally NOT passed as
    ChromaDB's ``embedding_function=`` kwarg. Attaching it to the collection
    causes the Rust client to call back into Python from a different OS
    thread/runtime than the one that first initialised chromadb's client
    (e.g. the asyncio-event-loop thread vs. an ``asyncio.to_thread`` worker),
    which deadlocks for ~90s and fails with "timed out in upsert."

    Returns None (caller should skip indexing) if the sidecar helper is
    unavailable, logging a warning so the failure is visible in container logs.
    """
    try:
        from memory_retrieval import HttpxOllamaEF
        base_url = os.getenv(
            "CONTEXT_INDEX_EMBED_URL",
            os.getenv("SEMANTIC_MEMORY_EMBED_URL", "http://moe-embed:11434"),
        )
        model_name = os.getenv("CONTEXT_INDEX_EMBED_MODEL", "nomic-embed-text")
        return HttpxOllamaEF(model_name=model_name, base_url=base_url)
    except Exception as exc:
        logger.warning(
            "context_index: embedding function unavailable — Tier-3 indexing "
            "skipped for this session. Error: %s", exc
        )
        return None


# ── Chunker ───────────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks. Prefers splitting on newlines.

    Termination is guaranteed: the window always stops once it reaches the end of
    the text, and ``start`` always advances by at least ``chunk_size - overlap``
    per iteration. (A previous version omitted the end-of-text break and looped
    forever, growing ``chunks`` until the process was OOM-killed.)
    """
    # Degenerate config guard: overlap must be strictly smaller than chunk_size,
    # otherwise start = end - overlap would never advance.
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        # Try to snap to a natural boundary (newline), but only one that lies past
        # start + overlap so the window keeps moving forward.
        if end < length:
            boundary = text.rfind("\n", start + overlap, end)
            if boundary > start:
                end = boundary + 1
        chunks.append(text[start:end])
        if end >= length:           # reached the end → done (the missing termination)
            break
        start = end - overlap       # end < length here, so start strictly advances
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

async def ensure_indexed(
    session_id: str,
    content: str,
    redis_client,
    *,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> bool:
    """Idempotent entry-point: index *content* for *session_id* if not done yet.

    Safe to call from any entry-point (CC tool handler, MoE handler, chat.py) —
    the atomic Redis lock ``cc:ctx:{session_id}:indexing`` prevents duplicate jobs.

    Returns True if indexing was triggered or already complete, False on error.
    """
    if not content or not session_id or len(content) <= CONTEXT_INDEX_THRESHOLD:
        return False
    if not redis_client:
        return False

    try:
        # Fast path: already indexed — nothing to do.
        if await redis_client.get(f"cc:ctx:{session_id}:indexed"):
            return True

        # Claim exclusive in-progress lock (TTL 300s — well above any indexing time).
        # nx=True means only one caller wins; all others skip silently.
        _claimed = await redis_client.set(
            f"cc:ctx:{session_id}:indexing", "1", ex=300, nx=True
        )
        if not _claimed:
            return True  # another task is already indexing

        _ev_loop = loop or asyncio.get_event_loop()
        _ev_loop.create_task(chunk_and_index_context(session_id, content, redis_client))
        logger.info(
            "context_index.ensure_indexed: triggered for session %s (%d chars)",
            session_id[:8], len(content),
        )
        return True
    except Exception as exc:
        logger.debug("context_index.ensure_indexed: skipped for session %s: %s", session_id[:8], exc)
        return False


async def retrieve(
    session_id: str,
    query: str,
    redis_client,
    n_results: int = RETRIEVAL_TOP_K,
) -> str:
    """Retrieve relevant context chunks.  Thin wrapper over retrieve_context_for_task."""
    return await retrieve_context_for_task(
        session_id=session_id,
        task_text=query,
        redis_client=redis_client,
        n_results=n_results,
    )


async def chunk_and_index_context(
    session_id: str,
    content: str,
    redis_client,
) -> bool:
    """Chunk content into ChromaDB. Stores TOC + size in Redis. Returns True on success.

    The heavy ChromaDB embedding/upsert runs in a worker thread; the Redis flag
    writes happen here on the main event loop, because ``redis_client`` is an async
    client bound to this loop and cannot be driven from the worker thread.
    """
    if not content or not session_id:
        return False
    try:
        result = await asyncio.to_thread(_index_sync, session_id, content)
    except Exception as exc:
        logger.warning("context_index: indexing failed for session %s: %s", session_id[:8], exc)
        result = None

    if not result:
        # Indexing failed: drop the in-progress lock so a later request can retry.
        if redis_client is not None:
            try:
                await redis_client.delete(f"cc:ctx:{session_id}:indexing")
            except Exception:
                pass
        return False

    # Persist completion flags on the main loop (where the async redis client lives).
    if redis_client is not None:
        try:
            await redis_client.set(f"cc:ctx:{session_id}:indexed", "1", ex=INDEX_TTL_SECS)
            await redis_client.set(f"cc:ctx:{session_id}:toc", result["toc"][:3000], ex=INDEX_TTL_SECS)
            await redis_client.set(f"cc:ctx:{session_id}:size", str(result["size"]), ex=INDEX_TTL_SECS)
            await redis_client.set(f"cc:ctx:{session_id}:chunks", str(result["chunks"]), ex=INDEX_TTL_SECS)
        except Exception as exc:
            logger.warning("context_index: flag persist failed for session %s: %s", session_id[:8], exc)
    return True


def _index_sync(session_id: str, content: str) -> Optional[dict]:
    """Synchronous indexing — runs in a thread pool.

    Returns a metadata dict ``{"toc", "size", "chunks"}`` on success (the async
    caller persists these to Redis), or ``None`` on failure.
    """
    chunks = _chunk_text(content)
    if not chunks:
        return None
    if len(chunks) > MAX_CHUNKS_PER_INDEX:
        logger.warning(
            "context_index: truncating %d chunks to %d for session %s (CONTEXT_MAX_CHUNKS)",
            len(chunks), MAX_CHUNKS_PER_INDEX, session_id[:12],
        )
        chunks = chunks[:MAX_CHUNKS_PER_INDEX]

    ef = _get_embedding_function()
    if ef is None:
        return None

    client = _get_chromadb_client()
    coll_name = _collection_name(session_id)

    try:
        try:
            client.delete_collection(coll_name)
        except Exception:
            pass
        # No embedding_function attached — embeddings are precomputed below and
        # passed explicitly to upsert() (see _get_embedding_function docstring).
        coll = client.get_or_create_collection(name=coll_name, metadata={"hnsw:space": "cosine"})
    except Exception as exc:
        logger.warning("context_index: collection create failed: %s", exc)
        return None

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
        for i in range(0, len(docs), INDEX_BATCH_SIZE):
            batch_docs = docs[i:i + INDEX_BATCH_SIZE]
            t_embed0 = time.time()
            embeddings = [e.tolist() for e in ef(batch_docs)]
            logger.info("context_index: batch %d embedded in %.1fs", i // INDEX_BATCH_SIZE, time.time() - t_embed0)
            t_upsert0 = time.time()
            coll.upsert(
                documents=batch_docs,
                embeddings=embeddings,
                ids=ids[i:i + INDEX_BATCH_SIZE],
                metadatas=metas[i:i + INDEX_BATCH_SIZE],
            )
            logger.info("context_index: batch %d upserted in %.1fs", i // INDEX_BATCH_SIZE, time.time() - t_upsert0)
    except Exception as exc:
        logger.warning("context_index: upsert failed: %r", exc, exc_info=True)
        return None

    toc = _build_toc(content)

    logger.info(
        "context_index: indexed %d chunks (%d chars) for session %s",
        len(chunks), len(content), session_id[:12],
    )
    return {"toc": toc, "size": len(content), "chunks": len(chunks)}


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
    ef = _get_embedding_function()
    if ef is None:
        return ""

    client = _get_chromadb_client()
    coll_name = _collection_name(session_id)

    try:
        # No embedding_function attached — see _get_embedding_function docstring;
        # the query embedding is precomputed below and passed explicitly.
        coll = client.get_collection(name=coll_name)
    except Exception:
        return ""

    try:
        query_embeddings = [e.tolist() for e in ef([query[:500]])]
        results = coll.query(
            query_embeddings=query_embeddings,
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

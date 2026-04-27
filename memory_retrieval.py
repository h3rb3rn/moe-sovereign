"""
memory_retrieval.py — Tier-2 (Warm) Semantic Memory for MoE Sovereign.

Evicted conversation turns are embedded and stored in ChromaDB. At query time,
semantically relevant past turns are retrieved and injected back into the prompt
as a WARM context block — giving the orchestrator a virtual context window
bounded only by disk storage.

Architecture:
    Tier 1 (Hot)  — last N turns verbatim in LLM context (~6k tokens)
    Tier 2 (Warm) — semantic retrieval from ChromaDB (this module)
    Tier 3 (Cold) — structured facts in Neo4j (existing GraphRAG)

Environment variables:
    CHROMA_HOST                  ChromaDB host (required; configure via Admin UI or CHROMA_HOST env var)
    CHROMA_PORT                  ChromaDB port (default: 8000)
    SEMANTIC_MEMORY_N_RESULTS    Max turns to retrieve per query (default: 5)
    SEMANTIC_MEMORY_MAX_CHARS    Max chars for the warm context block (default: 4000)
    SEMANTIC_MEMORY_TTL_DAYS     Days before stored turns expire (default: 30)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

_CHROMA_HOST      = os.getenv("CHROMA_HOST", "")
_CHROMA_PORT      = int(os.getenv("CHROMA_PORT", "8000"))
_N_RESULTS        = int(os.getenv("SEMANTIC_MEMORY_N_RESULTS", "10"))   # increased from 5
_MAX_CHARS        = int(os.getenv("SEMANTIC_MEMORY_MAX_CHARS", "4000"))
_TTL_DAYS         = int(os.getenv("SEMANTIC_MEMORY_TTL_DAYS", "30"))
# For sessions with few stored turns, retrieve all of them to avoid missing the needle.
# In production with many turns, _N_RESULTS applies; for small test sessions, retrieve all.
_SMALL_SESSION_THRESHOLD = int(os.getenv("SEMANTIC_MEMORY_SMALL_SESSION_THRESHOLD", "60"))

# Embedding model: configurable via SEMANTIC_MEMORY_EMBED_MODEL env var.
# Options:
#   "" or "default"  → all-MiniLM-L6-v2 (ChromaDB built-in, no server needed)
#   "ollama:<model>" → Ollama embedding model (e.g. "ollama:nomic-embed-text")
#                      Requires SEMANTIC_MEMORY_EMBED_URL (Ollama base URL).
_EMBED_MODEL  = os.getenv("SEMANTIC_MEMORY_EMBED_MODEL", "")
_EMBED_URL    = os.getenv("SEMANTIC_MEMORY_EMBED_URL", "")

_QUESTION_WORDS = re.compile(
    r'^(was ist|was sind|was war|wer ist|wer war|wann ist|wann war|'
    r'wie lautet|wie heißt|wie ist|was lautet|welche[rs]?|welchem|'
    r'what is|what was|who is|when is|how is|what are)\s+',
    re.IGNORECASE,
)

def _reformulate_query(query: str) -> str:
    """Convert recall questions to topic form for better ANN matching.

    Injected facts are statements ("Meine Glückszahl ist 7342.") while recall
    questions use interrogative structure ("Was ist meine Glückszahl?"). nomic-
    embed-text may embed the question closer to other questions (filler turns)
    than to the matching statement. Stripping the interrogative prefix reduces
    this asymmetry.

    Examples:
      "Was ist meine Glückszahl?"   → "meine Glückszahl"
      "Wer ist die Ansprechpartnerin?" → "die Ansprechpartnerin"
    """
    stripped = _QUESTION_WORDS.sub("", query.strip()).rstrip("?")
    return stripped if len(stripped) >= 5 else query


_STOP_WORDS = frozenset({
    "was", "ist", "sind", "wer", "wie", "wann", "welche", "welcher", "welches",
    "der", "die", "das", "ein", "eine", "einen", "meine", "mein", "mein",
    "für", "von", "bei", "mit", "und", "oder", "the", "is", "are", "what",
    "who", "when", "how", "which", "my", "our", "your", "their", "a", "an",
    "im", "in", "an", "auf", "zu", "zum", "zur", "dem", "den", "des",
    "dieses", "diesem", "diesem", "diese", "unsere", "unser",
})


def _topic_words(query: str) -> frozenset[str]:
    """Extract meaningful topic words from a recall query for overlap search.

    Strips stop words and short tokens; returns lowercase set of content words.
    Used as a fallback when semantic ANN ranks the needle too low.
    """
    words = re.split(r"[\s\?,!.]+", query.lower())
    return frozenset(w for w in words if len(w) >= 5 and w not in _STOP_WORDS)


_KW_PATTERNS = re.compile(
    r'\b\d[\d.,\-/]*\d\b'           # numbers: 7342, 3.14, 2026-11-14
    r'|[A-Z]{2,}[\-_]?[A-Z0-9]+\b'  # codes/acronyms: SOVEREIGN-AMBER, XK-9847
    r'|https?://\S+'                  # URLs
    r'|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b'  # proper names: Dr. Katharina Breitfeld
    r'|\b[A-Z]{2,}\b',               # abbreviations: GmbH, API
)


def _extract_keywords(text: str) -> str:
    """Extract exact-match tokens from text for hybrid retrieval fallback.

    Returns a space-separated string of unique tokens (numbers, codes, proper
    names, URLs). Stored as ChromaDB metadata for keyword-based WHERE queries
    when semantic ANN fails to find paraphrased facts.
    """
    tokens = list(dict.fromkeys(_KW_PATTERNS.findall(text)))  # unique, ordered
    return " ".join(tokens[:30])  # cap at 30 tokens to keep metadata small


# Versioned collection name: each embedding model gets its own collection so
# switching models does not delete existing turns (no data loss on upgrade).
_EMBED_SLUG   = (
    _EMBED_MODEL.split(":")[-1].replace("/", "-").replace(" ", "-")
    if _EMBED_MODEL and _EMBED_MODEL != "default"
    else "default"
)
_COLLECTION_NAME = f"conversation_memory_{_EMBED_SLUG}"


class _HttpxOllamaEF:
    """Minimal ChromaDB-compatible embedding function calling Ollama via httpx.

    Uses the batch /api/embed endpoint (Ollama ≥0.3) for all texts in one
    request, falling back to the legacy /api/embeddings endpoint one-by-one
    if the batch endpoint is unavailable.

    Avoids the `ollama` Python package dependency — uses only httpx which is
    already installed in the container.
    """

    def __init__(self, model_name: str, base_url: str) -> None:
        self._model      = model_name
        self._base_url   = base_url.rstrip("/")
        self._batch_url  = self._base_url + "/api/embed"        # Ollama ≥0.3 batch
        self._legacy_url = self._base_url + "/api/embeddings"   # single-prompt fallback
        self._use_batch  = True  # optimistically try batch first

    def name(self) -> str:
        """ChromaDB EmbeddingFunction interface — called as method, not property."""
        return f"ollama-{self._model}"

    def embed_query(self, input) -> "np.ndarray":
        """ChromaDB 1.5+ calls this with list[str] for query embedding.

        Returns the full list of embeddings (not just [0]) so the server
        receives the correct 2D structure [[dim0, dim1, ...]].
        """
        import numpy as np
        texts = input if isinstance(input, list) else [input]
        return self(texts)  # returns List[np.ndarray], chromadb wraps correctly

    def embed_documents(self, input: list[str]) -> list:
        """ChromaDB may call this for batch document embedding."""
        return self(input)

    def __call__(self, input: list[str]) -> list:
        import httpx
        import numpy as np  # ChromaDB 1.5+ expects numpy.ndarray, not plain lists
        if self._use_batch:
            try:
                resp = httpx.post(
                    self._batch_url,
                    json={"model": self._model, "input": input},
                    timeout=60.0,
                )
                resp.raise_for_status()
                data = resp.json()
                if "embeddings" in data:
                    return [np.array(e, dtype=np.float32) for e in data["embeddings"]]
            except Exception:
                self._use_batch = False  # fall back for remaining calls
        # Legacy: one request per text
        embeddings = []
        for text in input:
            resp = httpx.post(
                self._legacy_url,
                json={"model": self._model, "prompt": text},
                timeout=30.0,
            )
            resp.raise_for_status()
            embeddings.append(np.array(resp.json()["embedding"], dtype=np.float32))
        return embeddings


def _build_embedding_function():
    """Build the ChromaDB embedding function from environment configuration.

    Falls back to all-MiniLM-L6-v2 (DefaultEmbeddingFunction) if the configured
    model cannot be initialised, so startup never fails due to embedding config.
    """
    if _EMBED_MODEL.startswith("ollama:"):
        model_name = _EMBED_MODEL[len("ollama:"):]
        base_url   = _EMBED_URL or "http://localhost:11434"
        # Verify connectivity with a quick probe before committing
        try:
            import httpx
            httpx.get(base_url.rstrip("/") + "/api/tags", timeout=3.0).raise_for_status()
            logger.info(f"Semantic memory: Ollama embedding '{model_name}' at {base_url}")
            return _HttpxOllamaEF(model_name=model_name, base_url=base_url)
        except Exception as _e:
            logger.warning(
                f"Semantic memory: Ollama not reachable ({_e}), "
                "falling back to all-MiniLM-L6-v2"
            )
    # Default: all-MiniLM-L6-v2 (bundled with ChromaDB, ~80ms/batch, no GPU needed)
    return embedding_functions.DefaultEmbeddingFunction()


# Lazy-initialised in _ensure_connected() — avoids startup failure if embedding
# model is temporarily unavailable when the module is first imported.
_DEFAULT_EF: object = None


def _get_ef():
    global _DEFAULT_EF
    if _DEFAULT_EF is None:
        _DEFAULT_EF = _build_embedding_function()
    return _DEFAULT_EF


class ConversationMemoryStore:
    """Tier-2 semantic memory: store evicted turns, retrieve by relevance."""

    def __init__(self) -> None:
        self._client: Optional[chromadb.HttpClient] = None
        self._collection = None

    def _ensure_connected(self) -> None:
        if self._collection is not None:
            return
        if not _CHROMA_HOST:
            raise RuntimeError("CHROMA_HOST not configured — semantic memory disabled")
        self._client = chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT)
        try:
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                embedding_function=_get_ef(),
                metadata={"hnsw:space": "cosine"},
            )
        except ValueError as _e:
            if "Embedding function conflict" in str(_e):
                # Collection was created with a different embedding function.
                # Delete and recreate so the configured EF is used consistently.
                logger.warning(
                    f"Semantic memory: embedding function conflict ({_e}), "
                    "deleting and recreating collection with current EF"
                )
                self._client.delete_collection(_COLLECTION_NAME)
                self._collection = self._client.get_or_create_collection(
                    name=_COLLECTION_NAME,
                    embedding_function=_get_ef(),
                    metadata={"hnsw:space": "cosine"},
                )
            else:
                raise
        logger.info(f"Semantic memory: connected to ChromaDB collection '{_COLLECTION_NAME}'")

    # ── Store ─────────────────────────────────────────────────────────────────

    async def store_turns(
        self,
        session_id: str,
        turns: list[dict],
        base_turn_index: int = 0,
    ) -> int:
        """Embed and store evicted turns. Returns number of turns stored."""
        if not turns or not session_id:
            return 0
        try:
            return await asyncio.to_thread(
                self._store_sync, session_id, turns, base_turn_index
            )
        except Exception as exc:
            logger.warning(f"Semantic memory store error: {exc}")
            return 0

    def _store_sync(
        self,
        session_id: str,
        turns: list[dict],
        base_turn_index: int,
    ) -> int:
        self._ensure_connected()
        docs, ids, metas = [], [], []
        ts = int(time.time())
        expire_at = ts + _TTL_DAYS * 86400

        for i, turn in enumerate(turns):
            role    = turn.get("role", "user")
            content = turn.get("content", "")
            # Skip compressed placeholders and empty turns
            if not content or content.strip() in ("[…]", ""):
                continue
            # Stable ID: session + position + content fingerprint
            fp = hashlib.sha256(
                f"{session_id}:{base_turn_index + i}:{content[:120]}".encode()
            ).hexdigest()[:32]
            # Extract keywords for hybrid retrieval fallback.
            # Regex captures exact-match values (numbers, codes, proper nouns)
            # that semantic embedding may miss due to paraphrasing.
            keywords = _extract_keywords(content)
            docs.append(content)
            ids.append(fp)
            metas.append({
                "session_id":  session_id,
                "role":        role,
                "turn_index":  base_turn_index + i,
                "stored_at":   ts,
                "expire_at":   expire_at,
                "keywords":    keywords,  # space-separated for partial match queries
            })

        if not docs:
            return 0
        self._collection.upsert(documents=docs, ids=ids, metadatas=metas)
        logger.debug(f"Semantic memory: stored {len(docs)} turns (session={session_id[:8]}…)")
        return len(docs)

    # ── Retrieve ──────────────────────────────────────────────────────────────

    async def retrieve_relevant(
        self,
        session_id: str,
        query: str,
        n_results: int = _N_RESULTS,
    ) -> list[dict]:
        """ANN search: return the most semantically relevant past turns.

        Returns turns sorted by their original turn_index (temporal order),
        so the LLM sees them as they originally occurred in the conversation.
        """
        if not session_id or not query:
            return []
        try:
            return await asyncio.to_thread(
                self._retrieve_sync, session_id, query, n_results
            )
        except Exception as exc:
            logger.warning(f"Semantic memory retrieve error: {exc}")
            return []

    def _retrieve_sync(
        self,
        session_id: str,
        query: str,
        n_results: int,
    ) -> list[dict]:
        self._ensure_connected()

        # Guard: ChromaDB raises if n_results > collection size
        total_count = self._collection.count()
        if total_count == 0:
            return []

        # Query reformulation: strip interrogative prefix for better ANN matching.
        search_query = _reformulate_query(query)

        # ── Retrieval strategy ────────────────────────────────────────────────
        # ChromaDB's HNSW query can compute wrong distances when the embedding
        # function's query vector format differs from expectations. For small
        # SESSIONS (< threshold), we load ALL session turns and rank with numpy
        # ourselves — bypassing HNSW entirely for guaranteed correct cosine.
        # For large sessions, we fall back to HNSW which is acceptably accurate.
        #
        # IMPORTANT: use session-scoped count, not total collection count.
        # The collection accumulates turns from many sessions; total_count would
        # incorrectly trigger HNSW mode for small individual sessions.
        now = int(time.time())
        seen_ids: set[str] = set()
        turns: list[dict] = []

        # Fetch all session docs to get the session-scoped count cheaply
        raw = self._collection.get(
            where={"session_id": session_id},
            include=["documents", "metadatas", "embeddings"],
        )
        raw_docs   = raw.get("documents",  [])
        raw_metas  = raw.get("metadatas",  [])
        raw_embeds = raw.get("embeddings")
        count = len(raw_docs)   # session-scoped count

        if count == 0:
            return []

        if count <= _SMALL_SESSION_THRESHOLD:
            # Direct retrieval: rank all session turns by cosine using numpy
            if raw_embeds is not None and len(raw_embeds) > 0:
                import numpy as np
                # Embed query once with our EF (correct format guaranteed)
                q_emb = _get_ef()([search_query])[0]
                q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-9)

                ranked = []
                for doc, meta, emb in zip(raw_docs, raw_metas, raw_embeds):
                    if meta.get("expire_at", 0) < now:
                        continue
                    e = np.array(emb, dtype=np.float32)
                    e_norm = e / (np.linalg.norm(e) + 1e-9)
                    cos_dist = float(1.0 - np.dot(q_norm, e_norm))
                    ranked.append((cos_dist, doc, meta))

                ranked.sort(key=lambda x: x[0])
                # In direct-numpy mode: return ALL turns within threshold.
                # build_warm_context_block will truncate to _MAX_CHARS anyway.
                for cos_dist, doc, meta in ranked:
                    if cos_dist > 0.75:
                        break
                    fp = hashlib.sha256(doc[:120].encode()).hexdigest()[:16]
                    if fp in seen_ids:
                        continue
                    seen_ids.add(fp)
                    turns.append({
                        "role":       meta.get("role", "user"),
                        "content":    doc,
                        "turn_index": int(meta.get("turn_index", 0)),
                        "distance":   round(cos_dist, 4),
                    })
            else:
                # No embeddings returned — fall through to HNSW
                count = _SMALL_SESSION_THRESHOLD + 1

        if count > _SMALL_SESSION_THRESHOLD:
            # HNSW-based ANN retrieval for large sessions
            n = min(n_results, total_count)
            results = self._collection.query(
                query_texts=[search_query],
                n_results=n,
                where={"session_id": session_id},
                include=["documents", "metadatas", "distances"],
            )
            docs      = (results.get("documents")  or [[]])[0]
            metas     = (results.get("metadatas")  or [[]])[0]
            distances = (results.get("distances")  or [[]])[0]
            for doc, meta, dist in zip(docs, metas, distances):
                if meta.get("expire_at", 0) < now:
                    continue
                if dist > 0.75:
                    continue
                fp = hashlib.sha256(doc[:120].encode()).hexdigest()[:16]
                if fp in seen_ids:
                    continue
                seen_ids.add(fp)
                turns.append({
                    "role":       meta.get("role", "user"),
                    "content":    doc,
                    "turn_index": int(meta.get("turn_index", 0)),
                    "distance":   round(dist, 4),
                })

        # ── Phase 2: Topic-overlap fallback ──────────────────────────────────
        # For recall questions ("Was ist meine Glückszahl?"), nomic-embed-text
        # may rank other questions higher than the matching answer.
        # Fallback: extract meaningful words from the reformulated query and look
        # for stored turns containing those words — a simple text overlap that
        # reliably finds the injected fact regardless of embedding quality.
        # Always run topic-overlap: adds needle to top even when numpy ranking
        # already returned many turns. build_warm_context_block truncates to
        # _MAX_CHARS so extra entries are safe.
        topic_words = _topic_words(search_query)
        if topic_words:
            try:
                if count <= _SMALL_SESSION_THRESHOLD:
                    # Direct scan: already have raw docs from Phase 1
                    for doc, meta in zip(raw_docs, raw_metas):
                        if meta.get("expire_at", 0) < now:
                            continue
                        fp = hashlib.sha256(doc[:120].encode()).hexdigest()[:16]
                        if fp in seen_ids:
                            continue
                        doc_lower = doc.lower()
                        if any(w in doc_lower for w in topic_words):
                            seen_ids.add(fp)
                            turns.append({
                                "role":       meta.get("role", "user"),
                                "content":    doc,
                                "turn_index": int(meta.get("turn_index", 0)),
                                "distance":   0.0,  # topic-overlap match
                            })
            except Exception as _te:
                logger.debug(f"Topic-overlap fallback error: {_te}")

        # ── Phase 3: Keyword fallback (exact tokens from stored metadata) ──────
        query_kw = _extract_keywords(query)
        if query_kw:
            try:
                for token in query_kw.split()[:5]:
                    if len(token) < 3:
                        continue
                    kw_results = self._collection.get(
                        where={"$and": [
                            {"session_id": session_id},
                            {"keywords": {"$contains": token}},
                        ]},
                        include=["documents", "metadatas"],
                        limit=3,
                    )
                    for kw_doc, kw_meta in zip(
                        kw_results.get("documents", []),
                        kw_results.get("metadatas", []),
                    ):
                        if kw_meta.get("expire_at", 0) < now:
                            continue
                        fp = hashlib.sha256(kw_doc[:120].encode()).hexdigest()[:16]
                        if fp in seen_ids:
                            continue
                        seen_ids.add(fp)
                        turns.append({
                            "role":       kw_meta.get("role", "user"),
                            "content":    kw_doc,
                            "turn_index": int(kw_meta.get("turn_index", 0)),
                            "distance":   0.0,  # exact keyword match
                        })
            except Exception as _ke:
                logger.debug(f"Keyword fallback error (non-fatal): {_ke}")

        turns.sort(key=lambda t: t["turn_index"])
        return turns

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _cleanup_sync(self, max_delete: int) -> int:
        """Delete ChromaDB entries whose expire_at has passed. Returns count deleted."""
        self._ensure_connected()
        now = int(time.time())
        try:
            result = self._collection.get(
                where={"expire_at": {"$lt": now}},
                include=["metadatas"],
                limit=max_delete,
            )
            ids = result.get("ids", [])
            if ids:
                self._collection.delete(ids=ids)
                logger.info(f"Semantic memory cleanup: deleted {len(ids)} expired turns")
            return len(ids)
        except Exception as exc:
            logger.warning(f"Semantic memory cleanup query error: {exc}")
            return 0

    # ── Format ────────────────────────────────────────────────────────────────

    @staticmethod
    def build_warm_context_block(
        turns: list[dict],
        max_chars: int = _MAX_CHARS,
    ) -> str:
        """Format retrieved turns as an injected context block.

        The block uses the same style as the memory_recall system prompt so
        the LLM's existing instruction to 'Read ALL previous turns' applies.
        """
        if not turns:
            return ""
        lines = [
            "--- Semantic Memory: relevant earlier conversation turns ---",
        ]
        total = 0
        for turn in turns:
            role    = turn["role"].capitalize()
            content = turn["content"]
            entry   = f"{role}: {content}"
            if total + len(entry) > max_chars:
                break
            lines.append(entry)
            total += len(entry)
        lines.append("--- End of Semantic Memory ---")
        return "\n".join(lines)


# ── Module-level singleton ────────────────────────────────────────────────────

_store: Optional[ConversationMemoryStore] = None


def get_memory_store() -> ConversationMemoryStore:
    """Returns the module-level ConversationMemoryStore singleton."""
    global _store
    if _store is None:
        _store = ConversationMemoryStore()
    return _store


def compute_evicted_turns(
    raw_history: list[dict],
    kept_history: list[dict],
) -> list[dict]:
    """Return turns that were in raw_history but dropped during truncation.

    Matches by content fingerprint (first 200 chars) since _truncate_history_pure
    creates new dict objects rather than returning references to the originals.
    Turns replaced with '[…]' are treated as evicted (content lost).
    """
    kept_fp = {
        m["content"][:200]
        for m in kept_history
        if m.get("content") and m["content"] != "[…]"
    }
    evicted = [
        m for m in raw_history
        if m.get("content") and m["content"][:200] not in kept_fp
    ]
    return evicted


async def cleanup_expired_turns(max_delete: int = 500) -> int:
    """Delete ChromaDB entries whose expire_at timestamp has passed.

    Should be called periodically (e.g., every 6 hours) by a background task.
    Returns the number of deleted entries.
    """
    store = get_memory_store()
    try:
        return await asyncio.to_thread(store._cleanup_sync, max_delete)
    except Exception as exc:
        logger.warning(f"Semantic memory cleanup error: {exc}")
        return 0

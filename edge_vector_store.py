"""
edge_vector_store.py — Lightweight ChromaDB-compatible vector store for mobile edge.

Replaces chromadb.HttpClient() with a local hnswlib + SQLite backend.
No server required — data lives in EDGE_VECTOR_DIR on disk.

Embeddings are fetched via the OpenAI-compatible /v1/embeddings endpoint
at LLM_BASE_URL, or via an embedding_function passed to get_or_create_collection().

Activated automatically when ENVIRONMENT=edge_mobile.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

logger = logging.getLogger(__name__)

_EMBED_MODEL = os.getenv("EDGE_EMBED_MODEL", "nomic-embed-text")
_LLM_BASE    = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
_DATA_DIR    = os.getenv(
    "EDGE_VECTOR_DIR",
    os.path.join(os.path.expanduser("~"), ".moe_edge", "vector_data"),
)

# hnswlib lazy import — only needed when first collection is created
try:
    import hnswlib as _hnswlib
except ImportError:  # pragma: no cover
    _hnswlib = None  # type: ignore


def _vecs_to_blob(arr: np.ndarray) -> bytes:
    return arr.astype(np.float32).tobytes()


def _blob_to_vec(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).reshape(dim)


# ---------------------------------------------------------------------------
# EdgeCollection
# ---------------------------------------------------------------------------

class EdgeCollection:
    """
    ChromaDB Collection interface backed by hnswlib + SQLite.

    Thread-safe. Embeddings are stored in SQLite alongside documents so
    memory_retrieval.py can do exact numpy cosine ranking without a round-trip
    to the vector index.
    """

    def __init__(self, name: str, data_dir: str, embedding_fn=None):
        if _hnswlib is None:
            raise ImportError("hnswlib is required for EdgeCollection — pip install hnswlib")

        self.name  = name
        self._dir  = Path(data_dir) / name
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ef   = embedding_fn
        self._dim: Optional[int] = self._load_dim()
        self._db   = self._open_db()
        self._idx  = self._load_index() if self._dim else None

    # ── Persistence helpers ─────────────────────────────────────────────────

    def _meta_path(self) -> Path:
        return self._dir / "collection.json"

    def _load_dim(self) -> Optional[int]:
        p = self._meta_path()
        if p.exists():
            return json.loads(p.read_text()).get("dim")
        return None

    def _save_dim(self, dim: int):
        self._meta_path().write_text(json.dumps({"dim": dim}))

    def _open_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self._dir / "docs.db"), check_same_thread=False)
        db.execute("""
            CREATE TABLE IF NOT EXISTS docs (
                id        TEXT PRIMARY KEY,
                document  TEXT,
                metadata  TEXT NOT NULL DEFAULT '{}',
                embedding BLOB,
                hnsw_id   INTEGER UNIQUE
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_hnsw ON docs(hnsw_id)")
        db.commit()
        return db

    def _load_index(self) -> "hnswlib.Index":  # type: ignore[name-defined]
        assert self._dim is not None
        idx = _hnswlib.Index(space="cosine", dim=self._dim)
        path = str(self._dir / "index.bin")
        if Path(path).exists():
            idx.load_index(path, max_elements=50_000)
        else:
            idx.init_index(max_elements=10_000, ef_construction=200, M=16)
        idx.set_ef(50)
        return idx

    def _save_index(self):
        if self._idx is not None:
            self._idx.save_index(str(self._dir / "index.bin"))

    def _ensure_index(self, dim: int):
        """Lazy-initialise the hnswlib index on first upsert."""
        if self._idx is not None:
            return
        self._dim = dim
        self._save_dim(dim)
        self._idx = self._load_index()

    def _next_hnsw_id(self) -> int:
        row = self._db.execute("SELECT MAX(hnsw_id) FROM docs").fetchone()
        return (row[0] or 0) + 1

    # ── Embedding ───────────────────────────────────────────────────────────

    def _embed(self, texts: List[str]) -> np.ndarray:
        if self._ef is not None:
            vecs = self._ef(texts)
            return np.array(vecs, dtype=np.float32)
        resp = httpx.post(
            f"{_LLM_BASE}/embeddings",
            json={"model": _EMBED_MODEL, "input": texts},
            timeout=60.0,
        )
        resp.raise_for_status()
        items = sorted(resp.json()["data"], key=lambda x: x["index"])
        return np.array([i["embedding"] for i in items], dtype=np.float32)

    # ── Write ────────────────────────────────────────────────────────────────

    def add(self, ids, documents, metadatas=None):
        self.upsert(ids=ids, documents=documents, metadatas=metadatas)

    def upsert(self, ids=None, documents=None, metadatas=None, embeddings=None):
        with self._lock:
            ids       = list(ids or [])
            documents = list(documents or [])
            metadatas = list(metadatas or [{}] * len(ids))

            if embeddings is None:
                vecs = self._embed(documents)
            else:
                vecs = np.array(embeddings, dtype=np.float32)

            self._ensure_index(vecs.shape[1])

            for doc_id, doc, meta, vec in zip(ids, documents, metadatas, vecs):
                row = self._db.execute(
                    "SELECT hnsw_id FROM docs WHERE id=?", (doc_id,)
                ).fetchone()
                hnsw_id = row[0] if row else self._next_hnsw_id()

                if self._idx.get_current_count() >= self._idx.get_max_elements():
                    self._idx.resize_index(self._idx.get_max_elements() * 2)

                self._idx.add_items(vec.reshape(1, -1), [hnsw_id])
                self._db.execute(
                    "INSERT OR REPLACE INTO docs "
                    "(id, document, metadata, embedding, hnsw_id) VALUES (?,?,?,?,?)",
                    (doc_id, doc, json.dumps(meta), _vecs_to_blob(vec), hnsw_id),
                )
            self._db.commit()
            self._save_index()

    def update(self, ids, metadatas=None):
        with self._lock:
            for doc_id, meta in zip(ids, metadatas or [{}] * len(ids)):
                row = self._db.execute(
                    "SELECT metadata FROM docs WHERE id=?", (doc_id,)
                ).fetchone()
                if row:
                    merged = {**json.loads(row[0]), **(meta or {})}
                    self._db.execute(
                        "UPDATE docs SET metadata=? WHERE id=?",
                        (json.dumps(merged), doc_id),
                    )
            self._db.commit()

    def delete(self, ids=None):
        with self._lock:
            for doc_id in (ids or []):
                row = self._db.execute(
                    "SELECT hnsw_id FROM docs WHERE id=?", (doc_id,)
                ).fetchone()
                if row and self._idx:
                    try:
                        self._idx.mark_deleted(row[0])
                    except Exception:
                        pass
                self._db.execute("DELETE FROM docs WHERE id=?", (doc_id,))
            self._db.commit()
            self._save_index()

    # ── Read ─────────────────────────────────────────────────────────────────

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    def get(
        self,
        ids=None,
        where=None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        include = include or ["documents", "metadatas"]
        with self._lock:
            if ids:
                placeholders = ",".join("?" * len(ids))
                rows = self._db.execute(
                    f"SELECT id, document, metadata, embedding FROM docs"
                    f" WHERE id IN ({placeholders})",
                    list(ids),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, document, metadata, embedding FROM docs"
                ).fetchall()

            out_ids, out_docs, out_metas, out_embeds = [], [], [], []
            for row_id, doc, meta_str, emb_blob in rows:
                meta = json.loads(meta_str)
                if where and not self._match_where(meta, where):
                    continue
                out_ids.append(row_id)
                out_docs.append(doc)
                out_metas.append(meta)
                if "embeddings" in include and emb_blob and self._dim:
                    out_embeds.append(_blob_to_vec(emb_blob, self._dim).tolist())
                elif "embeddings" in include:
                    out_embeds.append(None)

            result: Dict[str, Any] = {"ids": out_ids}
            if "documents" in include:
                result["documents"] = out_docs
            if "metadatas" in include:
                result["metadatas"] = out_metas
            if "embeddings" in include:
                result["embeddings"] = out_embeds
            return result

    def query(
        self,
        query_texts,
        n_results: int = 5,
        where=None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            total = self.count()
            if total == 0 or self._idx is None:
                return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

            vecs = self._embed(list(query_texts))
            k    = min(n_results * 8, total)
            labels, distances = self._idx.knn_query(vecs, k=k)

            all_docs, all_metas, all_dists = [], [], []
            for label_row, dist_row in zip(labels, distances):
                docs_out, metas_out, dists_out = [], [], []
                for hnsw_id, dist in zip(label_row, dist_row):
                    row = self._db.execute(
                        "SELECT document, metadata FROM docs WHERE hnsw_id=?",
                        (int(hnsw_id),),
                    ).fetchone()
                    if not row:
                        continue
                    doc, meta_str = row
                    meta = json.loads(meta_str)
                    if meta.get("flagged"):
                        continue
                    if where and not self._match_where(meta, where):
                        continue
                    docs_out.append(doc)
                    metas_out.append(meta)
                    dists_out.append(float(dist))
                    if len(docs_out) >= n_results:
                        break
                all_docs.append(docs_out)
                all_metas.append(metas_out)
                all_dists.append(dists_out)

            return {"documents": all_docs, "metadatas": all_metas, "distances": all_dists}

    def _match_where(self, meta: dict, where: dict) -> bool:
        if "$and" in where:
            return all(self._match_where(meta, c) for c in where["$and"])
        if "$or" in where:
            return any(self._match_where(meta, c) for c in where["$or"])
        for key, condition in where.items():
            val = meta.get(key)
            if isinstance(condition, dict):
                for op, operand in condition.items():
                    if op == "$eq"  and val != operand:                          return False
                    if op == "$ne"  and val == operand:                          return False
                    if op == "$gt"  and not (val is not None and val >  operand): return False
                    if op == "$gte" and not (val is not None and val >= operand): return False
                    if op == "$lt"  and not (val is not None and val <  operand): return False
                    if op == "$lte" and not (val is not None and val <= operand): return False
                    if op == "$in"  and val not in operand:                      return False
                    if op == "$nin" and val in operand:                          return False
            else:
                if val != condition:
                    return False
        return True


# ---------------------------------------------------------------------------
# EdgeClient
# ---------------------------------------------------------------------------

class EdgeClient:
    """ChromaDB HttpClient-compatible facade for the local hnswlib+SQLite backend."""

    def __init__(self, data_dir: str = _DATA_DIR):
        self._data_dir = data_dir
        self._cols: Dict[str, EdgeCollection] = {}
        Path(data_dir).mkdir(parents=True, exist_ok=True)

    def get_or_create_collection(
        self,
        name: str,
        embedding_function=None,
        metadata: Optional[dict] = None,
    ) -> EdgeCollection:
        if name not in self._cols:
            self._cols[name] = EdgeCollection(name, self._data_dir, embedding_function)
        return self._cols[name]

    def delete_collection(self, name: str):
        self._cols.pop(name, None)
        path = Path(self._data_dir) / name
        if path.exists():
            shutil.rmtree(path)
        logger.info(f"EdgeClient: deleted collection '{name}'")

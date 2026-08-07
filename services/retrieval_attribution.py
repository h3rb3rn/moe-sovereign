"""
services/retrieval_attribution.py — Did retrieved context actually help?

After a final answer is produced, each retrieved chunk is scored by lexical
overlap with the answer (fast token-set ratio). Scores accumulate on the
Neo4j nodes (hit_count, miss_count, last_hit) and drive the decay job
(scripts/graph_decay.py): chunks that are retrieved often but never used
get pruned.

Flag: MOE_RETRIEVAL_ATTRIBUTION=1 (default off).
"""

import logging
import os
import re

logger = logging.getLogger("MOE-SOVEREIGN")

_WORD_RE = re.compile(r"[a-zA-ZäöüÄÖÜß0-9_]{4,}")


def _token_set(text: str) -> set:
    return set(w.lower() for w in _WORD_RE.findall(text or ""))


def chunk_used_in_answer(chunk_text: str, answer: str, threshold: float = 0.35) -> bool:
    """True when >= threshold of the chunk's significant tokens appear in the answer."""
    ct = _token_set(chunk_text)
    if len(ct) < 5:
        return False
    at = _token_set(answer)
    return (len(ct & at) / len(ct)) >= threshold


def graph_attribution_chunks(context: str) -> list[dict]:
    """Extract stable Entity identifiers from GraphRAG's rendered context."""
    chunks: list[dict] = []
    entity_line = re.compile(r"^•\s+(.+?)\s+\(([^)]+)\)(?::\s*(.*))?$")
    for line in (context or "").splitlines():
        match = entity_line.match(line.strip())
        if not match:
            continue
        chunks.append({
            "id": match.group(1).strip(),
            "id_field": "name",
            "text": line.strip(),
        })
    return chunks


async def record_attribution(driver, chunks: list, answer: str) -> None:
    """Persist whether the identifiable GraphRAG inputs contributed to ``answer``.

    Each item must contain ``id`` and ``text``. ``id_field`` may be ``chunk_id``
    (document chunks) or ``name`` (the Entity nodes returned by the current
    GraphRAG manager). The function is deliberately non-fatal, but callers
    should await it so short-lived request tasks cannot be lost at shutdown.
    """
    if os.getenv("MOE_RETRIEVAL_ATTRIBUTION", "0") != "1" or driver is None:
        return
    try:
        grouped: dict[str, dict[str, list]] = {}
        for c in chunks or []:
            cid = c.get("id")
            if not cid:
                continue
            id_field = c.get("id_field", "chunk_id")
            if id_field not in {"chunk_id", "name"}:
                logger.warning("retrieval_attribution: unsupported id_field=%r", id_field)
                continue
            bucket = grouped.setdefault(id_field, {"used": [], "miss": []})
            target = "used" if chunk_used_in_answer(c.get("text", ""), answer) else "miss"
            bucket[target].append(cid)

        used_total = miss_total = 0
        async with driver.session() as s:
            for id_field, ids_by_result in grouped.items():
                used_ids = ids_by_result["used"]
                miss_ids = ids_by_result["miss"]
                # ``id_field`` is selected from the fixed allow-list above; it
                # never contains request-controlled Cypher.
                if used_ids:
                    await s.run(
                        f"MATCH (n) WHERE n.{id_field} IN $ids "
                        "SET n.hit_count = coalesce(n.hit_count,0)+1, "
                        "n.retrieval_count = coalesce(n.retrieval_count,0)+1, "
                        "n.last_hit = datetime(), n.last_retrieved = datetime()",
                        ids=used_ids,
                    )
                    used_total += len(used_ids)
                if miss_ids:
                    await s.run(
                        f"MATCH (n) WHERE n.{id_field} IN $ids "
                        "SET n.miss_count = coalesce(n.miss_count,0)+1, "
                        "n.retrieval_count = coalesce(n.retrieval_count,0)+1, "
                        "n.last_retrieved = datetime()",
                        ids=miss_ids,
                    )
                    miss_total += len(miss_ids)
        logger.info("retrieval_attribution: %d used / %d unused inputs", used_total, miss_total)
    except Exception as e:
        logger.warning("retrieval_attribution failed (non-fatal): %s", e)

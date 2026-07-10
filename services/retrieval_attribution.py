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


async def record_attribution(driver, chunks: list, answer: str) -> None:
    """chunks: list of dicts with at least {'id': <neo4j element id or chunk id>, 'text': str}.

    Fire-and-forget: call via asyncio.create_task(). Never raises.
    """
    if os.getenv("MOE_RETRIEVAL_ATTRIBUTION", "0") != "1" or driver is None:
        return
    try:
        used_ids, miss_ids = [], []
        for c in chunks or []:
            cid = c.get("id")
            if not cid:
                continue
            (used_ids if chunk_used_in_answer(c.get("text", ""), answer) else miss_ids).append(cid)
        async with driver.session() as s:
            if used_ids:
                await s.run(
                    "MATCH (n) WHERE n.chunk_id IN $ids "
                    "SET n.hit_count = coalesce(n.hit_count,0)+1, n.last_hit = datetime()",
                    ids=used_ids,
                )
            if miss_ids:
                await s.run(
                    "MATCH (n) WHERE n.chunk_id IN $ids "
                    "SET n.miss_count = coalesce(n.miss_count,0)+1",
                    ids=miss_ids,
                )
        logger.info("retrieval_attribution: %d used / %d unused chunks", len(used_ids), len(miss_ids))
    except Exception as e:
        logger.warning("retrieval_attribution failed (non-fatal): %s", e)

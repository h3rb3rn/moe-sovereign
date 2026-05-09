"""
services/data_health.py — Data drift / schema health detection.

Every knowledge import is bracketed by a stats snapshot of the graph. After
the import we compare the deltas to the bundle's claimed entity count; large
deltas (≥ DRIFT_THRESHOLD) are recorded as drift events for admin review.

Why we need this:
- A bundle that claims 100 entities but only adds 2 to Neo4j is a sign that
  the dedup logic is suppressing nearly everything (alias collisions).
- A bundle that adds 3× more relations than entities suggests a malformed
  payload (e.g. cross-domain edges leaking out).
- A trust-floor regression — entity_count growing without trust-score growth
  — is a slow leak that deserves a paper trail.

Events live in Redis as a capped list (`moe:data_health:events`). When Redis
isn't available the helpers degrade silently.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("MOE-SOVEREIGN")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EVENT_KEY        = "moe:data_health:events"
MAX_EVENTS       = 500
# Drift threshold = how far the actual entity delta may diverge from the
# bundle's declared entity count (and from a reasonable relation/entity ratio).
DRIFT_THRESHOLD  = float(os.getenv("DATA_HEALTH_DRIFT_THRESHOLD", "0.3"))


# ---------------------------------------------------------------------------
# Pure analysis (no I/O)
# ---------------------------------------------------------------------------

def compute_drift(before: Dict[str, int],
                  after:  Dict[str, int],
                  *,
                  declared_entities: int,
                  declared_relations: Optional[int] = None,
                  threshold: float = DRIFT_THRESHOLD) -> Dict[str, Any]:
    """Compare graph stats before/after an import. Returns a drift report.

    Each detected anomaly contributes a string to ``flags``; the overall
    severity reflects whichever flag fired loudest:
    - "ok":   nothing notable
    - "info": small delta, within threshold
    - "warn": delta exceeded threshold (likely dedup or trust floor)
    - "crit": no entities added at all OR negative delta (graph shrank)
    """
    delta_entities  = (after.get("entities", 0) or 0) - (before.get("entities", 0) or 0)
    delta_relations = (after.get("relations", 0) or 0) - (before.get("relations", 0) or 0)
    delta_synth     = (after.get("synthesis_nodes", 0) or 0) - (before.get("synthesis_nodes", 0) or 0)

    flags: List[str] = []
    severity = "ok"

    if declared_entities > 0:
        ratio = delta_entities / declared_entities
        if ratio < 0:
            flags.append("entity_count_shrank")
            severity = "crit"
        elif declared_entities >= 5 and delta_entities == 0:
            flags.append("zero_entities_added")
            severity = "crit"
        elif ratio < (1.0 - threshold):
            flags.append("entity_dedup_suppressed")
            if severity != "crit":
                severity = "warn"
        elif ratio > (1.0 + threshold):
            flags.append("entity_overshoot")
            if severity != "crit":
                severity = "warn"

    if declared_relations is not None and declared_relations > 0:
        rel_ratio = delta_relations / declared_relations
        if rel_ratio > (1.0 + threshold):
            flags.append("relation_overshoot")
            if severity != "crit":
                severity = "warn"

    if declared_entities >= 10 and delta_relations > 5 * delta_entities and delta_entities > 0:
        flags.append("relation_to_entity_explosion")
        if severity != "crit":
            severity = "warn"

    if not flags and (delta_entities or delta_relations):
        severity = "info"

    return {
        "severity":           severity,
        "flags":              flags,
        "delta_entities":     delta_entities,
        "delta_relations":    delta_relations,
        "delta_synthesis":    delta_synth,
        "declared_entities":  declared_entities,
        "declared_relations": declared_relations,
    }


# ---------------------------------------------------------------------------
# Persistence (Redis-backed; soft-fail)
# ---------------------------------------------------------------------------

async def record_event(redis_client, *,
                       source_tag: str,
                       drift: Dict[str, Any],
                       trust_floor: Optional[float] = None) -> None:
    """Push a drift event into the capped Redis list. Never raises."""
    if redis_client is None:
        return
    event = {
        "ts":          int(time.time()),
        "source_tag":  source_tag,
        "trust_floor": trust_floor,
        **drift,
    }
    try:
        pipe = redis_client.pipeline()
        pipe.lpush(EVENT_KEY, json.dumps(event, ensure_ascii=False))
        pipe.ltrim(EVENT_KEY, 0, MAX_EVENTS - 1)
        await pipe.execute()
    except Exception as exc:
        logger.debug("data_health record_event failed: %s", exc)


async def recent_events(redis_client, *, limit: int = 50) -> List[Dict[str, Any]]:
    """Read the most recent drift events (newest first)."""
    if redis_client is None:
        return []
    try:
        raw = await redis_client.lrange(EVENT_KEY, 0, max(0, limit - 1))
    except Exception as exc:
        logger.debug("data_health recent_events failed: %s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out

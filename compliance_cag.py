"""
compliance_cag.py — Context-Augmented Generation for static compliance domains.

For regulatory knowledge that is stable and authoritative (BAIT, VAIT, DORA,
KRITIS, MaRisk), pre-loaded static context is more reliable than Neo4j retrieval:
  - No retrieval errors from sparse graph coverage
  - Deterministic output — same query always gets the same compliance block
  - Lower latency — no DB round-trip for known domains
  - Stronger BaFin/KRITIS pitch: "the system knows the regulation, not guesses it"

Based on: Chan et al. 2024, "Don't Do RAG: When Cache-Augmented Generation
is All You Need for Knowledge Tasks" (arXiv:2412.15605).

Admin interface:
  Drop JSON files into $MOE_DATA_ROOT/cag/ (default: /opt/moe-infra/cag/).
  Each file must have the schema:
    {
      "name":     "BAIT",
      "keywords": ["bait", "bankaufsichtlich", "it-sicherheit bank"],
      "context":  "Full compliance text block injected verbatim into the prompt."
    }
  The directory is scanned once at startup and refreshed every CAG_RELOAD_INTERVAL_S
  seconds (default: 300). No restart needed after adding files.

Environment:
  CAG_COMPLIANCE_DIR         Override the default cag/ directory path.
  CAG_RELOAD_INTERVAL_S      Seconds between live-reloads (default: 300).
  GRAPHRAG_CAG_ENABLED       Set to "0" to disable entirely (default: enabled).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN.compliance_cag")

_DATA_ROOT       = Path(os.getenv("MOE_DATA_ROOT", "/opt/moe-infra"))
_CAG_DIR         = Path(os.getenv("CAG_COMPLIANCE_DIR", str(_DATA_ROOT / "cag")))
_RELOAD_INTERVAL = int(os.getenv("CAG_RELOAD_INTERVAL_S", "300"))
_CAG_ENABLED     = os.getenv("GRAPHRAG_CAG_ENABLED", "1") not in ("0", "false", "no")

# Internal state — refreshed on _RELOAD_INTERVAL schedule.
# Each entry: {"name": str, "keywords": list[str], "context": str}
_DOMAINS:     list[dict] = []
_LOADED_AT:   float      = 0.0


def _load_domains() -> list[dict]:
    """Scan CAG_COMPLIANCE_DIR for *.json domain files and parse them.

    Silently skips files that are malformed or missing required keys.
    Returns an empty list if the directory does not exist — feature is then
    disabled without any error (zero-config safe default).
    """
    if not _CAG_DIR.exists():
        return []

    domains: list[dict] = []
    for path in sorted(_CAG_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not all(k in data for k in ("keywords", "context")):
                logger.warning("compliance_cag: %s missing required keys, skipping", path.name)
                continue
            domains.append({
                "name":     data.get("name", path.stem),
                "keywords": [kw.lower().strip() for kw in data["keywords"]],
                "context":  data["context"].strip(),
            })
        except Exception as exc:
            logger.warning("compliance_cag: could not parse %s: %s", path.name, exc)

    logger.info("compliance_cag: loaded %d domain(s) from %s", len(domains), _CAG_DIR)
    return domains


def _ensure_loaded() -> None:
    """Reload domains from disk when the TTL has elapsed."""
    global _DOMAINS, _LOADED_AT
    now = time.monotonic()
    if now - _LOADED_AT >= _RELOAD_INTERVAL:
        _DOMAINS   = _load_domains()
        _LOADED_AT = now


def _detect_domain(query: str) -> Optional[dict]:
    """Return the first domain whose keywords appear in the query, or None.

    Keyword matching is case-insensitive substring search — keeps it fast and
    avoids false negatives from inflection differences (e.g. "regulatorisch"
    vs "Regulatorik").
    """
    q_lower = query.lower()
    for domain in _DOMAINS:
        if any(kw in q_lower for kw in domain["keywords"]):
            return domain
    return None


def get_compliance_context(query: str, categories: Optional[list[str]] = None) -> str:
    """Return a pre-loaded CAG context block if the query matches a compliance domain.

    Returns an empty string when:
      - CAG is disabled via GRAPHRAG_CAG_ENABLED=0
      - No domain files are present in the CAG directory
      - The query does not match any loaded domain's keywords

    When a domain is matched, returns a formatted block that replaces Neo4j
    retrieval for that query — the caller should inject this instead of
    calling graph_manager.query_context().

    Args:
        query:      The user's input string.
        categories: Optional list of template categories (unused currently,
                    reserved for future per-category domain filtering).

    Returns:
        Formatted compliance context string, or "" if no match.
    """
    if not _CAG_ENABLED:
        return ""

    _ensure_loaded()
    if not _DOMAINS:
        return ""

    domain = _detect_domain(query)
    if not domain:
        return ""

    logger.info(
        "compliance_cag: CAG hit — domain=%r, bypassing Neo4j retrieval (%d chars)",
        domain["name"], len(domain["context"]),
    )
    return (
        f"[Compliance Knowledge — {domain['name']}]\n"
        f"{domain['context']}\n"
        f"[Source: CAG static domain — deterministic, no retrieval]"
    )

"""
web_search.py — SearXNG integration with source reliability ranking.

Domain scores determine citation tags ([HIGH]/[MEDIUM]/[LOW]) prepended
to each result so the merger can weight evidence appropriately. Higher-scoring
domains appear first in the returned text, putting the most credible sources
at the top of the context window.

Public API: _web_search_with_citations()
"""

from __future__ import annotations

import asyncio
import logging
from typing import List

logger = logging.getLogger("moe.web_search")

# Domain reliability scores for source ranking (higher = more trustworthy).
# Suffix-style entries like '.gov' match any sub-domain of that TLD.
_DOMAIN_SCORES: dict[str, float] = {
    "wikipedia.org":             0.95,
    "arxiv.org":                 0.92,
    "pubmed.ncbi.nlm.nih.gov":   0.92,
    "ncbi.nlm.nih.gov":          0.90,
    "nature.com":                0.90,
    "springer.com":              0.88,
    ".gov":                      0.85,
    ".edu":                      0.85,
    "github.com":                0.75,
    "stackoverflow.com":         0.72,
    "docs.python.org":           0.88,
    "developer.mozilla.org":     0.88,
    "reddit.com":                0.38,
    "quora.com":                 0.35,
}


def _domain_score(url: str) -> float:
    """Return reliability score for a URL based on its domain.

    Args:
        url: Full URL string.

    Returns:
        Float in [0.0, 1.0]. Defaults to 0.50 for unknown domains.
    """
    url_lower = url.lower()
    for domain, score in _DOMAIN_SCORES.items():
        if domain in url_lower:
            return score
    return 0.50


def _reliability_label(score: float) -> str:
    """Map a domain score to a human-readable reliability tag.

    Args:
        score: Float from _domain_score().

    Returns:
        'HIGH' | 'MEDIUM' | 'LOW'
    """
    if score >= 0.85:
        return "HIGH"
    if score >= 0.65:
        return "MEDIUM"
    return "LOW"


async def _web_search_with_citations(query: str, search: object) -> str:
    """Search via SearXNG and return result text with reliability-tagged citations.

    Results are sorted by domain reliability score before embedding so the
    most credible sources appear first in the context window.
    Each citation is tagged [HIGH/MEDIUM/LOW] based on source trustworthiness.

    Args:
        query: Search query string.
        search: A SearxSearchWrapper instance (or compatible object with
                .results() and .run() methods). Callers pass the app-level
                singleton so this module stays free of global state.

    Returns:
        Formatted string with numbered snippets and source citations,
        or empty string when search is None or all results fail.
    """
    if search is None:
        return ""
    try:
        raw_results = await asyncio.to_thread(search.results, query, num_results=5)
        if not raw_results:
            return await asyncio.to_thread(search.run, query)
        # Sort by domain reliability before processing
        raw_results = sorted(
            raw_results[:5],
            key=lambda r: _domain_score(r.get("link", r.get("url", ""))),
            reverse=True,
        )
        parts: List[str] = []
        citations: List[str] = []
        for i, r in enumerate(raw_results, 1):
            snippet = r.get("snippet", r.get("content", "")).strip()
            title   = r.get("title", "").strip()
            link    = r.get("link",   r.get("url", "")).strip()
            label   = _reliability_label(_domain_score(link))
            if snippet:
                parts.append(f"[{i}] {snippet}")
            if link:
                citations.append(f"[{i}] [{label}] {title or link}: {link}")
        text = "\n".join(parts)
        if citations:
            text += "\n\nSources:\n" + "\n".join(citations)
        return text or await asyncio.to_thread(search.run, query)
    except Exception as e:
        logger.warning("search.results() failed (%s) — fallback to search.run()", e)
        try:
            return await asyncio.to_thread(search.run, query)
        except Exception:
            return ""

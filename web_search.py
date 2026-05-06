"""
web_search.py — SearXNG integration with DuckDuckGo fallback and source reliability ranking.

Domain scores determine citation tags ([HIGH]/[MEDIUM]/[LOW]) prepended
to each result so the merger can weight evidence appropriately. Higher-scoring
domains appear first in the returned text, putting the most credible sources
at the top of the context window.

Fallback behaviour:
  When SearXNG returns an empty result or raises an exception, and
  ddg_fallback=True, the query is automatically retried via the public
  DuckDuckGo API (duckduckgo-search / ddgs library, no API key required).
  This prevents silent search failures when the self-hosted SearXNG instance
  is overloaded or temporarily unreachable.

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
    """Return reliability score for a URL based on its domain."""
    url_lower = url.lower()
    for domain, score in _DOMAIN_SCORES.items():
        if domain in url_lower:
            return score
    return 0.50


def _reliability_label(score: float) -> str:
    """Map a domain score to a human-readable reliability tag."""
    if score >= 0.85:
        return "HIGH"
    if score >= 0.65:
        return "MEDIUM"
    return "LOW"


def _format_results(raw_results: list) -> str:
    """Format a list of result dicts (SearXNG or DDG) into a cited text block."""
    parts: List[str] = []
    citations: List[str] = []
    for i, r in enumerate(raw_results, 1):
        snippet = r.get("snippet", r.get("content", r.get("body", ""))).strip()
        title   = r.get("title", "").strip()
        link    = r.get("link",  r.get("url", r.get("href", ""))).strip()
        label   = _reliability_label(_domain_score(link))
        if snippet:
            parts.append(f"[{i}] {snippet}")
        if link:
            citations.append(f"[{i}] [{label}] {title or link}: {link}")
    text = "\n".join(parts)
    if citations:
        text += "\n\nSources:\n" + "\n".join(citations)
    return text


async def _ddg_search_with_citations(query: str) -> str:
    """Search via the public DuckDuckGo API (no API key, no rate-limit key).

    Used as automatic fallback when SearXNG is unavailable or returns an empty
    result. Requires the 'duckduckgo-search' (ddgs) package.

    Args:
        query: Search query string.

    Returns:
        Formatted string with numbered snippets and source citations,
        or empty string on any failure.
    """
    def _sync_ddg(q: str) -> list:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            from ddgs import DDGS  # type: ignore[no-redef]
        with DDGS() as ddgs:
            return list(ddgs.text(q, max_results=5))

    try:
        raw = await asyncio.to_thread(_sync_ddg, query)
        if not raw:
            return ""
        raw = sorted(raw, key=lambda r: _domain_score(r.get("href", "")), reverse=True)
        result = _format_results(raw)
        if result:
            logger.info("DDG fallback succeeded for query '%s'", query[:60])
        return result
    except ImportError:
        logger.warning("DuckDuckGo fallback unavailable — install: pip install duckduckgo-search")
        return ""
    except Exception as e:
        logger.warning("DDG fallback failed (%s)", e)
        return ""


async def _web_search_with_citations(
    query: str,
    search: object,
    ddg_fallback: bool = True,
) -> str:
    """Search via SearXNG with automatic DuckDuckGo fallback.

    Priority:
      1. SearXNG (self-hosted, primary)
      2. DuckDuckGo public API — only when SearXNG returns empty / raises
         and ddg_fallback=True

    Args:
        query:        Search query string.
        search:       SearxSearchWrapper instance; None disables SearXNG.
        ddg_fallback: When True, retry via DDG if SearXNG yields no result.

    Returns:
        Formatted string with numbered snippets and source citations,
        or empty string when all backends fail.
    """
    searxng_result = ""

    if search is not None:
        try:
            raw_results = await asyncio.to_thread(search.results, query, num_results=5)
            if raw_results:
                raw_results = sorted(
                    raw_results[:5],
                    key=lambda r: _domain_score(r.get("link", r.get("url", ""))),
                    reverse=True,
                )
                searxng_result = _format_results(raw_results)
            if not searxng_result:
                # Last-resort: use search.run() which returns plain text
                fallback_text = await asyncio.to_thread(search.run, query)
                if fallback_text:
                    searxng_result = fallback_text
        except Exception as e:
            logger.warning("SearXNG search failed (%s) — will try DDG fallback if enabled", e)

    if searxng_result:
        return searxng_result

    if ddg_fallback:
        logger.info("SearXNG returned no result — activating DuckDuckGo fallback")
        ddg_result = await _ddg_search_with_citations(query)
        if ddg_result:
            return f"[DDG-FALLBACK]\n{ddg_result}"

    return ""

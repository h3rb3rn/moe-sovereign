"""
services/llm_instances.py — LLM client singletons.

These are constructed once at import time from config values:
  - judge_llm   — global judge / synthesis LLM (used by merger_node, critic_node)
  - planner_llm — task decomposition LLM (used by planner_node)
  - ingest_llm  — background GraphRAG extraction LLM (None falls back to judge_llm)
  - search      — SearxNG wrapper for web search (None when SEARXNG_URL is empty)

Per-template overrides (judge_model_override, planner_model_override) are resolved
at call time in services/inference.py (_get_judge_llm, _get_planner_llm).
"""

import logging
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_community.utilities import SearxSearchWrapper

from config import (
    JUDGE_MODEL, JUDGE_URL, JUDGE_TOKEN, JUDGE_TIMEOUT,
    PLANNER_MODEL, PLANNER_URL, PLANNER_TOKEN, PLANNER_TIMEOUT,
    GRAPH_INGEST_MODEL, GRAPH_INGEST_URL, GRAPH_INGEST_TOKEN,
    _SEARXNG_URL,
)

logger = logging.getLogger("MOE-SOVEREIGN")

judge_llm   = ChatOpenAI(model=JUDGE_MODEL,   base_url=JUDGE_URL,   api_key=JUDGE_TOKEN,   timeout=JUDGE_TIMEOUT)
planner_llm = ChatOpenAI(model=PLANNER_MODEL, base_url=PLANNER_URL, api_key=PLANNER_TOKEN, timeout=PLANNER_TIMEOUT)

# Ingest LLM: dedicated model for background GraphRAG extraction.
# Falls back to judge_llm when GRAPH_INGEST_MODEL is not configured.
ingest_llm: Optional[ChatOpenAI] = (
    ChatOpenAI(
        model=GRAPH_INGEST_MODEL,
        base_url=GRAPH_INGEST_URL,
        api_key=GRAPH_INGEST_TOKEN,
        timeout=JUDGE_TIMEOUT,
    )
    if GRAPH_INGEST_MODEL and GRAPH_INGEST_URL
    else None
)

search: Optional[SearxSearchWrapper] = (
    SearxSearchWrapper(searx_host=_SEARXNG_URL) if _SEARXNG_URL else None
)
if search is None:
    logger.info("SEARXNG_URL not set — web search disabled")

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
    MAX_JUDGE_TOKENS, MAX_PLANNER_TOKENS, JUDGE_NUM_CTX, PLANNER_NUM_CTX,
    _SEARXNG_URL,
)
from context_budget import get_model_context_window

logger = logging.getLogger("MOE-SOVEREIGN")

def _resolve_num_ctx(model: str, override: int) -> int:
    """Return num_ctx for an Ollama model: explicit override → static table → 0 (Ollama default)."""
    if override > 0:
        return override
    return get_model_context_window(model)

_judge_num_ctx   = _resolve_num_ctx(JUDGE_MODEL,   JUDGE_NUM_CTX)
_planner_num_ctx = _resolve_num_ctx(PLANNER_MODEL, PLANNER_NUM_CTX)

# extra_body MUST be a direct ChatOpenAI constructor parameter, NOT inside model_kwargs.
# LangChain silently drops extra_body from model_kwargs (emits UserWarning), so Ollama
# would fall back to its Modelfile default num_ctx (8192), causing a model reload.
_judge_extra_body:   dict | None = None
_planner_extra_body: dict | None = None
if _judge_num_ctx > 0:
    _judge_extra_body = {"options": {"num_ctx": _judge_num_ctx}}
    logger.info("Judge LLM: num_ctx=%d (model=%s)", _judge_num_ctx, JUDGE_MODEL)
_planner_extra_body = {"options": {"num_predict": MAX_PLANNER_TOKENS}}
if _planner_num_ctx > 0:
    _planner_extra_body["options"]["num_ctx"] = _planner_num_ctx
    logger.info("Planner LLM: num_ctx=%d (model=%s)", _planner_num_ctx, PLANNER_MODEL)

judge_llm   = ChatOpenAI(model=JUDGE_MODEL,   base_url=JUDGE_URL,   api_key=JUDGE_TOKEN,   timeout=JUDGE_TIMEOUT,
                         max_tokens=MAX_JUDGE_TOKENS,
                         **({"extra_body": _judge_extra_body} if _judge_extra_body else {}))
planner_llm = ChatOpenAI(model=PLANNER_MODEL, base_url=PLANNER_URL, api_key=PLANNER_TOKEN, timeout=PLANNER_TIMEOUT,
                         max_tokens=MAX_PLANNER_TOKENS, extra_body=_planner_extra_body)

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

"""
services/expert_builder.py — On-demand materialization of dynamic expert configurations.

Called by graph/expert.run_task() when the planner emits a task with
category="dynamic" or when no configured expert exists for the requested category.

Reuses the model-scoring and prompt-generation infrastructure from dynamic_router.py
without duplicating any logic.  The result is a list of model config dicts in the
same shape as entries in EXPERTS["general"], ready for expert_worker.run_single().
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import state
from config import INFERENCE_SERVERS_LIST, URL_MAP, TOKEN_MAP, MOE_USERDB_URL

logger = logging.getLogger("MOE-SOVEREIGN")

# Gate: off by default until the planner SFT model is trained on dynamic tasks.
import os
EXPERT_BUILDER_ENABLED = os.getenv("EXPERT_BUILDER_ENABLED", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def build_expert_for_task(
    task: dict,
    local_only: bool = False,
    user_connections: Optional[dict] = None,
    existing_web_research: str = "",
) -> tuple[list[dict], str]:
    """Materialise a single dynamic expert from a planner task dict.

    Returns (model_list, web_research_str):
        model_list        — list of model config dicts for expert_worker.run_single();
                            empty list on failure (caller should fall back to "general").
        web_research_str  — search result fetched inline for the domain, or "" when
                            existing_web_research was already present or the domain
                            does not need current information.

    Recognised task fields:
        category  — must be "dynamic" (or unknown)
        domain    — human-readable domain name, e.g. "Immobilienwertermittlung"
        task      — the concrete subtask description (used as prompt hint)
        requires  — optional list of capability hints, e.g. ["math", "legal_advisor"]
        privacy   — "local_only" overrides the request-level local_only parameter
        no_search — if True, skip inline web search even when domain needs it
    """
    if not EXPERT_BUILDER_ENABLED:
        return [], ""

    from services.dynamic_router import (
        _get_cluster_state,
        _score_and_allocate_model,
        _resolve_expert_tools_heuristic,
        _resolve_expert_skills_heuristic,
        _generate_prompt_specific_prompts,
        _CATEGORY_MCP_TOOLS,
    )

    domain   = (task.get("domain") or "").strip() or task.get("task", "dynamic")[:60]
    subtask  = task.get("task", domain)
    requires = list(task.get("requires") or [])

    # task-level privacy flag overrides the caller's flag
    if task.get("privacy") == "local_only":
        local_only = True

    # 1. Discover available models across all endpoints
    try:
        models = await _get_cluster_state()
    except Exception as exc:
        logger.warning("expert_builder: cluster state unavailable (%s)", exc)
        return [], ""

    if user_connections:
        from services.dynamic_router import _is_local_url
        for conn_name, conn_cfg in user_connections.items():
            for mc in conn_cfg.get("models_cache") or []:
                raw_id = mc.get("id") if isinstance(mc, dict) else str(mc)
                base   = raw_id.rsplit("@", 1)[0] if "@" in (raw_id or "") else (raw_id or "")
                if base:
                    models.append({
                        "model_id":   f"{base}@{conn_name}",
                        "model_name": base,
                        "endpoint":   conn_name,
                        "is_warmed":  False,
                        "is_local":   _is_local_url(conn_cfg.get("url", "")),
                    })

    if not models:
        logger.warning("expert_builder: no active models found on any endpoint")
        return [], ""

    # 2. Load model metadata from DB (best-effort)
    model_metadata = await _load_model_metadata()

    # 3. Map domain + requires to the closest scoring category for Thompson sampling
    score_cat = _infer_score_category(domain, requires, subtask)

    # 4. VRAM-aware model scoring and allocation
    try:
        allocated = await _score_and_allocate_model(
            score_cat, models, model_metadata, local_only, "moderate"
        )
    except Exception as exc:
        logger.warning("expert_builder: scoring failed (%s)", exc)
        return [], ""

    if not allocated:
        logger.warning(
            "expert_builder: no model survived scoring for domain=%r (local_only=%s)",
            domain, local_only,
        )
        return [], ""

    # 5. Generate a domain-specific system prompt
    sys_prompt = await _generate_system_prompt(domain, subtask, score_cat)

    # 6. Determine MCP tools and skills
    mcp_tools = _resolve_expert_tools_heuristic(score_cat, subtask)
    skills    = _resolve_expert_skills_heuristic(score_cat, subtask)

    # Merge additional tools from explicit `requires` hints
    for req in requires:
        for t in _CATEGORY_MCP_TOOLS.get(req, []):
            if t not in mcp_tools:
                mcp_tools.append(t)

    # 7. Inline web research — triggered when the domain needs current information
    #    and no research was already performed in this pipeline run.
    inline_web_research = ""
    if (
        not existing_web_research
        and not task.get("no_search")
        and _domain_needs_research(domain, score_cat, subtask)
    ):
        inline_web_research = await _fetch_inline_research(domain, subtask)

    # 8. Build the expert model list (primary + optional fallback)
    result = _build_model_list(allocated, sys_prompt, mcp_tools, skills, user_connections, model_metadata)

    logger.info(
        "expert_builder: domain=%r → model=%s score_cat=%s mcp_tools=%s skills=%s inline_search=%s",
        domain, allocated[0]["model_name"], score_cat, mcp_tools[:4], skills,
        bool(inline_web_research),
    )
    return result, inline_web_research


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_model_metadata() -> dict:
    """Load model metadata from DB; returns {} on any failure."""
    meta: dict = {}
    dsn = MOE_USERDB_URL or ""
    if not dsn:
        return meta
    try:
        from admin_ui.database import _get_pool
        pool = _get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT model_id, context_window, benchmark_scores, "
                    "parameter_size_b, family, strengths FROM model_metadata"
                )
                for r in await cur.fetchall():
                    if isinstance(r, dict):
                        mid = r["model_id"]
                        meta[mid] = {
                            "context_window":   r["context_window"],
                            "benchmark_scores": (
                                r["benchmark_scores"]
                                if isinstance(r["benchmark_scores"], dict)
                                else json.loads(r["benchmark_scores"] or "{}")
                            ),
                            "parameter_size_b": float(r["parameter_size_b"] or 7.0),
                            "family":           r["family"] or "other",
                            "strengths":        (
                                r["strengths"] if isinstance(r["strengths"], list) else []
                            ),
                        }
    except Exception as exc:
        logger.debug("expert_builder: model_metadata load failed (%s) — scoring without metadata", exc)
    return meta


async def _generate_system_prompt(domain: str, subtask: str, score_cat: str) -> str:
    """Generate a domain-specific system prompt; falls back to a compact static template."""
    try:
        from services.dynamic_router import _generate_prompt_specific_prompts
        resolved = await _generate_prompt_specific_prompts(subtask, [domain])
        prompt = resolved.get("experts", {}).get(domain, {}).get("system_prompt", "")
        if prompt:
            return prompt
    except Exception as exc:
        logger.debug("expert_builder: LLM prompt generation failed (%s) — using fallback", exc)

    # Static fallback: concise but domain-aware
    from prompts import DEFAULT_EXPERT_PROMPTS
    base = DEFAULT_EXPERT_PROMPTS.get(score_cat) or DEFAULT_EXPERT_PROMPTS.get("general", "")
    if base:
        return (
            f"[Domain: {domain}]\n{base}\n"
            f"Focus your expertise specifically on the domain of {domain}."
        )
    return (
        f"You are a specialized expert in {domain}. "
        f"Apply domain-specific knowledge, cite relevant standards or formulas, "
        f"and flag any assumptions clearly.\n"
        f"Subtask: {subtask[:300]}"
    )


def _build_model_list(
    allocated: list[dict],
    sys_prompt: str,
    mcp_tools: list[str],
    skills: list[str],
    user_connections: Optional[dict],
    model_metadata: Optional[dict] = None,
) -> list[dict]:
    """Translate scored model list into expert worker-compatible dicts."""
    _meta = model_metadata or {}

    def _entry(m: dict, tier: int | None) -> dict:
        ep    = m["endpoint"]
        url   = URL_MAP.get(ep) or ""
        if not url and user_connections and ep in user_connections:
            url = user_connections[ep].get("url", "")
        token = TOKEN_MAP.get(ep, "ollama")
        ctx   = int(_meta.get(m.get("model_id", ""), {}).get("context_window") or 0)
        return {
            "model":          m["model_name"],
            "endpoint":       ep,
            "url":            url,
            "token":          token,
            "forced":         False,
            "_tier":          tier,
            "_system_prompt": sys_prompt,
            "context_window": ctx,
            "_mcp_tools":     mcp_tools,
            "_skills":        skills,
            "enabled":        True,
        }

    result = [_entry(allocated[0], 1)]
    if len(allocated) > 1:
        result.append(_entry(allocated[1], 2))
    return result


_KNOWN_CATEGORIES = {
    "math", "science", "legal_advisor", "code_reviewer", "technical_support",
    "data_analysis", "reasoning", "research", "creative_writer", "translation",
    "medical_consult", "vision", "precision_tools", "general", "tool_agent",
    "agentic_coder", "data_analyst",
}


def _infer_score_category(domain: str, requires: list[str], subtask: str) -> str:
    """Map a free-form domain to the closest scoring category for Thompson sampling.

    Priority: explicit requires hint → keyword match on domain+subtask → "general".
    """
    for r in requires:
        if r in _KNOWN_CATEGORIES:
            return r

    text = (domain + " " + subtask).lower()

    if any(k in text for k in ("recht", "legal", "§", "gesetz", "bgb", "bgh", "bverfg", "norm", "paragraph", "vertrag", "haftung")):
        return "legal_advisor"
    if any(k in text for k in ("medizin", "diagnose", "therapie", "medikament", "klinisch", "symptom", "patient", "krankheit")):
        return "medical_consult"
    if any(k in text for k in ("rechne", "berechne", "calculate", "integral", "ableitung", "formel", "gleichung", "statistik", "mathe")):
        return "math"
    if any(k in text for k in ("code", "programm", "software", "script", "implementier", "debug", "refactor", "funktion", "klasse")):
        return "code_reviewer"
    if any(k in text for k in ("server", "netz", "subnet", "docker", "linux", "config", "deployment", "infra", "firewall", "ssh")):
        return "technical_support"
    if any(k in text for k in ("data", "csv", "tabelle", "daten", "analyse", "diagramm", "grafik", "visualisier", "pandas", "chart")):
        return "data_analysis"
    if any(k in text for k in ("forsch", "studie", "paper", "research", "wissenschaft", "literatur", "arxiv", "publikation")):
        return "research"
    if any(k in text for k in ("kreat", "text", "story", "gedicht", "poem", "schreib", "creative", "roman", "blog", "essay")):
        return "creative_writer"
    if any(k in text for k in ("übersetze", "translat", "sprache", "language", "deutsch", "english", "french")):
        return "translation"
    if any(k in text for k in ("bild", "foto", "image", "photo", "scan", "ocr", "diagram", "screenshot", "dokument")):
        return "vision"
    return "general"


# ---------------------------------------------------------------------------
# Online-Recherche-Logik für dynamische Experten
# ---------------------------------------------------------------------------

# Kategorien, für die aktuelle externe Informationen die Antwortqualität
# signifikant steigern — z.B. aktuelle Gesetze, Preise, Studien.
_RESEARCH_NEEDED_CATS = {
    "legal_advisor", "medical_consult", "science", "data_analysis",
    "research", "general",
}

# Signalwörter die auf Bedarf nach aktuellen Informationen hinweisen
_RESEARCH_SIGNAL_WORDS = (
    "aktuell", "current", "neuest", "latest", "preis", "price", "rate",
    "studie", "study", "norm", "standard", "richtlinie", "guideline",
    "gesetz", "law", "regulation", "verordnung", "2024", "2025", "2026",
    "markt", "market", "statistik", "statistic", "bericht", "report",
    "empfehlung", "recommendation", "leitlinie",
)

# Kategorien, für die eine Online-Suche keinen Nutzen hat
_RESEARCH_SKIP_CATS = {
    "creative_writer", "translation", "vision", "code_reviewer",
    "agentic_coder", "math", "precision_tools",
}


def _domain_needs_research(domain: str, score_cat: str, subtask: str) -> bool:
    """Entscheidet ob eine Online-Recherche für den dynamischen Experten sinnvoll ist.

    Gibt True zurück wenn die Kategorie oder der Subtask-Text darauf hindeuten,
    dass aktuelle externe Informationen die Antwortqualität verbessern.
    """
    if score_cat in _RESEARCH_SKIP_CATS:
        return False
    if score_cat in _RESEARCH_NEEDED_CATS:
        return True
    text = (domain + " " + subtask).lower()
    return any(kw in text for kw in _RESEARCH_SIGNAL_WORDS)


async def _fetch_inline_research(domain: str, subtask: str) -> str:
    """Führt eine gezielte Web-Suche für den dynamischen Experten durch.

    Konstruiert eine domänen-spezifische Suchanfrage aus domain + Subtask-Schlüsselwörtern
    und gibt das Ergebnis als formatierten String zurück (oder "" bei Fehler).
    """
    # Kompakte Suchanfrage: Domain + erste 80 Zeichen des Subtasks
    _query = f"{domain} {subtask[:80]}".strip()
    try:
        from services.helpers import _web_search_with_citations
        result = await _web_search_with_citations(_query, ddg_fallback=True)
        if result and len(result) > 100:
            logger.info("expert_builder: inline search for domain=%r → %d chars", domain, len(result))
            return result
    except Exception as exc:
        logger.debug("expert_builder: inline search failed (%s)", exc)
    return ""

"""
services/routing.py — Expert template and prompt resolution.

Pure sync functions — no async, no state deps.
Called from main.py (chat completions) and routes/anthropic_compat.py.
"""

import json
import logging
from typing import Optional

from config import URL_MAP, TOKEN_MAP, _WEB_SEARCH_FALLBACK_DDG, INFERENCE_SERVERS_LIST
from services.templates import _read_expert_templates

logger = logging.getLogger("MOE-SOVEREIGN")


def _resolve_user_experts(
    permissions_json: str,
    override_tmpl_id: Optional[str] = None,
    user_templates_json: str = "{}",
    admin_override: bool = False,
    user_connections_json: str = "{}",
) -> Optional[dict]:
    """Return an EXPERTS-compatible dict from the assigned expert template.

    Returns None when no template is assigned → global EXPERTS are used.
    override_tmpl_id: loaded directly (model-ID routing).
    admin_override: if True, override_tmpl_id is loaded without permission check.
    """
    try:
        perms     = json.loads(permissions_json or "{}")
        tmpl_ids  = perms.get("expert_template", [])
        templates = _read_expert_templates()
        user_templates: dict = json.loads(user_templates_json or "{}")

        def _find_tmpl(tid: str):
            if tid in user_templates:
                return user_templates[tid]
            return next(
                (t for t in templates if t.get("id") == tid or t.get("name") == tid), None
            )

        def _tmpl_in_allowed(tid: str) -> bool:
            if tid in tmpl_ids:
                return True
            t = _find_tmpl(tid)
            return bool(t and t.get("id") in tmpl_ids)

        if admin_override and override_tmpl_id:
            tmpl = _find_tmpl(override_tmpl_id)
        elif override_tmpl_id and override_tmpl_id in user_templates:
            tmpl = user_templates[override_tmpl_id]
        elif not tmpl_ids:
            return None
        elif override_tmpl_id and _tmpl_in_allowed(override_tmpl_id):
            tmpl = _find_tmpl(override_tmpl_id)
            if tmpl is None:
                logger.warning(
                    "Ghost template in _resolve_user_experts: %s in perms but not in DB/cache",
                    override_tmpl_id,
                )
        else:
            tmpl = next((_find_tmpl(tid) for tid in tmpl_ids if _find_tmpl(tid)), None)
            if tmpl is None:
                logger.warning(
                    "Ghost templates: all permitted templates missing from DB: %s", tmpl_ids
                )
        if not tmpl:
            return None

        result: dict = {}
        for cat, cat_cfg in tmpl.get("experts", {}).items():
            _sys_prompt = (cat_cfg.get("system_prompt") or "").strip() if isinstance(cat_cfg, dict) else ""
            if isinstance(cat_cfg, dict) and "models" in cat_cfg:
                models_list = []
                for m in cat_cfg.get("models", []):
                    role = m.get("role")
                    if role is None:
                        role = "always" if m.get("required", True) else "primary"
                    if role == "always":
                        forced, model_tier = True, None
                    elif role == "fallback":
                        forced, model_tier = False, 2
                    else:
                        forced, model_tier = False, 1
                    ep   = (m.get("endpoint") or "").strip()
                    url  = URL_MAP.get(ep) if ep else None
                    if not url:
                        _uc = json.loads(user_connections_json or "{}")
                        if ep in _uc:
                            url = _uc[ep]["url"]
                    models_list.append({
                        "model":          m.get("model", ""),
                        "endpoint":       ep,
                        "url":            url,
                        "token":          TOKEN_MAP.get(ep, "ollama") if ep else "ollama",
                        "forced":         forced,
                        "_tier":          model_tier,
                        "_system_prompt": _sys_prompt,
                    })
                result[cat] = models_list
            elif isinstance(cat_cfg, dict):
                # Legacy format: {model, endpoint}
                ep  = (cat_cfg.get("endpoint") or "").strip()
                url = URL_MAP.get(ep) if ep else None
                result[cat] = [{
                    "model":          cat_cfg.get("model", ""),
                    "endpoint":       ep,
                    "url":            url,
                    "token":          TOKEN_MAP.get(ep, "ollama") if ep else "ollama",
                    "forced":         True,
                    "_tier":          None,
                    "_system_prompt": _sys_prompt,
                }]
        return result or None
    except Exception:
        return None


def _resolve_template_prompts(
    permissions_json: str,
    override_tmpl_id: Optional[str] = None,
    user_templates_json: str = "{}",
    admin_override: bool = False,
    user_connections_json: str = "{}",
) -> dict:
    """Return planner_prompt, judge_prompt and optional model overrides from the template."""
    empty = {
        "planner_prompt": "", "judge_prompt": "",
        "judge_model_override": "", "judge_url_override": "", "judge_token_override": "",
        "planner_model_override": "", "planner_url_override": "", "planner_token_override": "",
        "enable_cache": True, "enable_graphrag": True, "enable_web_research": True,
        "search_fallback_ddg": _WEB_SEARCH_FALLBACK_DDG,
        "graphrag_max_chars": 0,
        "history_max_turns": 0, "history_max_chars": 0,
        "force_think": False, "max_agentic_rounds": 0,
        "enable_mission_context": False,
        "enable_semantic_memory": False,
        "semantic_memory_n_results": 0, "semantic_memory_ttl_hours": 0,
        "enable_cross_session_memory": False,
        "cross_session_scopes": ["private"], "cross_session_ttl_days": 0,
    }
    try:
        perms     = json.loads(permissions_json or "{}")
        tmpl_ids  = perms.get("expert_template", [])
        templates = _read_expert_templates()
        user_templates: dict = json.loads(user_templates_json or "{}")

        def _find_tmpl(tid: str):
            if tid in user_templates:
                return user_templates[tid]
            return next(
                (t for t in templates if t.get("id") == tid or t.get("name") == tid), None
            )

        def _tmpl_in_allowed(tid: str) -> bool:
            if tid in tmpl_ids:
                return True
            t = _find_tmpl(tid)
            return bool(t and t.get("id") in tmpl_ids)

        if admin_override and override_tmpl_id:
            tmpl = _find_tmpl(override_tmpl_id)
        elif override_tmpl_id and override_tmpl_id in user_templates:
            tmpl = user_templates[override_tmpl_id]
        elif not tmpl_ids:
            return empty
        elif override_tmpl_id and _tmpl_in_allowed(override_tmpl_id):
            tmpl = _find_tmpl(override_tmpl_id)
        else:
            tmpl = next((_find_tmpl(tid) for tid in tmpl_ids if _find_tmpl(tid)), None)
        if not tmpl:
            return empty

        def _split_model_ep(val: str) -> tuple:
            if val and "@" in val:
                at = val.rindex("@")
                return val[:at], val[at + 1:]
            return val or "", ""

        judge_m,   judge_ep   = _split_model_ep(tmpl.get("judge_model", ""))
        planner_m, planner_ep = _split_model_ep(tmpl.get("planner_model", ""))

        def _resolve_ep_url(ep: str) -> tuple:
            if ep in URL_MAP:
                return URL_MAP[ep], TOKEN_MAP.get(ep, "ollama")
            _uc = json.loads(user_connections_json or "{}")
            if ep in _uc:
                return _uc[ep]["url"], _uc[ep].get("api_key") or "ollama"
            return "", "ollama"

        judge_url,   judge_tok   = _resolve_ep_url(judge_ep)   if judge_ep   else ("", "ollama")
        planner_url, planner_tok = _resolve_ep_url(planner_ep) if planner_ep else ("", "ollama")
        return {
            "planner_prompt":          tmpl.get("planner_prompt", ""),
            "judge_prompt":            tmpl.get("judge_prompt", ""),
            "judge_model_override":    judge_m,
            "judge_url_override":      judge_url,
            "judge_token_override":    judge_tok,
            "planner_model_override":  planner_m,
            "planner_url_override":    planner_url,
            "planner_token_override":  planner_tok,
            "enable_cache":            tmpl.get("enable_cache", True),
            "enable_graphrag":         tmpl.get("enable_graphrag", True),
            "enable_web_research":     tmpl.get("enable_web_research", True),
            "search_fallback_ddg":     tmpl.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG),
            "graphrag_max_chars":      int(tmpl.get("graphrag_max_chars", 0)),
            "history_max_turns":       int(tmpl.get("history_max_turns", 0)),
            "history_max_chars":       int(tmpl.get("history_max_chars", 0)),
            "force_think":             bool(tmpl.get("force_think", False)),
            "max_agentic_rounds":      int(tmpl.get("max_agentic_rounds", 0)),
            "enable_mission_context":  bool(tmpl.get("enable_mission_context", False)),
            "enable_semantic_memory":  bool(tmpl.get("enable_semantic_memory", False)),
            "semantic_memory_n_results": int(tmpl.get("semantic_memory_n_results", 0)),
            "semantic_memory_ttl_hours": int(tmpl.get("semantic_memory_ttl_hours", 0)),
            "enable_cross_session_memory": bool(tmpl.get("enable_cross_session_memory", False)),
            "cross_session_scopes":    list(tmpl.get("cross_session_scopes", ["private"])),
            "cross_session_ttl_days":  int(tmpl.get("cross_session_ttl_days", 0)),
        }
    except Exception:
        return empty


def _server_info(endpoint_name: str) -> dict:
    """Return the full server configuration for a given endpoint name."""
    return next((s for s in INFERENCE_SERVERS_LIST if s["name"] == endpoint_name), {})


def _is_endpoint_error(exc: Exception) -> bool:
    """Return True when the exception signals an external endpoint is unavailable (auth/quota)."""
    s = str(exc).lower()
    return any(k in s for k in (
        "401", "unauthorized", "403", "forbidden",
        "429", "rate limit", "quota exceeded",
        "authentication", "x-api-key",
    ))

"""services/model_capabilities.py — Per-model capability matrix (TASK-31).

Loads configs/model_capabilities.yaml once (or per-request in dev mode) and
exposes typed getters for inference.py to make capability-aware call decisions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

logger = logging.getLogger("MOE-SOVEREIGN")

_DEFAULT_CAPS: dict = {
    "json_schema":   False,
    "json_object":   True,
    "stream":        True,
    "responses_api": False,
    "hints":         [],
}

_CAPS_PATH = Path(__file__).parent.parent / "configs" / "model_capabilities.yaml"
_RELOAD_ON_REQUEST = os.getenv("CAPABILITIES_RELOAD_ON_REQUEST", "false").lower() == "true"

_cache: dict | None = None


def load_capabilities(path: str | Path | None = None) -> dict:
    """Load (or reload) the capability YAML. Cached after first load unless
    CAPABILITIES_RELOAD_ON_REQUEST=true."""
    global _cache
    if _cache is not None and not _RELOAD_ON_REQUEST:
        return _cache
    _p = Path(path) if path else _CAPS_PATH
    try:
        import yaml  # PyYAML, available in requirements.txt
        with _p.open() as f:
            raw = yaml.safe_load(f)
        _cache = raw or {}
    except Exception as exc:
        logger.warning("model_capabilities: failed to load %s: %s — using defaults", _p, exc)
        _cache = {}
    return _cache


def get_model_caps(model: str) -> dict:
    """Return capability dict for *model*, falling back to 'default' block."""
    caps = load_capabilities()
    models_section = caps.get("models") or {}
    default = {**_DEFAULT_CAPS, **(caps.get("default") or {})}
    entry = models_section.get(model)
    if entry is None:
        return default
    return {**default, **entry}


def model_supports_json_schema(model: str) -> bool:
    """True if the model honours response_format={"type":"json_schema",...}."""
    return bool(get_model_caps(model).get("json_schema", False))


def model_supports_json_object(model: str) -> bool:
    """True if the model honours response_format={"type":"json_object"}."""
    return bool(get_model_caps(model).get("json_object", True))


def model_supports_streaming(model: str) -> bool:
    """True if the model can stream tokens."""
    return bool(get_model_caps(model).get("stream", True))


def model_hint_tokens(model: str) -> List[str]:
    """Return the hint-token list for *model* (empty list if none)."""
    return list(get_model_caps(model).get("hints") or [])

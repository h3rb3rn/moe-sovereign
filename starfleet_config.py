"""
starfleet_config — Two-layer feature toggle system for Star Trek capabilities.

Priority: Redis runtime override > .env persistent flag > hardcoded default.
Redis keys use the namespace moe:features:<name> (lowercase, underscores).

Feature names and their env-var counterparts:
  watchdog          → WATCHDOG_ENABLED          (default: true)
  mission_context   → MISSION_CONTEXT_ENABLED   (default: true)
  adaptive_ui       → ADAPTIVE_UI_ENABLED       (default: true)
  infra_mcp         → INFRA_MCP_ENABLED         (default: false)
"""

import os
import logging
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN.starfleet")

_FEATURE_DEFAULTS: dict[str, bool] = {
    "watchdog":        True,
    "mission_context": True,
    "adaptive_ui":     True,
    "infra_mcp":       False,
}

_TRUTHY = frozenset(("1", "true", "yes"))


async def is_feature_enabled(
    name: str,
    redis_client=None,
) -> bool:
    """Return whether a Starfleet feature is currently enabled.

    Checks Redis first (runtime override), then .env, then the hardcoded default.
    Unknown feature names are treated as disabled and logged as a warning.
    """
    if name not in _FEATURE_DEFAULTS:
        logger.warning("Unknown Starfleet feature '%s' — treated as disabled", name)
        return False

    # Layer 1: Redis runtime override (no restart required)
    if redis_client is not None:
        try:
            val: Optional[str] = await redis_client.get(f"moe:features:{name}")
            if val is not None:
                return val.strip().lower() in _TRUTHY
        except Exception as exc:
            logger.debug("Redis feature lookup failed for '%s': %s", name, exc)

    # Layer 2: .env persistent flag
    env_key = f"{name.upper()}_ENABLED"
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val.strip().lower() in _TRUTHY

    # Layer 3: hardcoded default
    return _FEATURE_DEFAULTS[name]


def is_feature_enabled_sync(name: str) -> bool:
    """Synchronous fallback — reads only .env and the hardcoded default.

    Use in startup code or non-async contexts where no event loop is available.
    """
    if name not in _FEATURE_DEFAULTS:
        logger.warning("Unknown Starfleet feature '%s' — treated as disabled", name)
        return False
    env_key = f"{name.upper()}_ENABLED"
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val.strip().lower() in _TRUTHY
    return _FEATURE_DEFAULTS[name]


async def set_feature_enabled(name: str, enabled: bool, redis_client) -> bool:
    """Write a runtime override to Redis.

    Returns True on success, False if Redis is unavailable or the feature is unknown.
    The override survives until explicitly cleared or the Redis instance is flushed.
    """
    if name not in _FEATURE_DEFAULTS:
        logger.warning("Cannot set unknown Starfleet feature '%s'", name)
        return False
    if redis_client is None:
        return False
    try:
        await redis_client.set(f"moe:features:{name}", "true" if enabled else "false")
        logger.info("Starfleet feature '%s' set to %s via Redis", name, enabled)
        return True
    except Exception as exc:
        logger.error("Failed to set Starfleet feature '%s' in Redis: %s", name, exc)
        return False


async def get_all_feature_states(redis_client=None) -> dict[str, dict]:
    """Return a snapshot of all feature states with their source (redis/env/default)."""
    result = {}
    for name, default in _FEATURE_DEFAULTS.items():
        source = "default"
        enabled = default

        env_key = f"{name.upper()}_ENABLED"
        if os.getenv(env_key) is not None:
            enabled = os.getenv(env_key, "").strip().lower() in _TRUTHY
            source = "env"

        if redis_client is not None:
            try:
                val = await redis_client.get(f"moe:features:{name}")
                if val is not None:
                    enabled = val.strip().lower() in _TRUTHY
                    source = "redis"
            except Exception:
                pass

        result[name] = {
            "enabled":  enabled,
            "source":   source,
            "env_key":  env_key,
            "default":  default,
            "requires_restart": name in {"watchdog", "mission_context"},
        }
    return result

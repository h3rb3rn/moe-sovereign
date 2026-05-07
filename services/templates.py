"""
services/templates.py — Expert template and CC profile loaders.

These are pure-sync functions (no asyncio) with their own in-process cache.
They can be called from any thread or async context without event-loop issues.

Callers:
  - main.py (planner, routing, chat completions, admin handlers)
  - routes/admin_benchmark.py (template lookup for benchmark lock)
  - routes/admin_ontology.py (template lookup for healer trigger)
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from config import MOE_USERDB_URL, POSTGRES_CHECKPOINT_URL


# ---------------------------------------------------------------------------
# Expert templates (primary: Postgres, fallback: .env file, fallback: env var)
# ---------------------------------------------------------------------------

def _read_expert_templates() -> list:
    """Return the current expert-templates list (30 s in-process cache).

    Primary source: Postgres table admin_expert_templates (written by the
    Admin UI). Falls back to /app/.env and finally to the EXPERT_TEMPLATES
    env var when the DB is unreachable at boot time.
    """
    now = time.monotonic()
    cache = _read_expert_templates._cache
    if now - cache["ts"] < 30 and cache["data"] is not None:
        return cache["data"]

    data = _load_templates_from_db_sync()
    if data is None:
        data = _load_templates_from_env_file()
    if data is None:
        data = json.loads(os.getenv("EXPERT_TEMPLATES", "[]"))

    cache["ts"] = now
    cache["data"] = data
    return data


_read_expert_templates._cache: dict = {"ts": 0.0, "data": None}


def _load_templates_from_db_sync() -> Optional[list]:
    """One-shot sync query against admin_expert_templates.

    Returns None on any failure so the caller can fall back to .env.
    """
    dsn = MOE_USERDB_URL or POSTGRES_CHECKPOINT_URL or ""
    if not dsn:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT id, name, description, config_json, is_active "
                    "FROM admin_expert_templates ORDER BY created_at ASC"
                )
                rows = cur.fetchall()
    except Exception:
        return None
    result: list = []
    for row in rows:
        cfg = row.get("config_json")
        if isinstance(cfg, str):
            try:
                tmpl = json.loads(cfg)
            except json.JSONDecodeError:
                tmpl = {}
        elif isinstance(cfg, dict):
            tmpl = dict(cfg)
        else:
            tmpl = {}
        tmpl["id"] = row["id"]
        tmpl["name"] = row["name"]
        tmpl["description"] = row.get("description", "")
        tmpl["is_active"] = row.get("is_active", True)
        result.append(tmpl)
    return result


def _load_templates_from_env_file() -> Optional[list]:
    """Legacy .env parser — kept as fallback when the DB is unreachable."""
    env_path = Path(os.getenv("ENV_FILE", "/app/.env"))
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("EXPERT_TEMPLATES="):
                raw = line[len("EXPERT_TEMPLATES="):].strip()
                if raw.startswith('"') and raw.endswith('"'):
                    raw = raw[1:-1].replace('\\\\', '\\').replace('\\"', '"')
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else None
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Claude Code profiles (.env file reader, 60 s cache)
# ---------------------------------------------------------------------------

def _read_cc_profiles() -> list:
    """Read CLAUDE_CODE_PROFILES dynamically from /app/.env (60 s cache).

    Profiles created in the Admin UI become visible within one minute
    without a container restart.
    """
    now = time.monotonic()
    cache = _read_cc_profiles._cache
    if now - cache["ts"] < 60 and cache["data"] is not None:
        return cache["data"]
    env_path = Path(os.getenv("ENV_FILE", "/app/.env"))
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("CLAUDE_CODE_PROFILES="):
                raw = line[len("CLAUDE_CODE_PROFILES="):].strip()
                if raw.startswith('"') and raw.endswith('"'):
                    raw = raw[1:-1].replace('\\\\', '\\').replace('\\"', '"')
                data = json.loads(raw)
                cache["ts"] = now
                cache["data"] = data
                return data
    except Exception:
        pass
    fallback = json.loads(os.getenv("CLAUDE_CODE_PROFILES", "[]"))
    cache["ts"] = now
    cache["data"] = fallback
    return fallback


_read_cc_profiles._cache: dict = {"ts": 0.0, "data": None}

"""Auto-provisioning of ontology curator templates for inference servers.

Shared between the admin-UI toggle flow and batch scripts. Given a server
entry from INFERENCE_SERVERS, picks a curator model (by VRAM or explicit
override), ensures Ollama has it pulled, clones the aggregated parent
template into a pinned variant, and upserts via database.upsert_admin_template.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from typing import Any, Optional

import httpx

try:
    from . import database  # when imported as admin_ui.curator_provisioner (e.g. from scripts)
except ImportError:
    import database  # flat layout inside the container


PROVISION_REDIS_PREFIX = "moe:ontology:provision:"


def pick_curator_model(server: dict) -> str:
    """Return explicit override if set, otherwise map API type or VRAM → model."""
    override = (server.get("curator_model") or "").strip()
    if override:
        return override
    # OpenAI-compatible remote APIs: use cloud-native sovereign model, no local pull
    if server.get("api_type") == "openai":
        return "qwen-3.5-122b-sovereign"
    vram = int(server.get("vram_gb") or 0)
    if vram >= 20:
        return "mistral-nemo:latest"
    if vram >= 8:
        return "llama3.1:8b"
    return "mistral:7b"


def _swap_node_and_model(value: Any, old_node: str, new_node: str, new_model: str) -> Any:
    """Replace every 'model@OldNode' with 'new_model@NewNode' inside nested data.

    Also rewrites bare ``endpoint`` and ``node`` dict keys whose value equals
    ``old_node`` (experts config uses ``{"endpoint": "N06-M10"}`` without @).
    """
    if isinstance(value, str):
        return re.sub(
            r"([\w\-.:/]+)@" + re.escape(old_node) + r"\b",
            f"{new_model}@{new_node}",
            value,
        )
    if isinstance(value, list):
        return [_swap_node_and_model(v, old_node, new_node, new_model) for v in value]
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if k in ("endpoint", "node") and isinstance(v, str) and v == old_node:
                out[k] = new_node
            elif k == "model" and isinstance(v, str):
                # Pin the expert's primary model to the instance's curator model
                # only when it doesn't already carry an @Node suffix.
                out[k] = new_model if "@" not in v else _swap_node_and_model(v, old_node, new_node, new_model)
            else:
                out[k] = _swap_node_and_model(v, old_node, new_node, new_model)
        return out
    return value


def _flatten_parent(parent_row: dict) -> dict:
    """list_admin_templates returns DB rows with nested config_json — flatten."""
    out = dict(parent_row)
    cfg = out.pop("config_json", None)
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    if isinstance(cfg, dict):
        out.update(cfg)
    return out


def build_curator_template(
    parent: dict, new_node: str, new_model: str, parent_node: Optional[str] = None,
) -> dict:
    """Clone an aggregated curator template for a pinned sub-instance."""
    parent = _flatten_parent(parent)
    if parent_node is None:
        parent_node = _infer_parent_node(parent)
    tmpl = copy.deepcopy(parent)
    tmpl = _swap_node_and_model(tmpl, parent_node, new_node, new_model)
    lower = new_node.lower()
    tmpl["id"] = f"tmpl-curator-{lower}"
    tmpl["name"] = f"moe-ontology-curator-{lower}"
    tmpl["description"] = (
        f"Ontology gap curator pinned to {new_node}. All 6 components use "
        f"{new_model}. Auto-generated from parent "
        f"moe-ontology-curator-{parent_node.lower()} (provisioner)."
    )
    tmpl["is_active"] = True
    return tmpl


def _infer_parent_node(parent: dict) -> str:
    """Read the node suffix from the parent's planner_model (e.g. mistral:7b@N06-M10)."""
    pm = parent.get("planner_model", "")
    if "@" in pm:
        return pm.rsplit("@", 1)[1]
    name = parent.get("name", "")
    m = re.match(r"moe-ontology-curator-(.+)", name)
    if m:
        return m.group(1).upper()
    raise ValueError(f"cannot infer parent node from template {name!r}")


def _pick_parent_template(all_templates: list[dict], server: dict) -> Optional[dict]:
    """Pick a parent template whose hardware family matches the server.

    Rough heuristic: if the server name contains the parent's node suffix
    (e.g. N11-M10-01 → parent N11-M10), use it. Otherwise fall back to
    the first aggregated ontology curator found.
    Supports single-word node names like AIHUB (moe-ontology-curator-aihub).
    """
    by_name = {t["name"]: t for t in all_templates if "ontology-curator" in t.get("name", "")}
    # Accept any number of hyphen-separated alphanumeric segments after the prefix
    agg = {n: t for n, t in by_name.items()
           if re.match(r"^moe-ontology-curator-[a-z0-9]+(-[a-z0-9]+)*$", n)}
    srv_name = server.get("name", "").upper()
    for name, tmpl in agg.items():
        suffix = name[len("moe-ontology-curator-"):].upper()
        if srv_name.startswith(suffix) or srv_name == suffix:
            return tmpl
    return next(iter(agg.values()), None)


async def _ensure_model_available(
    client: httpx.AsyncClient, base_url: str, model: str,
    progress_cb=None,
) -> None:
    """If `model` is not present on the Ollama server, pull it (streaming)."""
    tags_url = base_url.rstrip("/").rsplit("/v1", 1)[0] + "/api/tags"
    pull_url = base_url.rstrip("/").rsplit("/v1", 1)[0] + "/api/pull"
    try:
        r = await client.get(tags_url, timeout=10)
        r.raise_for_status()
        names = {m.get("name", "") for m in r.json().get("models", [])}
        if model in names or any(n.startswith(model + ":") or n == model for n in names):
            if progress_cb:
                await progress_cb("ready", "already present")
            return
    except Exception as e:
        if progress_cb:
            await progress_cb("pulling", f"tags check failed: {e}; attempting pull")

    if progress_cb:
        await progress_cb("pulling", f"pulling {model}")
    async with client.stream(
        "POST", pull_url, json={"name": model}, timeout=None,
    ) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_lines():
            if not chunk:
                continue
            try:
                msg = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            status = msg.get("status", "")
            if progress_cb and status:
                total = msg.get("total")
                completed = msg.get("completed")
                if total and completed:
                    pct = int(100 * completed / total)
                    await progress_cb("pulling", f"{status} {pct}%")
                else:
                    await progress_cb("pulling", status)


async def _set_redis_status(redis_cli, server_name: str, status: str, message: str = "") -> None:
    if redis_cli is None:
        return
    try:
        await redis_cli.hset(
            PROVISION_REDIS_PREFIX + server_name,
            mapping={"status": status, "message": message},
        )
        await redis_cli.expire(PROVISION_REDIS_PREFIX + server_name, 3600)
    except Exception:
        pass


async def get_provision_status(redis_cli, server_name: str) -> dict:
    if redis_cli is None:
        return {"status": "unknown", "message": "no redis"}
    try:
        data = await redis_cli.hgetall(PROVISION_REDIS_PREFIX + server_name)
        if not data:
            return {"status": "idle", "message": ""}
        return {"status": data.get("status", "unknown"), "message": data.get("message", "")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def provision_curator_for_server(
    server: dict, redis_cli=None, refresh_cache_cb=None,
) -> dict:
    """End-to-end: pick model → ensure pulled → clone template → upsert.

    Returns `{ok, template_name, model, message}`. Never raises — errors
    are reflected in Valkey status + returned dict.
    """
    name = server.get("name", "")
    result = {"ok": False, "template_name": None, "model": None, "message": ""}
    if not name:
        result["message"] = "server has no name"
        return result

    async def progress(status: str, msg: str) -> None:
        await _set_redis_status(redis_cli, name, status, msg)

    await progress("pending", "starting")
    model = pick_curator_model(server)
    result["model"] = model
    is_openai = server.get("api_type") == "openai"

    try:
        if is_openai:
            # Remote OpenAI-compatible endpoints: models are always available, no pull needed.
            await progress("pulling", "remote API — skipping model pull")
        else:
            async with httpx.AsyncClient() as client:
                await _ensure_model_available(client, server["url"], model, progress_cb=progress)

        all_templates = await database.list_admin_templates()
        parent = _pick_parent_template(all_templates, server)
        if parent is None:
            await progress("failed", "no parent curator template found")
            result["message"] = "no parent curator template"
            return result

        tmpl = build_curator_template(parent, new_node=name, new_model=model)

        # For OpenAI-compatible servers: upgrade planner/judge to the higher-quality
        # sovereign model (gpt-oss-120b-sovereign) while keeping experts on qwen for speed.
        if is_openai:
            planner_model = server.get("curator_planner_model", "gpt-oss-120b-sovereign")
            tmpl["planner_model"] = f"{planner_model}@{name}"
            tmpl["judge_model"] = f"{planner_model}@{name}"

        # Preserve existing template ID to avoid unique-name constraint violations:
        # build_curator_template may generate a new ID (tmpl-curator-*) but a template
        # with the same name may already exist under a different ID.
        existing = next(
            (t for t in all_templates if t.get("name") == tmpl.get("name")), None
        )
        if existing:
            tmpl["id"] = existing.get("id", tmpl["id"])

        await database.upsert_admin_template(tmpl)
        if refresh_cache_cb is not None:
            try:
                await refresh_cache_cb()
            except Exception:
                pass

        await progress("ready", f"{tmpl['name']} with {model}")
        result["ok"] = True
        result["template_name"] = tmpl["name"]
        result["message"] = "template ready"
    except Exception as e:
        await progress("failed", f"{type(e).__name__}: {str(e)[:200]}")
        result["message"] = str(e)
    return result


async def remove_curator_for_server(server_name: str, redis_cli=None) -> bool:
    """Soft-remove: just clear the provision status. Template itself remains."""
    await _set_redis_status(redis_cli, server_name, "idle", "ontology disabled")
    return True

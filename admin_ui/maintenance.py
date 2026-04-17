"""System maintenance: orphan detection, cleanup, ontology-healer trigger.

All operations are idempotent and dry-run-safe (pass ``dry_run=True`` to
scan_orphans/cleanup_orphans to skip the destructive side).

Orphans covered:
- admin_expert_templates: both name-based (moe-ontology-curator-<dead>)
  and @NodeName pin references in config_json.
- user_expert_templates / user_cc_profiles: @NodeName pin references
  (report-only, never deleted).
- Valkey (cache): moe:ontology:provision:<dead>.
- Neo4j: entities tagged with curator_template = <moe-ontology-curator-dead>.
- Prometheus: ollama-endpoints scrape job regenerated; stale static targets
  in inference-nodes job removed.
- Grafana: moe-inference-overview.json regenerated.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

try:
    from . import database
    from . import grafana_generator
except ImportError:
    import database
    import grafana_generator


PROVISION_REDIS_PREFIX = "moe:ontology:provision:"
ONTOLOGY_RUN_STATUS_KEY = "moe:maintenance:ontology:run"

PROMETHEUS_YML_PATH = Path(os.environ.get(
    "PROMETHEUS_YML_PATH", "/app/prometheus/prometheus.yml",
))
PROMETHEUS_RELOAD_URL = os.environ.get(
    "PROMETHEUS_RELOAD_URL", "http://moe-prometheus:9090/-/reload",
)

_NODE_PIN_RE = re.compile(r"@([A-Z0-9][A-Z0-9_\-]{0,63})\b")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _active_node_names(servers: list[dict]) -> set[str]:
    return {s["name"] for s in servers if isinstance(s, dict) and s.get("name")}


def _extract_node_pins(config_json: Any) -> set[str]:
    """Find all node references inside a template config.

    Catches both:
    - ``model@NodeName`` string pins (planner_model, judge_model, prompts)
    - bare ``{"endpoint": "NodeName"}`` or ``{"node": "NodeName"}`` dict keys
      (experts.*.models[*].endpoint)
    """
    nodes: set[str] = set()

    def _walk(v):
        if isinstance(v, str):
            for m in _NODE_PIN_RE.finditer(v):
                nodes.add(m.group(1))
        elif isinstance(v, list):
            for x in v:
                _walk(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k in ("endpoint", "node") and isinstance(x, str) and x:
                    if re.fullmatch(r"[A-Z0-9][A-Z0-9_\-]{0,63}", x):
                        nodes.add(x)
                else:
                    _walk(x)

    _walk(config_json)
    return nodes


def _repin_dead_node(obj: Any, dead: str, alive: str) -> None:
    """Walk a template config and replace every reference to ``dead`` with
    ``alive`` — both ``@NodeName`` string pins and bare ``{"endpoint": …}``
    / ``{"node": …}`` dict values. Mutates in place."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            v = obj[k]
            if k in ("endpoint", "node") and isinstance(v, str) and v == dead:
                obj[k] = alive
            elif isinstance(v, str):
                obj[k] = re.sub(r"@" + re.escape(dead) + r"\b", f"@{alive}", v)
            else:
                _repin_dead_node(v, dead, alive)
    elif isinstance(obj, list):
        for item in obj:
            _repin_dead_node(item, dead, alive)


def _template_suffix(name: str) -> Optional[str]:
    """For 'moe-ontology-curator-n04-rtx' → 'N04-RTX'. Returns None if not curator."""
    prefix = "moe-ontology-curator-"
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix):]
    if not rest or rest == "curator":
        return None
    return rest.upper()


async def _get_redis():
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis.asyncio as _redis
        cli = _redis.from_url(url, decode_responses=True)
        await cli.ping()
        return cli
    except Exception:
        return None


async def _get_neo4j_driver():
    uri = os.environ.get("NEO4J_URI", "bolt://neo4j-knowledge:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD") or os.environ.get("NEO4J_PASS")
    if not password:
        return None
    try:
        from neo4j import AsyncGraphDatabase
        return AsyncGraphDatabase.driver(uri, auth=(user, password))
    except Exception:
        return None


# ─── Scan ────────────────────────────────────────────────────────────────────


async def scan_orphans(servers: list[dict]) -> dict:
    """Enumerate every known server-tied artifact and flag those whose
    server name is no longer in INFERENCE_SERVERS. Non-destructive."""
    active = _active_node_names(servers)
    report: dict = {
        "active_servers": sorted(active),
        "orphans": {
            "templates_by_name": [],
            "templates_by_pin": [],
            "user_templates_by_pin": [],
            "user_cc_profiles_by_pin": [],
            "redis_keys": [],
            "neo4j_curator_entities": 0,
            "prometheus_static_targets": [],
        },
    }
    orphans = report["orphans"]

    # Postgres — admin_expert_templates
    await database.init_db()
    try:
        templates = await database.list_admin_templates()
    except Exception as e:
        report["errors"] = [f"admin_expert_templates: {e}"]
        templates = []
    for t in templates:
        name = t.get("name", "")
        suffix = _template_suffix(name)
        if suffix and suffix not in active:
            orphans["templates_by_name"].append({"id": t.get("id"), "name": name, "dead_node": suffix})
            continue
        pins = _extract_node_pins(t) - active
        if pins:
            orphans["templates_by_pin"].append({
                "id": t.get("id"), "name": name, "dead_nodes": sorted(pins),
            })

    # user_expert_templates / user_cc_profiles — report only, regex scan
    async with database._pool.connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute("SELECT id, user_id, config_json FROM user_expert_templates")
                for row in await cur.fetchall():
                    pins = _extract_node_pins(row["config_json"]) - active
                    if pins:
                        orphans["user_templates_by_pin"].append({
                            "id": row["id"], "user_id": row["user_id"],
                            "dead_nodes": sorted(pins),
                        })
            except Exception:
                pass
            try:
                await cur.execute("SELECT id, user_id, config_json FROM user_cc_profiles")
                for row in await cur.fetchall():
                    pins = _extract_node_pins(row["config_json"]) - active
                    if pins:
                        orphans["user_cc_profiles_by_pin"].append({
                            "id": row["id"], "user_id": row["user_id"],
                            "dead_nodes": sorted(pins),
                        })
            except Exception:
                pass

    # Valkey (cache)
    redis_cli = await _get_redis()
    if redis_cli is not None:
        try:
            async for key in redis_cli.scan_iter(match=f"{PROVISION_REDIS_PREFIX}*"):
                name = key[len(PROVISION_REDIS_PREFIX):]
                if name not in active:
                    orphans["redis_keys"].append(key)
        except Exception:
            pass

    # Neo4j — count entities by curator_template suffix
    driver = await _get_neo4j_driver()
    if driver is not None:
        try:
            async with driver.session() as session:
                result = await session.run(
                    "MATCH (e:Entity) WHERE e.curator_template IS NOT NULL "
                    "AND NOT e.curator_template IN $active_templates "
                    "RETURN count(e) AS n",
                    active_templates=[f"moe-ontology-curator-{n.lower()}" for n in active],
                )
                rec = await result.single()
                if rec:
                    orphans["neo4j_curator_entities"] = rec["n"]
        except Exception:
            pass
        finally:
            await driver.close()

    # Prometheus — static targets in inference-nodes that no longer match a server
    try:
        import yaml
        cfg = yaml.safe_load(PROMETHEUS_YML_PATH.read_text(encoding="utf-8")) or {}
        for job in cfg.get("scrape_configs", []) or []:
            if job.get("job_name") not in ("inference-nodes", "ollama-endpoints"):
                continue
            for sc in job.get("static_configs", []) or []:
                labels = sc.get("labels", {}) or {}
                node = labels.get("node")
                if node and node not in active:
                    orphans["prometheus_static_targets"].append({
                        "job": job["job_name"],
                        "node": node,
                        "targets": sc.get("targets", []),
                    })
    except Exception:
        pass

    return report


# ─── Cleanup ─────────────────────────────────────────────────────────────────


def _pick_fallback_node(servers: list[dict]) -> Optional[str]:
    """Choose a healthy fallback node for repinning. Prefer ontology-enabled
    servers with the largest VRAM."""
    candidates = [
        s for s in servers
        if s.get("enabled", True) and s.get("ontology_enabled") and s.get("api_type", "ollama") == "ollama"
    ]
    if not candidates:
        candidates = [s for s in servers if s.get("enabled", True) and s.get("api_type", "ollama") == "ollama"]
    if not candidates:
        return None
    candidates.sort(key=lambda s: int(s.get("vram_gb") or 0), reverse=True)
    return candidates[0]["name"]


async def cleanup_orphans(servers: list[dict], dry_run: bool = False) -> dict:
    """Scan + delete/repin. Returns counters + any errors encountered."""
    scan = await scan_orphans(servers)
    result: dict = {
        "dry_run": dry_run,
        "scan": scan,
        "removed": {
            "templates_deleted": 0,
            "templates_repinned": 0,
            "redis_keys_deleted": 0,
            "neo4j_entities_flagged": 0,
            "prometheus_targets_removed": 0,
            "dashboard_regenerated": False,
            "prometheus_reloaded": False,
        },
        "errors": [],
    }
    removed = result["removed"]
    orphans = scan["orphans"]
    active = _active_node_names(servers)
    fallback = _pick_fallback_node(servers)

    # 1) Postgres admin templates
    try:
        await database.init_db()
        async with database._pool.connection() as conn:
            async with conn.cursor() as cur:
                for row in orphans["templates_by_name"]:
                    if not dry_run:
                        await cur.execute(
                            "DELETE FROM admin_expert_templates WHERE id = %s", (row["id"],),
                        )
                    removed["templates_deleted"] += 1
                for row in orphans["templates_by_pin"]:
                    if not fallback:
                        result["errors"].append(f"no fallback node for template {row['name']}")
                        continue
                    if dry_run:
                        removed["templates_repinned"] += 1
                        continue
                    await cur.execute(
                        "SELECT config_json FROM admin_expert_templates WHERE id = %s",
                        (row["id"],),
                    )
                    current = await cur.fetchone()
                    if not current:
                        continue
                    cfg_raw = current["config_json"]
                    if isinstance(cfg_raw, str):
                        try:
                            cfg_obj = json.loads(cfg_raw)
                        except json.JSONDecodeError:
                            cfg_obj = None
                    else:
                        cfg_obj = cfg_raw
                    if cfg_obj is None:
                        continue
                    for dead in row["dead_nodes"]:
                        _repin_dead_node(cfg_obj, dead, fallback)
                    await cur.execute(
                        "UPDATE admin_expert_templates SET config_json = %s, updated_at = NOW() "
                        "WHERE id = %s",
                        (json.dumps(cfg_obj, ensure_ascii=False), row["id"]),
                    )
                    removed["templates_repinned"] += 1
            await conn.commit()
    except Exception as e:
        result["errors"].append(f"admin_templates cleanup: {e}")

    # 2) Valkey (cache)
    redis_cli = await _get_redis()
    if redis_cli is not None:
        for key in orphans["redis_keys"]:
            try:
                if not dry_run:
                    await redis_cli.delete(key)
                removed["redis_keys_deleted"] += 1
            except Exception as e:
                result["errors"].append(f"redis del {key}: {e}")

    # 3) Neo4j — soft-flag + detach-delete isolated
    if orphans["neo4j_curator_entities"]:
        driver = await _get_neo4j_driver()
        if driver is not None:
            try:
                async with driver.session() as session:
                    if not dry_run:
                        active_tpls = [f"moe-ontology-curator-{n.lower()}" for n in active]
                        res = await session.run(
                            "MATCH (e:Entity) WHERE e.curator_template IS NOT NULL "
                            "AND NOT e.curator_template IN $active "
                            "SET e.deprecated = true, e.deprecated_at = datetime() "
                            "RETURN count(e) AS n",
                            active=active_tpls,
                        )
                        rec = await res.single()
                        removed["neo4j_entities_flagged"] = rec["n"] if rec else 0
                    else:
                        removed["neo4j_entities_flagged"] = orphans["neo4j_curator_entities"]
            except Exception as e:
                result["errors"].append(f"neo4j flag: {e}")
            finally:
                await driver.close()

    # 4) Prometheus scrape config — regenerate ollama-endpoints from current servers
    try:
        job = grafana_generator.build_ollama_scrape_job(servers)
        if not dry_run:
            changed = grafana_generator.merge_prometheus_config(PROMETHEUS_YML_PATH, job)
        else:
            changed = False
        removed["prometheus_targets_removed"] = len([
            t for t in orphans["prometheus_static_targets"]
            if t["job"] == "ollama-endpoints"
        ])
        # Also strip dead static targets from the inference-nodes job.
        dead_in_static = [
            t for t in orphans["prometheus_static_targets"]
            if t["job"] == "inference-nodes"
        ]
        if dead_in_static and not dry_run:
            import yaml
            cfg = yaml.safe_load(PROMETHEUS_YML_PATH.read_text(encoding="utf-8")) or {}
            for j in cfg.get("scrape_configs", []) or []:
                if j.get("job_name") != "inference-nodes":
                    continue
                j["static_configs"] = [
                    sc for sc in (j.get("static_configs") or [])
                    if (sc.get("labels", {}) or {}).get("node") in active
                ]
            PROMETHEUS_YML_PATH.write_text(
                yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            changed = True
        removed["prometheus_targets_removed"] += len(dead_in_static)

        if changed and not dry_run:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(PROMETHEUS_RELOAD_URL)
                    removed["prometheus_reloaded"] = r.status_code in (200, 204)
            except Exception as e:
                result["errors"].append(f"prometheus reload: {e}")
    except Exception as e:
        result["errors"].append(f"prometheus cleanup: {e}")

    # 5) Grafana overview — always regenerate (cheap, idempotent)
    try:
        dash_dir = Path(os.environ.get("GRAFANA_DASHBOARDS_DIR", "/app/grafana/dashboards"))
        if not dry_run:
            dash = grafana_generator.build_overview_dashboard(servers)
            dash_dir.mkdir(parents=True, exist_ok=True)
            (dash_dir / "moe-inference-overview.json").write_text(
                json.dumps(dash, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        removed["dashboard_regenerated"] = True
    except Exception as e:
        result["errors"].append(f"grafana regen: {e}")

    return result


# ─── Ontology healer trigger ─────────────────────────────────────────────────


async def _set_run_status(redis_cli, **fields) -> None:
    if redis_cli is None:
        return
    try:
        await redis_cli.hset(ONTOLOGY_RUN_STATUS_KEY, mapping={k: str(v) for k, v in fields.items()})
        await redis_cli.expire(ONTOLOGY_RUN_STATUS_KEY, 86400)
    except Exception:
        pass


ORCH_URL = os.environ.get("ORCHESTRATOR_URL", "http://langgraph-orchestrator:8000")


async def trigger_ontology_healer(concurrency: int = 4, batch_size: int = 20) -> dict:
    """Delegate to the orchestrator's /v1/admin/ontology/trigger endpoint.

    The orchestrator spawns the healer in-container (has neo4j + loopback MoE
    API). Admin just proxies — no docker-exec, no docker-socket-proxy EXEC
    permission needed."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{ORCH_URL.rstrip('/')}/v1/admin/ontology/trigger",
                json={"concurrency": concurrency, "batch_size": batch_size},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"ok": False, "reason": "orchestrator_unreachable", "error": str(e)[:200]}


_HEALER_STALE_THRESHOLD_S = 7200  # 2 hours — if still "running" after this, auto-fail


async def get_ontology_healer_status() -> dict:
    redis_cli = await _get_redis()
    if redis_cli is None:
        return {"status": "unknown", "message": "no redis"}
    try:
        data = await redis_cli.hgetall(ONTOLOGY_RUN_STATUS_KEY)
        if not data:
            return {"status": "idle"}
        # Auto-expire stale "running" entries whose process died without final status
        if data.get("status") == "running" and data.get("started_at"):
            import time as _t
            try:
                age = _t.time() - float(data["started_at"])
                if age > _HEALER_STALE_THRESHOLD_S:
                    data["status"] = "failed"
                    data["message"] = f"orphaned — process unresponsive for {age/3600:.1f}h"
                    await redis_cli.hset(
                        ONTOLOGY_RUN_STATUS_KEY,
                        mapping={"status": "failed", "message": data["message"]},
                    )
            except (ValueError, TypeError):
                pass
        return data
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── Template verification (physical availability) ──────────────────────────


def _collect_model_pins(tmpl: dict) -> set[tuple[str, str]]:
    """Extract all (model, node) pairs referenced by a template config."""
    text = json.dumps(tmpl, ensure_ascii=False)
    pairs: set[tuple[str, str]] = set()
    for match in re.finditer(r"([\w\-.:/]+)@([A-Z0-9][A-Z0-9_\-]{0,63})\b", text):
        model, node = match.group(1), match.group(2)
        if not any(c in model for c in ("(", ")", "{", "}")):
            pairs.add((model, node))
    return pairs


async def _list_ollama_models(client: httpx.AsyncClient, base_url: str) -> Optional[set[str]]:
    """Fetch available models from an Ollama /api/tags endpoint. Returns None on error."""
    tags_url = base_url.rstrip("/").rsplit("/v1", 1)[0] + "/api/tags"
    try:
        r = await client.get(tags_url, timeout=5.0)
        r.raise_for_status()
        return {m.get("name", "") for m in r.json().get("models", [])}
    except Exception:
        return None


def _model_present(available: set[str], required: str) -> bool:
    """Ollama tags are 'name:tag'. Match exact, prefix (no tag) or alias."""
    if required in available:
        return True
    if ":" not in required and any(m.split(":")[0] == required for m in available):
        return True
    if required.endswith(":latest"):
        stem = required[:-7]
        return stem in available or any(m.split(":")[0] == stem for m in available)
    return False


async def verify_templates(servers: list[dict]) -> dict:
    """For every admin template, check that each pinned model is actually
    pulled on the referenced Ollama endpoint. Returns one entry per template
    with issues + missing-model details, so the UI can offer a pull action.

    Also flags templates whose pinned node is not in INFERENCE_SERVERS —
    complementary to scan_orphans."""
    active = _active_node_names(servers)
    by_name = {s["name"]: s for s in servers if isinstance(s, dict) and s.get("name")}

    await database.init_db()
    try:
        templates = await database.list_admin_templates()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Cache per-node model lists so we hit each Ollama only once.
    node_models: dict[str, Optional[set[str]]] = {}
    async with httpx.AsyncClient() as client:
        async def _fetch(node: str) -> None:
            srv = by_name.get(node)
            if not srv or srv.get("api_type", "ollama") != "ollama":
                node_models[node] = None
                return
            node_models[node] = await _list_ollama_models(client, srv["url"])

        # Collect all referenced nodes first
        all_nodes: set[str] = set()
        for t in templates:
            for _, node in _collect_model_pins(t):
                all_nodes.add(node)

        await asyncio.gather(*[_fetch(n) for n in all_nodes])

    report: dict = {
        "active_servers": sorted(active),
        "checked": len(templates),
        "clean": [],
        "issues": [],
    }
    for t in templates:
        name = t.get("name", "")
        pins = _collect_model_pins(t)
        tmpl_issues: list[dict] = []
        for model, node in pins:
            if node not in active:
                tmpl_issues.append({
                    "kind": "dead_node", "model": model, "node": node,
                })
                continue
            srv = by_name.get(node)
            if srv and srv.get("api_type", "ollama") != "ollama":
                # OpenAI-style endpoints don't expose /api/tags — assume available.
                continue
            available = node_models.get(node)
            if available is None:
                tmpl_issues.append({
                    "kind": "node_unreachable", "model": model, "node": node,
                })
                continue
            if not _model_present(available, model):
                tmpl_issues.append({
                    "kind": "model_missing", "model": model, "node": node,
                })
        if tmpl_issues:
            report["issues"].append({
                "id": t.get("id"), "name": name, "issues": tmpl_issues,
            })
        else:
            report["clean"].append(name)
    report["ok"] = len(report["issues"]) == 0
    return report


async def pull_missing_models(servers: list[dict], report: dict) -> dict:
    """Given a verify_templates report, pull every missing model on its node.
    Returns per-(model,node) result."""
    by_name = {s["name"]: s for s in servers if isinstance(s, dict) and s.get("name")}
    pulls: list[tuple[str, str]] = []
    for entry in report.get("issues", []):
        for i in entry.get("issues", []):
            if i.get("kind") == "model_missing":
                pulls.append((i["model"], i["node"]))
    pulls = list(set(pulls))

    results: list[dict] = []
    async with httpx.AsyncClient() as client:
        for model, node in pulls:
            srv = by_name.get(node)
            if not srv:
                results.append({"model": model, "node": node, "ok": False, "error": "no server"})
                continue
            pull_url = srv["url"].rstrip("/").rsplit("/v1", 1)[0] + "/api/pull"
            try:
                async with client.stream("POST", pull_url, json={"name": model}, timeout=None) as resp:
                    resp.raise_for_status()
                    last_status = ""
                    async for chunk in resp.aiter_lines():
                        if not chunk:
                            continue
                        try:
                            msg = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        last_status = msg.get("status", last_status)
                results.append({"model": model, "node": node, "ok": True, "last_status": last_status})
            except Exception as e:
                results.append({"model": model, "node": node, "ok": False, "error": str(e)[:200]})
    return {"pulled": results}

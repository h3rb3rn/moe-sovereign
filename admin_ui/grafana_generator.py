"""Generate the Grafana master dashboard + Prometheus scrape config.

Produces a single file-provisioned dashboard that lets operators pick any
inference node via a `node` variable and see Ollama runtime metrics plus
node-exporter host metrics side by side.

Writing the dashboard JSON into /var/lib/grafana/dashboards (bind-mounted
from grafana/dashboards/) is enough — the file provisioner rescans every
30 s (dashboards.yml: updateIntervalSeconds). Prometheus needs an explicit
/-/reload call after the scrape config changes.
"""
from __future__ import annotations

import copy
import re
from typing import Iterable
from urllib.parse import urlparse

import yaml


DASHBOARD_UID = "moe-inf-overview"
DASHBOARD_TITLE = "MoE Inference — Overview (auto)"
PROM_JOB_NAME = "ollama-endpoints"
PROM_DS_UID = "moe-prometheus"


def _split_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def build_ollama_scrape_job(servers: Iterable[dict]) -> dict:  # noqa: ARG001
    """Historical: Ollama (<= 0.20.x) does not expose /metrics. Returns an
    empty job so merge_prometheus_config removes any stale entry. Inference
    metrics come from the orchestrator's moe_inference_server_* counters
    (scraped via the moe-orchestrator job)."""
    return {"job_name": PROM_JOB_NAME, "static_configs": []}


def merge_prometheus_config(yaml_path, new_job: dict) -> bool:
    """Idempotently replace or insert our scrape job in prometheus.yml.

    If ``new_job`` has no targets (e.g. the Ollama job after we dropped
    /metrics scraping), remove the job entirely so Prometheus doesn't log
    errors about empty static_configs.

    Returns True if the file changed.
    """
    from pathlib import Path
    path = Path(yaml_path)
    original = path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(original) or {}
    jobs = cfg.setdefault("scrape_configs", [])
    existing_idx = next(
        (i for i, j in enumerate(jobs) if j.get("job_name") == new_job["job_name"]),
        None,
    )
    targets_empty = not (new_job.get("static_configs") or [])
    if targets_empty:
        if existing_idx is None:
            return False
        jobs.pop(existing_idx)
    elif existing_idx is not None:
        if jobs[existing_idx] == new_job:
            return False
        jobs[existing_idx] = new_job
    else:
        jobs.append(new_job)
    new_text = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)
    if new_text.strip() == original.strip():
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


# ─── Grafana dashboard JSON ──────────────────────────────────────────────────


def _ds() -> dict:
    return {"type": "prometheus", "uid": PROM_DS_UID}


def _target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {
        "datasource": _ds(),
        "expr": expr,
        "legendFormat": legend,
        "refId": ref,
        "editorMode": "code",
        "range": True,
    }


def _panel(
    id_: int, title: str, gridPos: dict, targets: list[dict],
    unit: str = "none", panel_type: str = "timeseries",
) -> dict:
    return {
        "id": id_,
        "type": panel_type,
        "title": title,
        "datasource": _ds(),
        "gridPos": gridPos,
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {"lineWidth": 1, "fillOpacity": 10},
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
    }


def _stat_panel(id_: int, title: str, gridPos: dict, expr: str, unit: str = "short") -> dict:
    return {
        "id": id_,
        "type": "stat",
        "title": title,
        "datasource": _ds(),
        "gridPos": gridPos,
        "targets": [_target(expr, "{{node}}")],
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value_and_name",
            "colorMode": "value",
        },
    }


def build_overview_dashboard(servers: list[dict]) -> dict:
    """Build the full Grafana dashboard JSON for file provisioning."""
    node_names = [s.get("name") for s in servers if s.get("name")]
    default_nodes = node_names[:5] if node_names else ["N04-RTX"]

    # Panels laid out in a 24-col grid
    panels: list[dict] = []
    pid = 1

    # Row 1: Status stat panels (Ollama loaded models, in-progress, pending)
    panels.append(_stat_panel(pid, "Loaded models",
        {"x": 0, "y": 0, "w": 8, "h": 4},
        f'sum by (node) (ollama_loaded_models{{job="{PROM_JOB_NAME}", node=~"$node"}})'))
    pid += 1
    panels.append(_stat_panel(pid, "In-progress requests",
        {"x": 8, "y": 0, "w": 8, "h": 4},
        f'sum by (node) (ollama_requests_in_progress{{job="{PROM_JOB_NAME}", node=~"$node"}})'))
    pid += 1
    panels.append(_stat_panel(pid, "Pending queue",
        {"x": 16, "y": 0, "w": 8, "h": 4},
        f'sum by (node) (ollama_pending_requests{{job="{PROM_JOB_NAME}", node=~"$node"}})'))
    pid += 1

    # Row 2: Request throughput & p95 latency
    panels.append(_panel(pid, "Ollama request rate (req/s)",
        {"x": 0, "y": 4, "w": 12, "h": 8},
        [_target(
            f'sum by (node) (rate(ollama_request_duration_seconds_count{{job="{PROM_JOB_NAME}", node=~"$node"}}[2m]))',
            "{{node}}")],
        unit="reqps"))
    pid += 1
    panels.append(_panel(pid, "Ollama p95 latency (s)",
        {"x": 12, "y": 4, "w": 12, "h": 8},
        [_target(
            f'histogram_quantile(0.95, sum by (le, node) (rate(ollama_request_duration_seconds_bucket{{job="{PROM_JOB_NAME}", node=~"$node"}}[5m])))',
            "{{node}}")],
        unit="s"))
    pid += 1

    # Row 3: Host metrics (node-exporter)
    panels.append(_panel(pid, "CPU busy %",
        {"x": 0, "y": 12, "w": 8, "h": 8},
        [_target(
            '100 - (avg by (node) (rate(node_cpu_seconds_total{job="inference-nodes", mode="idle", node=~"$node"}[2m])) * 100)',
            "{{node}}")],
        unit="percent"))
    pid += 1
    panels.append(_panel(pid, "Memory used %",
        {"x": 8, "y": 12, "w": 8, "h": 8},
        [_target(
            '100 * (1 - (node_memory_MemAvailable_bytes{job="inference-nodes", node=~"$node"} / node_memory_MemTotal_bytes{job="inference-nodes", node=~"$node"}))',
            "{{node}}")],
        unit="percent"))
    pid += 1
    panels.append(_panel(pid, "Disk root used %",
        {"x": 16, "y": 12, "w": 8, "h": 8},
        [_target(
            '100 - (node_filesystem_avail_bytes{job="inference-nodes", mountpoint="/", node=~"$node"} / node_filesystem_size_bytes{job="inference-nodes", mountpoint="/", node=~"$node"}) * 100',
            "{{node}}")],
        unit="percent"))
    pid += 1

    # Row 4: Curator template activity (for whitepaper)
    panels.append(_panel(pid, "Curator template invocations (rate 5m)",
        {"x": 0, "y": 20, "w": 24, "h": 8},
        [_target(
            'sum by (template) (rate(moe_template_invocations_total{template=~"moe-ontology-curator.*"}[5m]))',
            "{{template}}")],
        unit="ops"))
    pid += 1

    # Template variables
    templating = {
        "list": [{
            "name": "node",
            "type": "query",
            "datasource": _ds(),
            "query": {"query": "label_values(node_uname_info, node)", "refId": "node"},
            "refresh": 2,
            "includeAll": True,
            "multi": True,
            "current": {
                "selected": False,
                "text": "All",
                "value": "$__all",
            },
        }],
    }

    return {
        "uid": DASHBOARD_UID,
        "title": DASHBOARD_TITLE,
        "tags": ["moe", "auto-generated", "inference"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "templating": templating,
        "panels": panels,
        "annotations": {"list": []},
        "editable": True,
    }

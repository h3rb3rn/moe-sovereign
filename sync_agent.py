#!/usr/bin/env python3
"""
MkDocs Sync-Agent — automatically populates MkDocs documentation from:
  - Expert system prompts (EXPERTS_PATH/*.md)
  - Live system status (Docker + Prometheus API)
  - Self-Correction Journal (FEW_SHOT_DIR/*.md)
  - Changelog pending entries (changelog_pending.jsonl)

Runs every 15 minutes (controlled by the Docker Compose while-loop).
All writes are atomic (write tmp → rename).
"""

from __future__ import annotations
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import docker as docker_sdk
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sync-agent] %(message)s",
)
logger = logging.getLogger("mkdocs-sync")

# ── Konfiguration ─────────────────────────────────────────────────────────────
DOCS_DIR        = Path(os.getenv("DOCS_DIR",        "/app/docs"))
EXPERTS_PATH    = Path(os.getenv("EXPERTS_PATH",    "/opt/moe-sovereign/prompts/systemprompt"))
FEW_SHOT_DIR    = Path(os.getenv("FEW_SHOT_DIR",    "/moe-few-shot"))
PROMETHEUS_URL  = os.getenv("PROMETHEUS_URL",       "http://moe-prometheus:9090")


# ── Atomic write ─────────────────────────────────────────────────────────────

def atomic_write(path: Path, content: str) -> None:
    """Writes atomically: tmp file → rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ── Expert pages ─────────────────────────────────────────────────────────────

_EXPERT_DESC = {
    "general":          "Generalist expert for general requests",
    "code_reviewer":    "Senior SWE — OWASP / code quality",
    "creative_writer":  "Skilled author (multi-language)",
    "data_analyst":     "Data science / statistics",
    "judge":            "MoE orchestrator synthesizer",
    "legal_advisor":    "Legal advisor (German law)",
    "math":             "Mathematics and physics",
    "medical_consult":  "Specialist physician (S3/AWMF/WHO)",
    "planer":           "MoE orchestrator / planner",
    "reasoning":        "Analytical multi-step problem solver",
    "science":          "Natural scientist",
    "technical_support": "Senior IT/DevOps",
    "translation":      "Professional translator (DE↔EN↔FR↔ES↔IT)",
    "vision":           "Image and document analysis",
}


def sync_experts() -> int:
    """Creates docs/experts/{name}.md for each expert prompt + index page."""
    if not EXPERTS_PATH.exists():
        logger.warning(f"EXPERTS_PATH not found: {EXPERTS_PATH}")
        return 0
    experts_dir = DOCS_DIR / "experts"
    count = 0
    names = []
    for src in sorted(EXPERTS_PATH.glob("*.md")):
        name = src.stem
        names.append(name)
        content_raw = src.read_text(encoding="utf-8")
        dest = experts_dir / f"{name}.md"
        # Only update timestamp when the prompt content actually changed
        existing_ts = None
        if dest.exists():
            for line in dest.read_text(encoding="utf-8").splitlines():
                if line.startswith("*Last updated:"):
                    existing_ts = line
                    break
        body = (
            f"**Role:** {_EXPERT_DESC.get(name, '—')}\n\n"
            "## System Prompt\n\n"
            f"```\n{content_raw.strip()}\n```\n"
        )
        ts_line = existing_ts or f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*"
        if not dest.exists() or body not in dest.read_text(encoding="utf-8"):
            ts_line = f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*"
        page = f"# Expert: {name}\n\n{ts_line}\n\n{body}"
        atomic_write(dest, page)
        count += 1

    # Index page
    index_lines = [
        "# Expert Overview\n\n",
        f"*{len(names)} experts — updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n",
        "| Expert | Description | Page |\n",
        "|--------|-------------|------|\n",
    ]
    for n in names:
        desc = _EXPERT_DESC.get(n, "—")
        index_lines.append(f"| **{n}** | {desc} | [{n}.md]({n}.md) |\n")
    atomic_write(experts_dir / "index.md", "".join(index_lines))
    logger.info(f"  Experts: {count} pages updated")
    return count


# ── System-Status ─────────────────────────────────────────────────────────────

def _docker_status() -> str:
    """Returns Docker container status via Python SDK."""
    if not HAS_DOCKER:
        return "*Docker SDK not available (pip install docker)*"
    try:
        client = docker_sdk.from_env()
        containers = client.containers.list(
            filters={"label": "com.docker.compose.project=moe-infra"}
        )
        rows = ["| Container | Status | Ports |", "|-----------|--------|-------|"]
        for c in sorted(containers, key=lambda x: x.name):
            ports = ", ".join(
                f"{h[0]['HostPort']}→{p}"
                for p, h in (c.ports or {}).items()
                if h
            ) or "—"
            rows.append(f"| {c.name} | {c.status} | {ports} |")
        return "\n".join(rows)
    except Exception as e:
        return f"*Docker status unavailable: {e}*"


def _prometheus_metric(metric: str) -> str:
    """Fetches a single Prometheus metric value."""
    if not HAS_REQUESTS:
        return "?"
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": metric},
            timeout=5,
        )
        data = r.json()
        results = data.get("data", {}).get("result", [])
        if results:
            val = results[0]["value"][1]
            # Format: float → int if possible
            try:
                return str(int(float(val)))
            except Exception:
                return val
    except Exception:
        pass
    return "?"


def sync_status() -> None:
    """Generates docs/system/status.md with live metrics from Prometheus and Docker."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    entities  = _prometheus_metric("moe_graph_entities_total")
    relations = _prometheus_metric("moe_graph_relations_total")
    chroma    = _prometheus_metric("moe_chroma_documents_total")
    req_total = _prometheus_metric("sum(moe_requests_total)")

    docker_table = _docker_status()

    page = f"""# System Status

!!! info "Auto-generated"
    This page is updated every 15 minutes by the `moe-docs-sync` service.

*Last updated: **{ts}***

## Metrics

| Metric | Value |
|--------|-------|
| Neo4j Entities | {entities} |
| Neo4j Relations | {relations} |
| ChromaDB Documents | {chroma} |
| Total Requests | {req_total} |

## Docker Containers

{docker_table}

---
*Automatically generated by `moe-docs-sync`*
"""
    atomic_write(DOCS_DIR / "system" / "status.md", page)
    logger.info("  Status page updated")


# ── Self-Correction Journal ───────────────────────────────────────────────────

def sync_self_correction() -> None:
    """Aggregates few-shot files into docs/self_correction/journal.md."""
    target = DOCS_DIR / "self_correction" / "journal.md"
    if not FEW_SHOT_DIR.exists():
        atomic_write(target, "# Self-Correction Journal\n\n*No entries yet.*\n")
        return
    files = sorted(FEW_SHOT_DIR.glob("*.md"))
    if not files:
        atomic_write(target, "# Self-Correction Journal\n\n*No corrections recorded yet.*\n")
        return

    parts = [
        "# Self-Correction Journal\n\n",
        f"*{len(files)} categories — updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n",
    ]
    for fp in files:
        parts.append(f"## {fp.stem}\n\n")
        parts.append(fp.read_text(encoding="utf-8"))
        parts.append("\n")
    atomic_write(target, "".join(parts))
    logger.info(f"  Self-Correction Journal: {len(files)} categories")


# ── Changelog Sync ───────────────────────────────────────────────────────────

PENDING_LOG = DOCS_DIR / "changelog_pending.jsonl"
CHANGELOG   = DOCS_DIR / "changelog.md"

_SECTION_LABELS = {
    "Admin UI":        "Admin UI",
    "Orchestrator":    "Orchestrator (main.py)",
    "Infrastructure":  "Infrastructure / Docker",
    "Docs Sync":       "Docs Sync Agent",
    "Documentation":   "Documentation (mkDocs)",
    "System":          "System",
}


def sync_changelog() -> None:
    """Reads changelog_pending.jsonl, deduplicates entries, and prepends a
    structured block to docs/changelog.md. Each entry is processed exactly once
    (file is cleared after reading).

    Output format per date: one `## [auto-DATE]` section with metadata table
    showing impact, git context, and affected files per component.
    No sensitive data (env values, credentials, server addresses) is emitted.
    """
    if not PENDING_LOG.exists():
        return
    try:
        raw = PENDING_LOG.read_text(encoding="utf-8").strip()
    except Exception:
        return
    if not raw:
        return

    import json as _json
    from collections import defaultdict

    entries = []
    for line in raw.splitlines():
        try:
            entries.append(_json.loads(line))
        except Exception:
            pass
    if not entries:
        return

    # Deduplicate: keep one entry per (date, section, file_basename) tuple
    seen: set = set()
    deduped = []
    for e in entries:
        key = (e["date"], e["section"], e["file_basename"])
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # Group by date, then by section
    by_date: dict = defaultdict(lambda: defaultdict(list))
    meta_by_date: dict = {}
    for e in deduped:
        by_date[e["date"]][e["section"]].append(e)
        # Keep highest-impact metadata per date
        existing_meta = meta_by_date.get(e["date"], {})
        impact_rank = {"patch": 0, "minor": 1, "major": 2}
        if impact_rank.get(e.get("impact", "patch"), 0) >= impact_rank.get(existing_meta.get("impact", "patch"), 0):
            meta_by_date[e["date"]] = e

    # Read existing changelog
    existing = ""
    if CHANGELOG.exists():
        existing = CHANGELOG.read_text(encoding="utf-8")
    lines = existing.splitlines(keepends=True)
    header_end = 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## ["):
            header_end = i
            break
    header = "".join(lines[:header_end])
    body   = "".join(lines[header_end:])

    if not header:
        header = (
            "# Changelog\n\n"
            "All notable changes to the Sovereign MoE Orchestrator are documented here.\n"
            "Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)\n\n"
            "> **Metadata fields:** `impact` · `breaking` · `domain` · `git` · `files`\n\n"
            "---\n\n"
        )

    new_sections = []
    for date in sorted(by_date.keys(), reverse=True):
        meta = meta_by_date.get(date, {})
        impact   = meta.get("impact", "patch")
        git_hash = meta.get("git_hash", "—")
        git_branch = meta.get("git_branch", "—")

        # Collect all affected domains from sections present
        domains = sorted({_SECTION_LABELS.get(s, s) for s in by_date[date]})
        domain_str = ", ".join(domains)

        # Count unique files changed
        all_files = sorted({e["file_basename"] for sfiles in by_date[date].values() for e in sfiles})
        total_files = len(all_files)

        block = [f"## [auto-{date}] - {date}\n\n### Changed\n\n"]

        for section in sorted(by_date[date].keys()):
            label = _SECTION_LABELS.get(section, section)
            section_files = sorted({e["file_basename"] for e in by_date[date][section]})
            file_list = ", ".join(f"`{f}`" for f in section_files)
            block.append(f"- **{label}**: {file_list}\n")

        block.append(
            f"\n| Metadata | Value |\n"
            f"|---|---|\n"
            f"| `impact` | {impact} |\n"
            f"| `breaking` | no |\n"
            f"| `domain` | {domain_str} |\n"
            f"| `git` | `{git_hash}` on `{git_branch}` |\n"
            f"| `files changed` | {total_files} |\n"
            f"\n---\n\n"
        )
        new_sections.append("".join(block))

    new_content = header + "".join(new_sections) + body
    atomic_write(CHANGELOG, new_content)

    PENDING_LOG.write_text("", encoding="utf-8")
    logger.info(f"  Changelog: {len(deduped)} unique entries ({len(entries) - len(deduped)} duplicates removed) for {len(by_date)} date(s)")


# ── Main ──────────────────────────────────────────────────────────────────────

def reload_docs_server() -> None:
    """Restart the moe-docs container so mkdocs serve picks up file changes.

    mkdocs serve's livereload uses inotify which is unreliable across
    read-only Docker bind mounts — changes to status.md are often missed.
    A container restart is the cheapest reliable reload trigger.
    """
    if not HAS_DOCKER:
        return
    try:
        client = docker_sdk.from_env()
        client.containers.get("moe-docs").restart(timeout=5)
        logger.info("  moe-docs reloaded")
    except Exception as e:
        logger.warning(f"  moe-docs reload skipped: {e}")


def main() -> None:
    logger.info("=== MkDocs Sync-Agent started ===")
    try:
        sync_experts()
        sync_status()
        sync_self_correction()
        sync_changelog()
        reload_docs_server()
        logger.info("=== Sync complete ===")
    except Exception as e:
        logger.error(f"Sync error: {e}", exc_info=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MkDocs Sync-Agent")
    parser.add_argument("--changelog-only", action="store_true",
                        help="Nur Changelog aus changelog_pending.jsonl aktualisieren")
    args = parser.parse_args()
    if args.changelog_only:
        sync_changelog()
    else:
        main()

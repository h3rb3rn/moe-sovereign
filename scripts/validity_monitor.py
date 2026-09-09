#!/usr/bin/env python3
"""
scripts/validity_monitor.py — Standalone system-validity monitor for the
moe-infra Docker Compose deployment.

Runs independently of any chat/agent session (cron, systemd timer, or
`--loop`) and checks for exactly the class of problems that fail silently
and get noticed late:

  1. Permission/ownership breakage: a running container's recent logs show
     a permission-denied / read-only-filesystem / MISCONF pattern -- the
     class of bug behind the 2026-08-31 Valkey RDB-persistence incident.
     install.sh intentionally chowns several bind-mounted data directories
     to 0:0 on update and relies on each container's own entrypoint to
     re-chown to its runtime UID on next start; a long-running container
     that predates an update sits broken until someone notices (see
     install.sh's "_upd_chown 0 0 ... # valkey entrypoint: chown -> valkey
     (999)" comments).
  2. Crash-looping or unhealthy containers: Docker health status, plus a
     RestartCount delta between two checks -- a nonzero RestartCount alone
     just means "restarted at some point in this container's history", not
     "looping right now".
  3. Low local disk space on this deployment host, where Postgres/Valkey/
     Neo4j/Chroma/Kafka's bind-mounted volumes actually live.
  4. Host RAM/swap pressure: found live 2026-08-31 with swap at 100% and
     ~591MB RAM free on a 35GB shared host, with no OOM-kill yet -- a silent
     precursor to one, showing up here before any single container's own
     cgroup memory limit would trip.

Escalation: a structured JSONL alert log (VALIDITY_ALERT_LOG) plus a stderr
line, with a per-alert-key cooldown persisted to a state file (VALIDITY_STATE_FILE)
so a persistent condition doesn't spam on every invocation, including across
separate cron-triggered runs of this script (state is not just in-process).

This intentionally shells out to the `docker` CLI rather than requiring
docker.sock access from inside a container: run it directly on the
deployment host (cron/systemd timer), the same way benchmarks/watchdog.sh
supervises the benchmark process from outside any container.

Usage:
  python3 scripts/validity_monitor.py                    # one pass
  python3 scripts/validity_monitor.py --loop --interval 300
  python3 scripts/validity_monitor.py --json              # machine-readable summary to stdout, no alert side effects
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("validity-monitor")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

ALERT_LOG = Path(os.environ.get("VALIDITY_ALERT_LOG", DATA_DIR / "validity_monitor_alerts.jsonl"))
STATE_FILE = Path(os.environ.get("VALIDITY_STATE_FILE", DATA_DIR / "validity_monitor_state.json"))
LOCK_FILE = Path(os.environ.get("VALIDITY_LOCK_FILE", DATA_DIR / "validity_monitor.lock"))

ALERT_COOLDOWN_S = int(os.environ.get("VALIDITY_ALERT_COOLDOWN_S", "3600"))
LOG_LOOKBACK_S = int(os.environ.get("VALIDITY_LOG_LOOKBACK_S", "600"))
DISK_WARN_PCT = float(os.environ.get("VALIDITY_DISK_WARN_PCT", "85"))
DISK_CRIT_PCT = float(os.environ.get("VALIDITY_DISK_CRIT_PCT", "95"))
# Found live 2026-08-31: swap at 100%, ~591MB free RAM on a 35GB shared host,
# with zero OOM-kills yet -- a real, silent precursor to one. free/available
# accounts for reclaimable page cache the way `free -h`'s "available" column
# does; MemFree alone would false-alarm constantly on a healthy, cache-heavy host.
MEM_AVAILABLE_WARN_PCT = float(os.environ.get("VALIDITY_MEM_AVAILABLE_WARN_PCT", "15"))
SWAP_WARN_PCT = float(os.environ.get("VALIDITY_SWAP_WARN_PCT", "80"))
SWAP_CRIT_PCT = float(os.environ.get("VALIDITY_SWAP_CRIT_PCT", "95"))

# Defense-in-depth note: these patterns are matched against container stdout/
# stderr, which is untrusted application output -- used only for substring/
# regex detection and truncated logging, never executed or interpolated
# into a shell command.
_PERMISSION_PATTERNS = re.compile(
    r"permission denied|read-only file system|MISCONF|EACCES|operation not permitted|"
    r"cannot open .*for (?:reading|writing|saving)",
    re.IGNORECASE,
)

# This host runs multiple unrelated Compose projects side by side (see
# project_shared_host_hermes / project_n04_shared_physical_host in memory) --
# every one of them carries the same com.docker.compose.project *label key*,
# just with a different *value*. Filtering on label presence alone silently
# pulls in every other project's containers too; only an exact value match
# scopes this monitor to moe-infra's own deployment.
_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_COMPOSE_PROJECT_NAME = os.environ.get("COMPOSE_PROJECT_NAME", "moe-infra")


def _run_docker(args: list[str]) -> str:
    """Run a docker CLI command and return stdout. Never raises on a nonzero
    exit -- a single container's inspect failing (e.g. it was removed
    between listing and inspecting) must not abort the whole pass."""
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            logger.debug("docker %s failed: %s", " ".join(args), result.stderr.strip()[:300])
            return ""
        return result.stdout
    except Exception as exc:
        logger.warning("docker %s errored: %s", " ".join(args), exc)
        return ""


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"last_alert_ts": {}, "restart_counts": {}}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        logger.warning("Failed writing state file %s: %s", STATE_FILE, exc)


def _alert(state: dict[str, Any], key: str, severity: str, message: str) -> Optional[dict[str, Any]]:
    """Emit one alert if its cooldown has elapsed. Returns the alert record
    (for --json summaries) whether or not it was actually (re-)logged, so a
    single-pass caller can still see the full current problem list."""
    now = time.time()
    record = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "key": key,
        "severity": severity,
        "message": message,
    }
    last = state["last_alert_ts"].get(key, 0)
    if now - last < ALERT_COOLDOWN_S:
        return record
    state["last_alert_ts"][key] = now
    logger.warning("ALERT[%s] %s: %s", severity.upper(), key, message)
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed writing alert log %s: %s", ALERT_LOG, exc)
    return record


def check_permissions(state: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    """Category 1: scan each running container's recent logs for a
    permission/ownership failure signature."""
    findings = []
    for name in names:
        logs = _run_docker(["logs", "--since", f"{LOG_LOOKBACK_S}s", name])
        match = _PERMISSION_PATTERNS.search(logs)
        if match:
            snippet = logs[max(0, match.start() - 40):match.end() + 80].replace("\n", " ").strip()
            rec = _alert(
                state, f"perm:{name}", "critical",
                f"container '{name}' logged a permission/ownership error in the last {LOG_LOOKBACK_S}s: {snippet}",
            )
            if rec:
                findings.append(rec)
    return findings


def check_container_health(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Category 2: unhealthy containers and active (not merely historical)
    restart-count deltas, across the whole compose project."""
    findings = []
    ids_raw = _run_docker(["ps", "-a", "-q"])
    ids = [i for i in ids_raw.splitlines() if i.strip()]
    if not ids:
        return findings

    inspected = _run_docker(["inspect", *ids])
    try:
        containers = json.loads(inspected) if inspected else []
    except json.JSONDecodeError:
        logger.warning("Failed parsing `docker inspect` output -- skipping health check this pass")
        return findings

    restart_counts = state.setdefault("restart_counts", {})
    for c in containers:
        name = (c.get("Name") or "").lstrip("/")
        labels = (c.get("Config", {}) or {}).get("Labels", {}) or {}
        if labels.get(_COMPOSE_PROJECT_LABEL) != _COMPOSE_PROJECT_NAME:
            continue  # a different Compose project on this shared host -- not ours to monitor
        c_state = c.get("State", {}) or {}
        status = c_state.get("Status")
        health = (c_state.get("Health") or {}).get("Status")
        restart_count = c.get("RestartCount", 0)

        if health == "unhealthy":
            rec = _alert(state, f"unhealthy:{name}", "critical", f"container '{name}' is unhealthy (status={status})")
            if rec:
                findings.append(rec)
        if status == "restarting":
            rec = _alert(state, f"restarting:{name}", "critical", f"container '{name}' is currently mid-restart (possible crash loop)")
            if rec:
                findings.append(rec)

        prev = restart_counts.get(name)
        restart_counts[name] = restart_count
        if prev is not None and restart_count > prev:
            rec = _alert(
                state, f"restart_delta:{name}", "warning",
                f"container '{name}' RestartCount increased {prev} -> {restart_count} since the last check "
                f"(active crash-looping, not just historical restarts)",
            )
            if rec:
                findings.append(rec)
    return findings


def check_disk_space(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Category 3: local disk space where the deployment's bind-mounted
    volumes (Postgres/Valkey/Neo4j/Chroma/Kafka data) actually live."""
    findings = []
    paths = {"/": Path("/")}
    data_root = os.environ.get("MOE_DATA_ROOT")
    if data_root and Path(data_root).exists():
        paths[data_root] = Path(data_root)

    for label, path in paths.items():
        try:
            usage = shutil.disk_usage(path)
        except Exception as exc:
            logger.debug("disk_usage(%s) failed: %s", path, exc)
            continue
        pct_used = 100.0 * usage.used / usage.total
        free_gb = usage.free / 1e9
        if pct_used >= DISK_CRIT_PCT:
            rec = _alert(
                state, f"disk_crit:{label}", "critical",
                f"disk usage at '{label}' is {pct_used:.1f}% ({free_gb:.1f} GB free) -- at/above critical threshold {DISK_CRIT_PCT}%",
            )
            if rec:
                findings.append(rec)
        elif pct_used >= DISK_WARN_PCT:
            rec = _alert(
                state, f"disk_warn:{label}", "warning",
                f"disk usage at '{label}' is {pct_used:.1f}% ({free_gb:.1f} GB free) -- at/above warning threshold {DISK_WARN_PCT}%",
            )
            if rec:
                findings.append(rec)
    return findings


def _read_meminfo(path: str = "/proc/meminfo") -> dict[str, int]:
    """Parses /proc/meminfo into {key: value_kb}. Portable across the minimal
    images/hosts this runs on without adding a psutil dependency."""
    out: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            key = parts[0].strip()
            value = parts[1].strip().split()[0]  # drop the trailing "kB"
            try:
                out[key] = int(value)
            except ValueError:
                continue
    return out


def check_memory(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Category 3b: host RAM/swap pressure. A shared PoC host can run low on
    memory well before any single container hits its own cgroup limit --
    that shows up here first, as a precursor to an eventual OOM-kill."""
    findings = []
    try:
        meminfo = _read_meminfo()
    except Exception as exc:
        logger.debug("Reading /proc/meminfo failed: %s", exc)
        return findings

    mem_total = meminfo.get("MemTotal", 0)
    mem_available = meminfo.get("MemAvailable", 0)
    if mem_total > 0:
        avail_pct = 100.0 * mem_available / mem_total
        if avail_pct <= MEM_AVAILABLE_WARN_PCT:
            rec = _alert(
                state, "mem_available_warn", "warning",
                f"available RAM is {avail_pct:.1f}% ({mem_available / 1e6:.1f} GB) of {mem_total / 1e6:.1f} GB total "
                f"-- at/below warning threshold {MEM_AVAILABLE_WARN_PCT}%",
            )
            if rec:
                findings.append(rec)

    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    if swap_total > 0:
        swap_used_pct = 100.0 * (swap_total - swap_free) / swap_total
        if swap_used_pct >= SWAP_CRIT_PCT:
            rec = _alert(
                state, "swap_crit", "critical",
                f"swap usage is {swap_used_pct:.1f}% of {swap_total / 1e6:.1f} GB -- at/above critical threshold "
                f"{SWAP_CRIT_PCT}% (OOM-kill risk rises sharply once swap is exhausted)",
            )
            if rec:
                findings.append(rec)
        elif swap_used_pct >= SWAP_WARN_PCT:
            rec = _alert(
                state, "swap_warn", "warning",
                f"swap usage is {swap_used_pct:.1f}% of {swap_total / 1e6:.1f} GB -- at/above warning threshold {SWAP_WARN_PCT}%",
            )
            if rec:
                findings.append(rec)
    return findings


def run_once() -> list[dict[str, Any]]:
    state = _load_state()
    # Scoped to this Compose project's own containers -- this host runs
    # several unrelated projects side by side (see check_container_health's
    # comment); scanning their logs would be both wasted work and out of
    # scope for a monitor that's specifically ours.
    running = [n for n in _run_docker([
        "ps", "--filter", f"label={_COMPOSE_PROJECT_LABEL}={_COMPOSE_PROJECT_NAME}", "--format", "{{.Names}}",
    ]).splitlines() if n.strip()]

    findings: list[dict[str, Any]] = []
    findings += check_permissions(state, running)
    findings += check_container_health(state)
    findings += check_disk_space(state)
    findings += check_memory(state)

    _save_state(state)
    return findings


def _acquire_lock() -> bool:
    """Best-effort single-instance guard so overlapping cron ticks never
    stack up. Found live 2026-09-01: under heavy host swap pressure, a
    single pass's sequential `docker logs`/`docker inspect` calls (each with
    its own subprocess timeout) can outlast the 5-minute cron interval,
    letting the next tick start before the previous one finished -- 12
    consecutive ticks produced no log output at all that night, most likely
    piled up behind each other instead of running one at a time. A stale
    lock (recorded PID no longer alive) is treated as free, so a killed
    process can never wedge this shut permanently."""
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOCK_FILE.exists():
            try:
                old_pid = int(LOCK_FILE.read_text().strip())
                os.kill(old_pid, 0)  # no exception => that PID is alive
                return False  # another instance is genuinely still running
            except ProcessLookupError:
                pass  # PID no longer exists -- lock is stale, proceed
            except PermissionError:
                return False  # PID exists (owned by another user) -- still running
            except (ValueError, OSError):
                pass  # unparseable lock content -- treat as stale, proceed
        LOCK_FILE.write_text(str(os.getpid()))
        return True
    except Exception as exc:
        logger.debug("Lock acquisition failed (proceeding anyway): %s", exc)
        return True


def _release_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception as exc:
        logger.debug("Lock release failed (next run's stale-PID check will clear it): %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", action="store_true", help="Run continuously instead of a single pass.")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between passes in --loop mode (default 300).")
    parser.add_argument("--json", action="store_true", help="Print this pass's findings as JSON to stdout (still subject to alert-log cooldown).")
    args = parser.parse_args()

    def _pass() -> list[dict[str, Any]]:
        findings = run_once()
        if args.json:
            print(json.dumps(findings, ensure_ascii=False))
        elif not findings:
            logger.info("OK -- no permission/health/disk issues detected.")
        return findings

    if not _acquire_lock():
        logger.debug("Another validity_monitor instance is still running -- skipping this tick.")
        return 0
    try:
        if not args.loop:
            _pass()
            return 0

        logger.info("Starting validity monitor loop (interval=%ds, alert log=%s)", args.interval, ALERT_LOG)
        while True:
            try:
                _pass()
            except Exception:
                logger.exception("Unhandled error during a monitor pass -- continuing loop")
            time.sleep(args.interval)
    finally:
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())

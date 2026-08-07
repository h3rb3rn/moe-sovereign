#!/usr/bin/env python3
"""Bounded scheduler for MoE learning and graph-maintenance jobs.

The scheduler isolates every job in a subprocess, applies a timeout, records a
heartbeat for container health, and keeps destructive operations opt-in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


logging.basicConfig(level=logging.INFO, format="%(asctime)s [maintenance] %(message)s")
logger = logging.getLogger("moe-maintenance")

ROOT = Path(__file__).resolve().parent.parent
STATUS_FILE = Path(
    os.getenv("MAINTENANCE_STATUS_FILE", "/app/logs/maintenance-status.json")
)


def _enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _seconds(name: str, default: int, minimum: int = 60) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %d seconds", name, default)
        return default


@dataclass(frozen=True)
class Job:
    name: str
    script: Path
    interval: int
    timeout: int
    environment: dict[str, str]


def configured_jobs() -> list[Job]:
    jobs: list[Job] = []
    if _enabled("HABE_SCHEDULER_ENABLED", "1"):
        jobs.append(Job(
            "habe_rebuild",
            ROOT / "scripts" / "cron_habe_rebuild.py",
            _seconds("HABE_REBUILD_INTERVAL_SECONDS", 86400),
            _seconds("HABE_REBUILD_TIMEOUT_SECONDS", 1800),
            {},
        ))
    if _enabled("GRAPH_DECAY_SCHEDULER_ENABLED", "1"):
        jobs.append(Job(
            "graph_decay",
            ROOT / "scripts" / "graph_decay.py",
            _seconds("GRAPH_DECAY_INTERVAL_SECONDS", 86400),
            _seconds("GRAPH_DECAY_TIMEOUT_SECONDS", 900),
            {"DRY_RUN": "0" if _enabled("GRAPH_DECAY_APPLY", "0") else "1"},
        ))
    if _enabled("EURISKO_SCHEDULER_ENABLED", "0"):
        jobs.append(Job(
            "eurisko_optimizer",
            ROOT / "scripts" / "eurisko_template_optimizer.py",
            _seconds("EURISKO_INTERVAL_SECONDS", 21600),
            _seconds("EURISKO_TIMEOUT_SECONDS", 1800),
            {},
        ))
    return jobs


def _write_status(status: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    status["heartbeat"] = time.time()
    temp = STATUS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(status, sort_keys=True), encoding="utf-8")
    os.replace(temp, STATUS_FILE)


async def run_job(job: Job) -> dict:
    started = time.time()
    env = os.environ.copy()
    env.update(job.environment)
    logger.info("starting %s", job.name)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        str(job.script),
        cwd=str(ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=job.timeout)
    except asyncio.TimeoutError:
        proc.kill()
        output, _ = await proc.communicate()
        logger.error("%s timed out after %ds", job.name, job.timeout)
        return {
            "ok": False,
            "exit_code": None,
            "error": "timeout",
            "finished_at": time.time(),
            "duration_seconds": round(time.time() - started, 3),
            "output_tail": output.decode(errors="replace")[-2000:],
        }

    text = output.decode(errors="replace")
    if text:
        logger.info("%s output:\n%s", job.name, text[-4000:])
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "finished_at": time.time(),
        "duration_seconds": round(time.time() - started, 3),
        "output_tail": text[-2000:],
    }


async def main() -> None:
    jobs = configured_jobs()
    initial_delay = _seconds("MAINTENANCE_INITIAL_DELAY_SECONDS", 30, minimum=0)
    status: dict = {
        "scheduler": "running",
        "configured_jobs": [job.name for job in jobs],
        "jobs": {},
    }
    _write_status(status)
    logger.info(
        "configured jobs=%s initial_delay=%ds",
        [job.name for job in jobs],
        initial_delay,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    due = {job.name: time.monotonic() + initial_delay for job in jobs}
    while not stop.is_set():
        now = time.monotonic()
        for job in jobs:
            if now < due[job.name]:
                continue
            status["jobs"][job.name] = await run_job(job)
            due[job.name] = time.monotonic() + job.interval
            _write_status(status)
        _write_status(status)
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass

    status["scheduler"] = "stopped"
    _write_status(status)


if __name__ == "__main__":
    asyncio.run(main())

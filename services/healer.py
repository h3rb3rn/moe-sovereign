"""
services/healer.py — Dedicated ontology gap-healer status helpers.

Contains the Redis status helper, the stdout-streaming coroutine, and the
auto-restart logic for the persistent gap-healer subprocess.

Callers:
  - routes/admin_ontology.py (start/stop/status/verify handlers)
  - main.py (_watchdog_dedicated_healer, _auto_resume_dedicated_healer)
"""

import asyncio
import json
import logging
import os
import time
import uuid

import state

logger = logging.getLogger("MOE-SOVEREIGN")

_DEDICATED_HEALER_KEY       = "moe:ontology:dedicated"
_ONTOLOGY_RUNS_HISTORY_KEY  = "moe:maintenance:ontology:runs"
_HEALER_STALL_SECONDS       = 300   # 5 min without output → stalled
_HEALER_RESTART_DELAY_S     = 30    # pause between auto-restarts


async def _set_healer_status_dedicated(**fields) -> None:
    """Write arbitrary fields into the dedicated-healer Redis hash."""
    if state.redis_client is None:
        return
    try:
        await state.redis_client.hset(
            _DEDICATED_HEALER_KEY,
            mapping={k: str(v) for k, v in fields.items()},
        )
    except Exception:
        pass


async def _stream_dedicated_healer(
    proc: "asyncio.subprocess.Process",
    *,
    first_line: "bytes | None" = None,
) -> None:
    """Read stdout from the dedicated healer loop and update Redis counters.

    first_line: optional pre-read line from the early-exit detection in the
    start endpoint — passed in to avoid dropping the first output token.
    Writes a history entry to moe:maintenance:ontology:runs on completion.
    """
    stats: dict = {"processed": 0, "written": 0, "failed": 0}
    assert proc.stdout is not None
    start_ts = time.time()

    async def _handle(text: str) -> None:
        if "✓" in text and "→" in text:
            stats["written"] += 1
        elif "?" in text and "→" in text:
            stats["processed"] += 1
        elif "✗" in text:
            stats["failed"] += 1
        await _set_healer_status_dedicated(last_activity_ts=str(time.time()), **stats)

    try:
        if first_line:
            await _handle(first_line.decode(errors="replace"))
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            await _handle(line.decode(errors="replace"))
        rc = await proc.wait()
    except Exception:
        rc = -1

    if state.redis_client is not None:
        try:
            cur = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
            await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "stopped", "exit_code": str(rc),
            })
            template = (cur or {}).get("template", "")
            entry = json.dumps({
                "run_id":      str(uuid.uuid4()),
                "type":        "dedicated",
                "template":    template,
                "started_at":  float((cur or {}).get("started_at", start_ts)),
                "finished_at": time.time(),
                "exit_code":   rc,
                **stats,
            })
            await state.redis_client.lpush(_ONTOLOGY_RUNS_HISTORY_KEY, entry)
            await state.redis_client.ltrim(_ONTOLOGY_RUNS_HISTORY_KEY, 0, 99)
        except Exception:
            cur = {}

    await _dedicated_healer_auto_restart_if_needed(
        template_hint=(cur or {}).get("template", "") if state.redis_client else ""
    )


async def _dedicated_healer_auto_restart_if_needed(template_hint: str = "") -> None:
    """Re-spawn the dedicated healer if auto_restart=1 is set in Redis.

    Called after a subprocess exits (clean or error). Waits HEALER_RESTART_DELAY_S
    before spawning so rapid crash-loops don't thrash the system.
    The restart lock ensures only one concurrent restart attempt runs at a time.
    """
    if state._dedicated_healer_restart_lock.locked():
        return
    async with state._dedicated_healer_restart_lock:
        if state.redis_client is None:
            return
        try:
            cur = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
        except Exception:
            return
        if (cur or {}).get("auto_restart") != "1":
            return

        template = (cur or {}).get("template", "") or template_hint
        if not template:
            return

        logger.info("🔄 Dedicated healer exited — auto-restart in %ds (template=%s)",
                    _HEALER_RESTART_DELAY_S, template)
        await asyncio.sleep(_HEALER_RESTART_DELAY_S)

        try:
            cur2 = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
            if (cur2 or {}).get("auto_restart") != "1":
                return
        except Exception:
            return

        env = os.environ.copy()
        env["TEMPLATE_POOL"] = template
        env.setdefault("REQUEST_TIMEOUT", "900")
        env.setdefault("MOE_API_BASE", "http://localhost:8000")
        sys_key = (os.environ.get("SYSTEM_API_KEY", "")
                   or os.environ.get("MOE_API_KEY", "")).strip()
        if sys_key:
            env["MOE_API_KEY"] = sys_key
        try:
            new_proc = await asyncio.create_subprocess_exec(
                "python3", "/app/scripts/gap_healer_templates.py",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            state._dedicated_healer_proc = new_proc
            await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "running", "stalled": "0",
                "pid": str(new_proc.pid),
                "started_at": str(time.time()),
                "last_activity_ts": str(time.time()),
                "processed": "0", "written": "0", "failed": "0",
                "auto_restart": "1",
            })
            asyncio.create_task(_stream_dedicated_healer(new_proc))
            logger.info("✅ Dedicated healer auto-restarted — PID %s", new_proc.pid)
        except Exception as exc:
            logger.error("❌ Dedicated healer auto-restart failed: %s", exc)

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
_ONTOLOGY_RUN_KEY           = "moe:maintenance:ontology:run"
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


# ─── Ad-hoc one-shot healer + dedicated lifecycle handlers ──────────────────


async def _set_healer_status(**fields) -> None:
    if state.redis_client is None:
        return
    try:
        await state.redis_client.hset(_ONTOLOGY_RUN_KEY, mapping={k: str(v) for k, v in fields.items()})
        await state.redis_client.expire(_ONTOLOGY_RUN_KEY, 86400)
    except Exception:
        pass


async def _run_healer_task(concurrency: int, batch_size: int, run_id: str) -> None:
    """Spawn gap_healer_templates.py --once in the orchestrator container."""
    import time as _t
    import uuid as _uuid
    start = _t.time()
    # Clear stale fields from previous runs first.
    if state.redis_client is not None:
        try:
            await state.redis_client.delete(_ONTOLOGY_RUN_KEY)
        except Exception:
            pass
    await _set_healer_status(
        status="running", run_id=run_id, started_at=str(start),
        concurrency=concurrency, batch_size=batch_size,
        processed=0, written=0, failed=0, message="",
    )
    env = os.environ.copy()
    env["CONCURRENCY"] = str(concurrency)
    env["BATCH_SIZE"] = str(batch_size)
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    # The healer calls /v1/chat/completions which requires a valid Bearer.
    # Use the SYSTEM_API_KEY (installed with an active api_keys row).
    sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
    if sys_key:
        env["MOE_API_KEY"] = sys_key
    proc = await asyncio.create_subprocess_exec(
        "python3", "/app/scripts/gap_healer_templates.py", "--once",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stats = {"processed": 0, "written": 0, "failed": 0}
    assert proc.stdout is not None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            if "✓" in text and "→" in text:
                stats["written"] += 1
            elif "?" in text and "→" in text:
                stats["processed"] += 1
            elif "✗" in text:
                stats["failed"] += 1
            if any(stats.values()):
                await _set_healer_status(status="running", **stats)
        rc = await proc.wait()
    except Exception as e:
        await _set_healer_status(status="failed", message=str(e)[:200])
        return
    final = "ready" if rc == 0 else "failed"
    await _set_healer_status(
        status=final, run_id=run_id, finished_at=str(_t.time()),
        exit_code=rc, **stats,
    )
    if state.redis_client is not None:
        try:
            entry = json.dumps({
                "run_id": run_id, "type": "oneshot", "template": "",
                "started_at": start, "finished_at": _t.time(),
                "exit_code": rc, **stats,
            })
            await state.redis_client.lpush(_ONTOLOGY_RUNS_HISTORY_KEY, entry)
            await state.redis_client.ltrim(_ONTOLOGY_RUNS_HISTORY_KEY, 0, 99)
        except Exception:
            pass


async def _auto_resume_dedicated_healer() -> None:
    """Resume the dedicated healer after a container restart if Redis shows it was running.

    Called as a background task during startup. A short sleep lets the ASGI
    server finish binding before the healer subprocess sends its first request
    to the local API.
    """
    # state._dedicated_healer_proc is in state — no global needed
    import time as _t
    await asyncio.sleep(5)  # wait for ASGI server to start serving

    if state.redis_client is None:
        return
    try:
        data = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
    except Exception:
        return

    if not data or data.get("status") != "running":
        return

    template = data.get("template", "").strip()
    if not template:
        return

    # Confirm the previous PID is dead — if still alive, no restart needed.
    pid = int(data.get("pid", 0))
    if pid:
        try:
            os.kill(pid, 0)
            logger.info("🔄 Dedicated healer PID %s still alive — skipping auto-resume", pid)
            return
        except OSError:
            pass  # process dead; container was restarted

    logger.info("🔄 Auto-resuming dedicated healer (template=%s, prev_pid=%s)", template, pid)

    # Clean stale fields before resuming so the watchdog gets a fresh baseline.
    try:
        await state.redis_client.delete(_DEDICATED_HEALER_KEY)
        await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
            "status": "starting",
            "template": template,
            "processed": "0",
            "written": "0",
            "failed": "0",
            "stalled": "0",
            "started_at": str(_t.time()),
            "auto_restart": "1",
        })
    except Exception:
        pass

    env = os.environ.copy()
    env["TEMPLATE_POOL"] = template
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    sys_key = (os.environ.get("SYSTEM_API_KEY", "") or os.environ.get("MOE_API_KEY", "")).strip()
    if sys_key:
        env["MOE_API_KEY"] = sys_key

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "/app/scripts/gap_healer_templates.py",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        state._dedicated_healer_proc = proc
        await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"pid": str(proc.pid)})

        # Early-exit check: if the process dies within 3 s, mark as stopped.
        first_line: "bytes | None" = None
        try:
            fl_task = asyncio.create_task(proc.stdout.readline())
            wt_task = asyncio.create_task(proc.wait())
            done, pending = await asyncio.wait({fl_task, wt_task}, timeout=3.0,
                                               return_when=asyncio.FIRST_COMPLETED)
            if wt_task in done:
                rc = wt_task.result()
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                    "status": "stopped", "exit_code": str(rc),
                })
                state._dedicated_healer_proc = None
                logger.error("❌ Dedicated healer exited immediately on resume (rc=%s)", rc)
                return
            wt_task.cancel()
            try:
                await wt_task
            except asyncio.CancelledError:
                pass
            if fl_task in done:
                first_line = fl_task.result() or None
            else:
                fl_task.cancel()
                try:
                    await fl_task
                except asyncio.CancelledError:
                    pass
        except Exception:
            pass

        await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "running"})
        asyncio.create_task(_stream_dedicated_healer(proc, first_line=first_line))
        logger.info("✅ Dedicated healer auto-resumed — PID %s (template=%s)", proc.pid, template)
    except Exception as e:
        logger.error("❌ Failed to auto-resume dedicated healer: %s", e)
        try:
            await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "stopped"})
        except Exception:
            pass


_HEALER_STALL_SECONDS = 300  # 5 minutes without output → stalled
_HEALER_RESTART_DELAY_S = 30  # pause between auto-restarts


async def _watchdog_dedicated_healer() -> None:
    """Periodic watchdog: escalate or auto-restart a stalled dedicated healer.

    Runs every 60 s. If the healer reports 'running' in Redis but has not
    produced any stdout output for HEALER_STALL_SECONDS, it is marked 'stalled'
    and the subprocess is restarted automatically.
    """
    import time as _t
    # state._dedicated_healer_proc is in state — no global needed
    while True:
        await asyncio.sleep(60)
        if state.redis_client is None:
            continue
        try:
            data = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
        except Exception:
            continue

        if not data or data.get("status") not in ("running", "stalled"):
            continue

        # PID liveness check — catches clean exits that didn't update status.
        pid = int(data.get("pid", 0))
        pid_alive = False
        if pid:
            try:
                os.kill(pid, 0)
                pid_alive = True
            except OSError:
                pass
        if not pid_alive:
            logger.warning(
                "⚠️  Dedicated healer PID %d is dead but status=%s — triggering auto-restart.",
                pid, data.get("status", "?"),
            )
            asyncio.create_task(_dedicated_healer_auto_restart_if_needed(
                template_hint=data.get("template", ""),
            ))
            continue

        last_ts = float(data.get("last_activity_ts") or 0)
        age = _t.time() - last_ts if last_ts else float("inf")

        if age < _HEALER_STALL_SECONDS:
            # Remove stalled flag if activity resumed.
            if data.get("stalled") == "1":
                try:
                    await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"stalled": "0", "status": "running"})
                except Exception:
                    pass
            continue

        logger.warning(
            "⚠️  Dedicated healer stalled — no output for %.0f s (template=%s). Auto-restarting.",
            age, data.get("template", "?"),
        )

        # Mark as stalled so the UI can show a warning immediately.
        try:
            await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"stalled": "1", "status": "stalled"})
        except Exception:
            pass

        # Kill the stuck subprocess before restarting.
        proc = state._dedicated_healer_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Re-use the same template to restart.
        template = data.get("template", "").strip()
        if not template:
            continue
        env = os.environ.copy()
        env["TEMPLATE_POOL"] = template
        env.setdefault("REQUEST_TIMEOUT", "900")
        env.setdefault("MOE_API_BASE", "http://localhost:8000")
        sys_key = os.environ.get("SYSTEM_API_KEY", "").strip()
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
                "status": "running",
                "stalled": "0",
                "pid": str(new_proc.pid),
                "started_at": str(_t.time()),
                "last_activity_ts": str(_t.time()),
            })
            asyncio.create_task(_stream_dedicated_healer(new_proc))
            logger.info("✅ Dedicated healer auto-restarted after stall — PID %s", new_proc.pid)
        except Exception as exc:
            logger.error("❌ Dedicated healer restart failed: %s", exc)


# _set_healer_status_dedicated, _stream_dedicated_healer,
# _dedicated_healer_auto_restart_if_needed moved to services/healer.py
from services.healer import (
    _set_healer_status_dedicated,
    _stream_dedicated_healer,
    _dedicated_healer_auto_restart_if_needed,
    _DEDICATED_HEALER_KEY,
    _HEALER_STALL_SECONDS,
    _HEALER_RESTART_DELAY_S,
)



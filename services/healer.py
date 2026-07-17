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
_BLOCKED_SERVERS_KEY        = "moe:blocked_servers"
_NODE_ACTIVE_PREFIX         = "moe:healer:active:"  # scripts/gap_healer_templates.py's per-node slot counter
_HEALER_STALL_SECONDS       = 300   # 5 min without output → stalled
_HEALER_RESTART_DELAY_S     = 30    # pause between auto-restarts


def _node_names_for_template_pool(template_pool: str) -> list[str]:
    """Python mirror of gap_healer_templates.py::_node_name_from_template,
    for the comma-separated TEMPLATE_POOL string stored in the dedicated
    healer's Redis hash."""
    names = []
    for t in (template_pool or "").split(","):
        t = t.strip()
        if t:
            names.append(t.replace("moe-ontology-curator-", ""))
    return names


async def _clear_orphaned_node_slots(template_pool: str) -> None:
    """Reset the per-node concurrency slot counter(s) after forcibly killing
    the healer subprocess.

    gap_healer_templates.py's _try_acquire_node_slot()/_release_node_slot()
    coordinate concurrent LLM calls per node via a Redis INCR/DECR counter
    (moe:healer:active:{node}), released in a `finally:` block around each
    call. SIGTERM does not run that cleanup, so a kill mid-call leaves the
    counter incremented forever (TTL 3600s — far longer than the 300s stall
    window that triggers this kill). Once the counter exceeds the node's
    allowed-slots cap, every future acquire attempt fails immediately and
    the healer spins re-queueing gaps without ever calling the LLM again —
    observed in production as "model loaded, zero API calls, zero
    throughput" until manually cleared. Safe to reset unconditionally here:
    by the time this runs the owning process is confirmed dead, so nothing
    can legitimately be relying on that reservation anymore.
    """
    if state.redis_client is None:
        return
    for node in _node_names_for_template_pool(template_pool):
        try:
            await state.redis_client.set(_NODE_ACTIVE_PREFIX + node, 0)
        except Exception:
            pass


def _target_server_for_template(template: str) -> str:
    """Resolve the INFERENCE_SERVERS node name a healer template pins to.

    Mirrors admin_ui/curator_provisioner.py's moe-ontology-curator-{node}
    naming, and falls back to the "model@node" suffix for raw model strings
    (both are valid TEMPLATE_POOL values, see start_dedicated_healer).
    """
    template = (template or "").strip()
    if template.startswith("moe-ontology-curator-"):
        return template[len("moe-ontology-curator-"):].upper()
    if "@" in template:
        return template.rsplit("@", 1)[1].strip()
    return ""


async def _set_server_blocked(server_name: str, blocked: bool) -> None:
    """Hard-block/unblock a server for regular routing (mirrors the
    /api/servers/{name}/block-toggle endpoint's Redis side, called here
    directly since the healer lifecycle owns this decision dynamically)."""
    if state.redis_client is None or not server_name:
        return
    try:
        if blocked:
            await state.redis_client.sadd(_BLOCKED_SERVERS_KEY, server_name)
        else:
            await state.redis_client.srem(_BLOCKED_SERVERS_KEY, server_name)
    except Exception:
        pass


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

    block_pref = False
    target_server = ""
    if state.redis_client is not None:
        try:
            init = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
            block_pref = (init or {}).get("block_server") == "1"
            target_server = _target_server_for_template((init or {}).get("template", ""))
        except Exception:
            pass

    async def _handle(text: str) -> None:
        if "✓" in text and "→" in text:
            stats["written"] += 1
        elif "?" in text and "→" in text:
            stats["processed"] += 1
        elif "✗" in text:
            stats["failed"] += 1
        await _set_healer_status_dedicated(last_activity_ts=str(time.time()), **stats)
        # Idle signal: the healer's gap queue is empty. Since scripts/
        # gap_healer_templates.py now idles in-process instead of exiting
        # (see IDLE_POLL_INTERVAL_S there), this can fire many times across
        # a single process's lifetime, not just once before exit — release
        # the node each time so idle windows are never left blocked.
        if block_pref and target_server and "No more gaps" in text:
            await _set_server_blocked(target_server, False)
        # Resume signal: mirrors the eager block-on-spawn logic in
        # start_dedicated_healer/_dedicated_healer_auto_restart_if_needed/
        # _auto_resume_dedicated_healer, but for the in-process idle→active
        # transition those don't cover (no new process is spawned when an
        # already-idling healer finds new eligible gaps).
        if block_pref and target_server and "claimed" in text and "gaps via ZPOPMAX" in text:
            await _set_server_blocked(target_server, True)

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

    # Safety net: always release the node when the subprocess actually exits
    # (crash, stall-kill, manual stop) even if no "No more gaps" line was seen.
    if block_pref and target_server:
        await _set_server_blocked(target_server, False)

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
        stale_pid = (cur or {}).get("pid", "")

        logger.info("🔄 Dedicated healer exited — auto-restart in %ds (template=%s)",
                    _HEALER_RESTART_DELAY_S, template)
        await asyncio.sleep(_HEALER_RESTART_DELAY_S)

        try:
            cur2 = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
            if (cur2 or {}).get("auto_restart") != "1":
                return
        except Exception:
            return
        # Someone else (manual stop/start while we were sleeping) already
        # replaced this run — the pid we were tracking is gone from Redis,
        # so a newer lifecycle already owns this key. Don't clobber its
        # (possibly different) template/block_server settings.
        if (cur2 or {}).get("pid", "") != stale_pid:
            return
        template = (cur2 or {}).get("template", "") or template
        block_pref = (cur2 or {}).get("block_server") == "1"

        # The exited process (whatever the reason) can no longer be holding
        # any per-node concurrency slot — release it so the fresh process
        # isn't blocked from ever calling the LLM by its predecessor's
        # orphaned reservation.
        await _clear_orphaned_node_slots(template)

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
                "auto_restart": "1", "block_server": "1" if block_pref else "0",
            })
            # A fresh restart means we're probing for new gaps again — re-block
            # the node for the duration of this attempt; it's released again
            # the moment "No more gaps" is seen or the process exits.
            if block_pref:
                await _set_server_blocked(_target_server_for_template(template), True)
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
    block_pref = data.get("block_server") == "1"

    # Confirm the previous PID is dead — if still alive, no restart needed.
    # A bare os.kill(pid, 0) is not enough: PIDs reset on every container
    # restart, so a low-numbered PID from the Redis hash (left over from
    # before the restart) can coincidentally match some unrelated, short-
    # lived process in the FRESH container's PID namespace — a false
    # positive that makes auto-resume skip forever, since nothing will ever
    # trigger a real exit event for a healer that never actually restarted.
    # Confirmed live: this happened on first deploy after this comment was
    # added, leaving the node stuck blocked with no healer running. Checking
    # the process's actual cmdline is a cheap, reliable disambiguator.
    pid = int(data.get("pid", 0))
    if pid:
        try:
            os.kill(pid, 0)
            with open(f"/proc/{pid}/cmdline", "rb") as _f:
                _cmdline = _f.read().decode(errors="replace")
            if "gap_healer_templates.py" in _cmdline:
                logger.info("🔄 Dedicated healer PID %s still alive — skipping auto-resume", pid)
                return
            logger.info(
                "🔄 Dedicated healer PID %s belongs to a different process "
                "(cmdline=%r) — PID reuse after restart, proceeding with auto-resume",
                pid, _cmdline[:100],
            )
        except (OSError, FileNotFoundError):
            pass  # process dead; container was restarted

    # The previous process is confirmed dead (container restart) — any
    # per-node slot it held is orphaned; see _clear_orphaned_node_slots.
    await _clear_orphaned_node_slots(template)

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
            "block_server": "1" if block_pref else "0",
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
        if block_pref:
            await _set_server_blocked(_target_server_for_template(template), True)
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

        # Kill the stuck subprocess. Do NOT respawn it here — the process's
        # own _stream_dedicated_healer background task (started when it was
        # originally spawned) is still awaiting its exit and will detect the
        # termination itself, then perform the actual respawn via the
        # lock-protected _dedicated_healer_auto_restart_if_needed(). Spawning
        # a second replacement from here as well would race that task: both
        # would write pid/template/block_server to the same Redis hash, and
        # whichever wrote last (typically the other one, ~30s later) wins —
        # in production this silently reset block_server back to unblocked
        # even though the operator had it enabled.
        proc = state._dedicated_healer_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


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



"""routes/admin_ontology.py — Ontology gap-healer endpoints.

Contains both the one-shot trigger/clear pair and the full dedicated-healer
lifecycle (start/stop/status/verify). All subprocess state lives in state.py
(_dedicated_healer_proc, _dedicated_healer_restart_lock).
"""

import asyncio
import json
import os
import signal
import time
import uuid

from fastapi import APIRouter

import state
from services.healer import (
    _DEDICATED_HEALER_KEY,
    _stream_dedicated_healer,
    _set_healer_status_dedicated,
)

router = APIRouter()

_ONTOLOGY_RUN_KEY = "moe:maintenance:ontology:run"


def _run_healer_task_ref():
    """Return main._run_healer_task via lazy import to avoid circular dependency.

    Python resolves the import from its module cache after the first call (O(1)),
    so there is no runtime overhead. Moving _run_healer_task out of main.py would
    require co-moving _set_healer_status and subprocess env logic — not worth it.
    """
    import main as _main
    return _main._run_healer_task


@router.post("/v1/admin/ontology/trigger")
async def trigger_ontology_healer(body: dict = None):
    """Kick off one gap-healer iteration in the background."""
    body = body or {}
    if state.redis_client is not None:
        try:
            cur = await state.redis_client.hgetall(_ONTOLOGY_RUN_KEY)
            if cur and cur.get("status") == "running":
                return {"ok": False, "reason": "already_running", "status": cur}
        except Exception:
            pass
    concurrency = max(1, min(32, int(body.get("concurrency") or 4)))
    batch_size  = max(1, min(200, int(body.get("batch_size") or 20)))
    run_id = uuid.uuid4().hex[:12]
    asyncio.create_task(_run_healer_task_ref()(concurrency, batch_size, run_id))
    return {"ok": True, "run_id": run_id}


@router.delete("/v1/admin/ontology/status")
async def clear_ontology_healer_status():
    """Delete the healer run status from Redis (dismiss failed/stale entries)."""
    if state.redis_client is None:
        return {"ok": False, "reason": "no_redis"}
    try:
        cur = await state.redis_client.hgetall(_ONTOLOGY_RUN_KEY)
        if cur and cur.get("status") == "running":
            return {"ok": False, "reason": "still_running"}
        await state.redis_client.delete(_ONTOLOGY_RUN_KEY)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:100]}


# ---------------------------------------------------------------------------
# Dedicated healer lifecycle (start / stop / status / verify)
# ---------------------------------------------------------------------------

@router.post("/v1/admin/ontology/dedicated/start")
async def start_dedicated_healer(body: dict = None):
    """Start a permanent gap-healer loop pinned to a single curator template."""
    body = body or {}
    template = (body.get("template") or "").strip()
    if not template:
        return {"ok": False, "reason": "template_required"}

    if state._dedicated_healer_proc is not None and state._dedicated_healer_proc.returncode is None:
        return {"ok": False, "reason": "already_running"}

    if state.redis_client is not None:
        try:
            cur = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
            if cur and cur.get("status") in ("running", "starting"):
                pid = int(cur.get("pid", 0))
                if pid:
                    try:
                        os.kill(pid, 0)
                        return {"ok": False, "reason": "already_running", "pid": pid}
                    except OSError:
                        pass
        except Exception:
            pass

    if state.redis_client is not None:
        try:
            await state.redis_client.delete(_DEDICATED_HEALER_KEY)
        except Exception:
            pass

    env = os.environ.copy()
    env["TEMPLATE_POOL"] = template
    env.setdefault("REQUEST_TIMEOUT", "900")
    env.setdefault("MOE_API_BASE", "http://localhost:8000")
    sys_key = (os.environ.get("SYSTEM_API_KEY", "")
               or os.environ.get("MOE_API_KEY", "")).strip()
    if sys_key:
        env["MOE_API_KEY"] = sys_key

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "/app/scripts/gap_healer_templates.py",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:
        return {"ok": False, "reason": "spawn_failed", "error": str(exc)[:200]}

    state._dedicated_healer_proc = proc

    if state.redis_client is not None:
        try:
            await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "starting", "template": template,
                "pid": str(proc.pid), "started_at": str(time.time()),
                "processed": "0", "written": "0", "failed": "0",
                "stalled": "0", "auto_restart": "1",
            })
        except Exception:
            pass

    first_line = None
    try:
        first_line_task = asyncio.create_task(proc.stdout.readline())
        wait_task = asyncio.create_task(proc.wait())
        done, pending = await asyncio.wait(
            {first_line_task, wait_task}, timeout=3.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if wait_task in done:
            rc = wait_task.result()
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            state._dedicated_healer_proc = None
            if state.redis_client is not None:
                try:
                    await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                        "status": "stopped", "exit_code": str(rc),
                    })
                except Exception:
                    pass
            return {"ok": False, "reason": "process_exited_immediately", "exit_code": rc}

        wait_task.cancel()
        try:
            await wait_task
        except asyncio.CancelledError:
            pass
        if first_line_task in done:
            first_line = first_line_task.result() or None
        else:
            first_line_task.cancel()
            try:
                await first_line_task
            except asyncio.CancelledError:
                pass
    except Exception:
        pass

    if state.redis_client is not None:
        try:
            await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={"status": "running"})
        except Exception:
            pass

    asyncio.create_task(_stream_dedicated_healer(proc, first_line=first_line))
    return {"ok": True, "pid": proc.pid, "template": template}


@router.post("/v1/admin/ontology/dedicated/stop")
async def stop_dedicated_healer():
    """Stop the running dedicated healer loop."""
    stopped = False
    if state._dedicated_healer_proc is not None and state._dedicated_healer_proc.returncode is None:
        try:
            state._dedicated_healer_proc.terminate()
            await asyncio.wait_for(state._dedicated_healer_proc.wait(), timeout=5.0)
        except Exception:
            try:
                state._dedicated_healer_proc.kill()
            except Exception:
                pass
        state._dedicated_healer_proc = None
        stopped = True

    template_name = ""
    if state.redis_client is not None:
        try:
            cur = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
            template_name = cur.get("template", "")
            pid = int(cur.get("pid", 0))
            if pid and not stopped:
                try:
                    os.kill(pid, signal.SIGTERM)
                    stopped = True
                except OSError:
                    pass
            await state.redis_client.delete(_DEDICATED_HEALER_KEY)
            await state.redis_client.hset(_DEDICATED_HEALER_KEY, mapping={
                "status": "stopped",
                "template": template_name,
                "auto_restart": "0",
            })
        except Exception:
            pass

    return {"ok": True, "stopped": stopped}


@router.get("/v1/admin/ontology/dedicated/status")
async def get_dedicated_healer_status():
    """Return the current state of the dedicated healer loop."""
    if state.redis_client is None:
        if state._dedicated_healer_proc is not None and state._dedicated_healer_proc.returncode is None:
            return {"status": "running", "pid": state._dedicated_healer_proc.pid}
        return {"status": "stopped"}
    try:
        data = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY)
        if not data:
            return {"status": "stopped"}
        if data.get("status") in ("running", "stalled"):
            pid = int(data.get("pid", 0))
            if pid:
                try:
                    os.kill(pid, 0)
                except OSError:
                    await state.redis_client.hset(
                        _DEDICATED_HEALER_KEY, mapping={"status": "stopped"}
                    )
                    data["status"] = "stopped"
        last_ts = float(data.get("last_activity_ts") or 0)
        age = round(time.time() - last_ts) if last_ts else None
        data["activity_age_seconds"] = str(age) if age is not None else ""
        data["stalled"] = data.get("stalled", "0")
        return dict(data)
    except Exception as e:
        return {"status": "unknown", "error": str(e)[:100]}


@router.get("/v1/admin/ontology/dedicated/verify")
async def verify_dedicated_healer():
    """Verify that the dedicated healer is genuinely running."""
    data: dict = {}
    if state.redis_client is not None:
        try:
            data = await state.redis_client.hgetall(_DEDICATED_HEALER_KEY) or {}
        except Exception:
            pass

    pid = int(data.get("pid", 0))
    pid_alive = False
    if pid:
        try:
            os.kill(pid, 0)
            pid_alive = True
        except OSError:
            pass
    if not pid_alive and state._dedicated_healer_proc is not None \
            and state._dedicated_healer_proc.returncode is None:
        pid_alive = True

    active_chat_id = None
    if state.redis_client is not None:
        try:
            async for key in state.redis_client.scan_iter("moe:active:*"):
                try:
                    raw = await state.redis_client.get(key)
                    if raw:
                        meta = json.loads(raw)
                        if (meta.get("model") or "").startswith("moe-ontology-curator"):
                            active_chat_id = meta.get("chat_id")
                            break
                except Exception:
                    continue
        except Exception:
            pass

    last_ts = float(data.get("last_activity_ts") or 0)
    activity_age = round(time.time() - last_ts) if last_ts else None
    is_active = active_chat_id is not None or (activity_age is not None and activity_age < 60)

    return {
        "pid_alive": pid_alive,
        "has_active_request": active_chat_id is not None,
        "is_active": is_active,
        "chat_id": active_chat_id,
        "status": data.get("status", "stopped"),
        "pid": pid,
        "activity_age_seconds": activity_age,
    }

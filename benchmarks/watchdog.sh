#!/usr/bin/env bash
# Overnight benchmark watchdog — autonomous restart & repair
# Runs in background, watches for benchmark process death, auto-restarts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
[[ -f "$SCRIPT_DIR/.env" ]] && set -a && source "$SCRIPT_DIR/.env" && set +a

LOCK_FILE="$SCRIPT_DIR/.bench_running"
HEARTBEAT_FILE="$SCRIPT_DIR/.bench_heartbeat"
WATCHDOG_LOG="${MOE_WATCHDOG_LOG:-/tmp/watchdog.log}"
BENCH_LOG="${MOE_BENCH_LOG:-/tmp/benchmark_run.log}"
MAX_RESTARTS="${MOE_WATCHDOG_MAX_RESTARTS:-5}"
RESTART_COUNT=0

_log() { echo "[$(date '+%H:%M:%S')] [watchdog] $*" | tee -a "$WATCHDOG_LOG"; }

STALE_HEARTBEAT_SECONDS="${MOE_WATCHDOG_STALE_SECONDS:-2400}"  # 40min: above every observed real single-call duration

_bench_pid() {
    # Never returns non-zero: under `set -e`, a bare `[[ cond ]] || return 1`
    # (or `&& return 0`) trips the WHOLE SCRIPT the moment this function is
    # called from anything other than a direct `if`/`while` condition -- which
    # is exactly what happened live (_kill_hung_benchmark calls this via a
    # plain assignment, not a condition; the script silently died right after
    # logging "process dead or hung" and never restarted). If the lock file is
    # missing, just produce no output instead of a nonzero return.
    if [[ -f "$LOCK_FILE" ]]; then
        python3 -c "import json,sys; d=json.load(open('$LOCK_FILE')); print(d.get('pid',''))" 2>/dev/null
    fi
    return 0
}

_bench_alive() {
    # A process that is technically running but stuck inside one HTTP call with
    # no server-side progress (observed live: a native-baseline call sat at 0%
    # GPU utilization for ~5h, well inside its own client timeout) is NOT
    # "alive" for watchdog purposes -- PID liveness alone cannot tell a hang
    # from real work. Require BOTH: PID alive AND heartbeat fresher than
    # STALE_HEARTBEAT_SECONDS. Heartbeat freshness alone (no lock file at all)
    # remains a valid fallback signal of life.
    #
    # Written entirely with `if`/`fi` blocks, not `[[ ]] && cmd` one-liners:
    # the latter trips `set -e` whenever the test is false and the statement
    # isn't itself a condition (see _bench_pid's comment for the live incident
    # this caused).
    local pid
    pid=$(_bench_pid)
    local pid_alive=1
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        pid_alive=0
    fi

    if [[ -f "$HEARTBEAT_FILE" ]]; then
        local age
        age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE") ))
        if [[ "$age" -lt "$STALE_HEARTBEAT_SECONDS" ]]; then
            if [[ "$pid_alive" -eq 0 ]]; then
                return 0
            fi
            # No lock file / no resolvable PID, but a fresh heartbeat -- still alive.
            if [[ ! -f "$LOCK_FILE" ]]; then
                return 0
            fi
            return 1
        fi
        _log "Heartbeat stale (${age}s >= ${STALE_HEARTBEAT_SECONDS}s) -- treating as hung even though PID is alive."
        return 1
    fi

    # No heartbeat file at all yet (very early startup) -- trust PID alone.
    if [[ "$pid_alive" -eq 0 ]]; then
        return 0
    fi
    return 1
}

_kill_hung_benchmark() {
    local pid
    pid=$(_bench_pid)
    if [[ -n "$pid" ]]; then
        if kill -0 "$pid" 2>/dev/null; then
            _log "Killing hung benchmark PID $pid (SIGTERM)..."
            kill "$pid" 2>/dev/null || true
            sleep 5
            if kill -0 "$pid" 2>/dev/null; then
                _log "PID $pid still alive after SIGTERM -- SIGKILL."
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
    fi
    return 0
}

_start_benchmark() {
    _log "Starting benchmark run #$((RESTART_COUNT + 1))..."
    cd "$SCRIPT_DIR/.."
    # Resume from checkpoint on restart (not --fresh) -- the checkpoint-validity fix in
    # run_scientific_benchmark.py (_result_is_valid) makes resume trustworthy again.
    nohup python3 -u benchmarks/run_scientific_benchmark.py >> "$BENCH_LOG" 2>&1 &
    local new_pid=$!
    _log "Benchmark PID: $new_pid"
    sleep 10
    if kill -0 "$new_pid" 2>/dev/null; then
        _log "Benchmark started successfully."
        return 0
    else
        _log "ERROR: Benchmark exited immediately after launch."
        return 1
    fi
}

_check_already_complete() {
    # run_scientific_benchmark.py writes knowledge_graph_impact_delta only in the final
    # payload (interim checkpoint writes during the run omit it) -- that key's presence is
    # the real completion signal for this script, not overnight_report.json (GAIA suite).
    #
    # Must also be newer than WATCHDOG_START_EPOCH: without that check, a leftover
    # latest_scientific_benchmark.json from a PRIOR (unrelated) completed run looks
    # identical to this run's own completion. Observed live: a fresh, filtered
    # single-task run was declared "complete" and the watchdog exited 30s after
    # starting -- purely because an earlier run's result file was still sitting
    # there with the same key -- leaving the actual run unsupervised for the rest
    # of its (failing) execution.
    local latest="$SCRIPT_DIR/results/latest_scientific_benchmark.json"
    if [[ ! -f "$latest" ]]; then
        return 1
    fi
    local mtime
    mtime=$(stat -c %Y "$latest" 2>/dev/null || echo 0)
    if [[ "$mtime" -le "$WATCHDOG_START_EPOCH" ]]; then
        return 1
    fi
    if python3 -c "import json,sys; sys.exit(0 if 'knowledge_graph_impact_delta' in json.load(open('$latest')) else 1)" 2>/dev/null; then
        _log "latest_scientific_benchmark.json has a final result from THIS run — benchmark complete!"
        return 0
    fi
    return 1
}

WATCHDOG_START_EPOCH=$(date +%s)
_log "Watchdog started. Monitoring benchmark process..."
_log "Max restarts: $MAX_RESTARTS"

# Wait for initial benchmark to be alive
sleep 30

while true; do
    if _check_already_complete; then
        _log "All done. Watchdog exiting."
        exit 0
    fi

    if ! _bench_alive; then
        _log "Benchmark process dead or hung (heartbeat stale or PID gone)."
        _kill_hung_benchmark

        if _check_already_complete; then
            _log "Report exists — clean completion. Exiting watchdog."
            exit 0
        fi

        if [[ "$RESTART_COUNT" -ge "$MAX_RESTARTS" ]]; then
            _log "FATAL: Max restarts ($MAX_RESTARTS) exceeded. Manual intervention required."
            exit 1
        fi

        _log "Attempting auto-restart ($((RESTART_COUNT + 1))/$MAX_RESTARTS)..."
        sleep 15  # brief cooldown before restart

        if _start_benchmark; then
            RESTART_COUNT=$((RESTART_COUNT + 1))
            _log "Restart $RESTART_COUNT successful. Resuming watch..."
        else
            _log "Restart failed — waiting 60s before retry..."
            sleep 60
        fi
    else
        # Alive — check every 60s
        sleep 60
    fi
done

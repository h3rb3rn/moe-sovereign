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

_bench_alive() {
    # Primary: PID check
    if [[ -f "$LOCK_FILE" ]]; then
        local pid
        pid=$(python3 -c "import json,sys; d=json.load(open('$LOCK_FILE')); print(d.get('pid',''))" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    # Fallback: heartbeat freshness (< 90s)
    if [[ -f "$HEARTBEAT_FILE" ]]; then
        local age
        age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE") ))
        if [[ "$age" -lt 90 ]]; then
            return 0
        fi
    fi
    return 1
}

_start_benchmark() {
    _log "Starting benchmark run #$((RESTART_COUNT + 1))..."
    cd "$SCRIPT_DIR/.."
    nohup bash benchmarks/run_overnight.sh >> "$BENCH_LOG" 2>&1 &
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
    # If all 10 epochs have result files and overnight_report.json exists → done
    local results_dir
    results_dir=$(ls -td "$SCRIPT_DIR/results"/overnight_* 2>/dev/null | head -1)
    if [[ -z "$results_dir" ]]; then return 1; fi
    if [[ -f "$results_dir/overnight_report.json" ]]; then
        _log "overnight_report.json found — benchmark complete!"
        return 0
    fi
    return 1
}

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
        _log "Benchmark process dead (heartbeat stale or PID gone)."

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

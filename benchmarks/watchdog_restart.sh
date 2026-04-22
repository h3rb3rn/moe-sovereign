#!/usr/bin/env bash
# Watchdog: monitors a RUNNING benchmark and restarts it only on crash.
# Does NOT auto-start — benchmarks must be triggered manually.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$SCRIPT_DIR/results/overnight_watchdog.log"
LOCK="$SCRIPT_DIR/.bench_running"
HEARTBEAT="$SCRIPT_DIR/.bench_heartbeat"

echo "[watchdog $(date +%H:%M:%S)] Prüfe Benchmark-Status..."

# No lock file → benchmark is not running. Do NOT start it automatically.
if [ ! -f "$LOCK" ]; then
    echo "[watchdog] Kein Lock-File — kein laufender Benchmark. Kein Auto-Start."
    # Clean up a stale tmux session if one somehow exists without a lock
    if tmux has-session -t benchmark 2>/dev/null; then
        echo "[watchdog] Verwaiste tmux-Session gefunden — wird bereinigt."
        tmux kill-session -t benchmark 2>/dev/null || true
    fi
    exit 0
fi

# Lock present → benchmark was started manually. Check if it's still alive.
if [ -f "$HEARTBEAT" ]; then
    heartbeat_age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT") ))
    echo "[watchdog] Heartbeat Alter: ${heartbeat_age}s"
    if [ "$heartbeat_age" -gt 120 ]; then
        echo "[watchdog] WARNUNG: Heartbeat ${heartbeat_age}s alt — Benchmark hängt?"
        pid=$(python3 -c "import json; d=json.load(open('$LOCK')); print(d['pid'])" 2>/dev/null || echo 0)
        if [ "$pid" -gt 0 ] && ! kill -0 "$pid" 2>/dev/null; then
            echo "[watchdog] PID $pid nicht mehr aktiv — starte abgestürzten Benchmark neu."
            rm -f "$LOCK" "$HEARTBEAT"
            tmux kill-session -t benchmark 2>/dev/null || true
            sleep 5
            tmux new-session -d -s benchmark -c "$BENCH_DIR" \
                "bash benchmarks/run_overnight.sh 2>&1 | tee -a $LOG"
            echo "[watchdog] Benchmark-Session neu gestartet."
        else
            echo "[watchdog] PID $pid noch aktiv — Benchmark läuft (langsame Antwort ok)."
        fi
    else
        echo "[watchdog] Heartbeat frisch — Benchmark läuft normal."
        python3 -c "import json; d=json.load(open('$LOCK')); print(f'  Run: {d[\"run_id\"]} Template: {d[\"template\"]} Epochen: {d[\"epochs\"]}')" 2>/dev/null || true
    fi
else
    echo "[watchdog] Lock aber kein Heartbeat — prüfe PID..."
    pid=$(python3 -c "import json; d=json.load(open('$LOCK')); print(d['pid'])" 2>/dev/null || echo 0)
    if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
        echo "[watchdog] PID $pid läuft — warte auf Heartbeat."
    else
        echo "[watchdog] PID $pid tot — starte abgestürzten Benchmark neu."
        rm -f "$LOCK" "$HEARTBEAT"
        tmux kill-session -t benchmark 2>/dev/null || true
        sleep 5
        tmux new-session -d -s benchmark -c "$BENCH_DIR" \
            "bash benchmarks/run_overnight.sh 2>&1 | tee -a $LOG"
    fi
fi

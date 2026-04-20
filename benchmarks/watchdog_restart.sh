#!/usr/bin/env bash
# Watchdog: prüft ob Benchmark läuft, startet neu bei Absturz
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$SCRIPT_DIR/results/overnight_watchdog.log"
LOCK="$SCRIPT_DIR/.bench_running"
HEARTBEAT="$SCRIPT_DIR/.bench_heartbeat"

echo "[watchdog $(date +%H:%M:%S)] Prüfe Benchmark-Status..."

# Lock-Datei vorhanden?
if [ ! -f "$LOCK" ]; then
    echo "[watchdog] Kein Lock-File — Benchmark nicht aktiv."
    
    # Läuft tmux-Session?
    if tmux has-session -t benchmark 2>/dev/null; then
        pane_output=$(tmux capture-pane -t benchmark -p 2>/dev/null | tail -3)
        echo "[watchdog] tmux-Session vorhanden. Output: $pane_output"
        # Session ist da aber kein Lock — vermutlich fertig oder gecrasht
        # Warte kurz und prüfe nochmals
        sleep 30
        if [ ! -f "$LOCK" ]; then
            echo "[watchdog] Immer noch kein Lock nach 30s — starte Benchmark neu."
            tmux kill-session -t benchmark 2>/dev/null || true
            sleep 5
            tmux new-session -d -s benchmark -c "$BENCH_DIR" \
                "bash benchmarks/run_overnight.sh 2>&1 | tee -a $LOG"
            echo "[watchdog] Benchmark-Session neu gestartet."
        fi
    else
        echo "[watchdog] Keine tmux-Session — starte Benchmark."
        tmux new-session -d -s benchmark -c "$BENCH_DIR" \
            "bash benchmarks/run_overnight.sh 2>&1 | tee -a $LOG"
        echo "[watchdog] Benchmark-Session gestartet."
    fi
    exit 0
fi

# Lock vorhanden — prüfe Heartbeat (sollte max 90s alt sein)
if [ -f "$HEARTBEAT" ]; then
    heartbeat_age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT") ))
    echo "[watchdog] Heartbeat Alter: ${heartbeat_age}s"
    if [ "$heartbeat_age" -gt 120 ]; then
        echo "[watchdog] WARNUNG: Heartbeat ${heartbeat_age}s alt — Benchmark hängt?"
        # Prüfe ob PID noch läuft
        pid=$(python3 -c "import json; d=json.load(open('$LOCK')); print(d['pid'])" 2>/dev/null || echo 0)
        if [ "$pid" -gt 0 ] && ! kill -0 "$pid" 2>/dev/null; then
            echo "[watchdog] PID $pid nicht mehr aktiv — starte neu."
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
        echo "[watchdog] PID $pid tot — starte neu."
        rm -f "$LOCK" "$HEARTBEAT"
        tmux kill-session -t benchmark 2>/dev/null || true
        sleep 5
        tmux new-session -d -s benchmark -c "$BENCH_DIR" \
            "bash benchmarks/run_overnight.sh 2>&1 | tee -a $LOG"
    fi
fi

#!/usr/bin/env bash
# health_check.sh -- Standalone, low-noise supervision for the overnight scientific benchmark.
#
# Runs independently of any chat session. Every CHECK_INTERVAL seconds it writes ONE compact
# line to overnight_status.log (cheap to tail/grep, not meant to be read live). It only ever
# writes to overnight_alert.log when something actually needs attention:
#   - the benchmark process is dead AND the watchdog also isn't bringing it back
#   - the invalid/fallback rate over the last WINDOW results exceeds INVALID_RATE_THRESHOLD
#   - the run finished (final summary written)
# This exists so a human or an LLM session can check status via one grep instead of a
# per-task chat notification -- see benchmarks/watchdog.sh for crash-restart itself.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
LATEST="$RESULTS_DIR/latest_scientific_benchmark.json"
LOCK="$SCRIPT_DIR/.bench_running"
HEARTBEAT="$SCRIPT_DIR/.bench_heartbeat"
STATUS_LOG="$RESULTS_DIR/overnight_status.log"
ALERT_LOG="$RESULTS_DIR/overnight_alert.log"

CHECK_INTERVAL="${MOE_HEALTH_INTERVAL:-300}"     # 5 min
WINDOW=10                                        # look at the last N results for invalid-rate
INVALID_RATE_THRESHOLD=0.7                       # alert if >70% of the last WINDOW are invalid
STALE_HEARTBEAT_ALERT_S="${MOE_HEALTH_STALE_S:-9000}"  # 2.5h: PoC hardware -- kept above watchdog.sh's own
                                                        # STALE_HEARTBEAT_SECONDS (7200s) so this only fires when
                                                        # the watchdog itself has failed to recover, not as a
                                                        # duplicate of its own restart threshold

ALERT_COOLDOWN_S=3600   # don't re-fire the same alert type more than once per hour
declare -A _last_alert_ts

_alert() {
    local alert_type="$1"; shift
    local now; now=$(date +%s)
    local last="${_last_alert_ts[$alert_type]:-0}"
    if (( now - last < ALERT_COOLDOWN_S )); then
        return 0
    fi
    _last_alert_ts[$alert_type]=$now
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALERT[$alert_type]: $*" | tee -a "$ALERT_LOG"
}

_already_completed() {
    [[ -f "$LATEST" ]] && python3 -c "
import json
d = json.load(open('$LATEST'))
exit(0 if 'knowledge_graph_impact_delta' in d else 1)
" 2>/dev/null
}

echo "[health_check] Started, checking every ${CHECK_INTERVAL}s -> $STATUS_LOG (alerts -> $ALERT_LOG)"

while true; do
    ts="$(date '+%Y-%m-%d %H:%M:%S')"

    if _already_completed; then
        _alert "completed" "Benchmark run completed (knowledge_graph_impact_delta present in latest_scientific_benchmark.json)."
        echo "[health_check] Run complete, exiting."
        exit 0
    fi

    # --- process liveness -----------------------------------------------------------
    proc_alive="unknown"
    if [[ -f "$LOCK" ]]; then
        pid=$(python3 -c "import json; print(json.load(open('$LOCK')).get('pid',''))" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            proc_alive="yes(pid=$pid)"
        else
            proc_alive="no(pid=$pid)"
        fi
    else
        proc_alive="no-lock-file"
    fi

    hb_age="n/a"
    if [[ -f "$HEARTBEAT" ]]; then
        hb_age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT") ))
    fi

    # --- progress + invalid-rate from the interim/latest result file ----------------
    stats=$(python3 -c "
import json
try:
    d = json.load(open('$LATEST'))
except Exception:
    print('n=0 valid=0 invalid=0 rate=0.0 last_window_invalid_rate=0.0')
    raise SystemExit
results = d.get('detailed_results', [])
n = len(results)
window = results[-$WINDOW:]
VALID = {'EXCELLENT','PASS','DEFICIENT','FAIL'}
def is_valid(r):
    if not r.get('total_tokens', 0) > 0: return False
    if r.get('judge_verdict') not in VALID: return False
    return all(t.get('ok', True) for t in r.get('turns', []))
valid = sum(1 for r in results if is_valid(r))
invalid = n - valid
w_invalid = sum(1 for r in window if not is_valid(r))
w_rate = round(w_invalid / len(window), 2) if window else 0.0
print(f'n={n} valid={valid} invalid={invalid} rate={round(invalid/n,2) if n else 0.0} last_window_invalid_rate={w_rate}')
" 2>/dev/null)

    echo "$ts | alive=$proc_alive | heartbeat_age=${hb_age}s | $stats" >> "$STATUS_LOG"

    # --- anomaly checks ---------------------------------------------------------------
    if [[ "$proc_alive" == no* ]] && [[ "$hb_age" != "n/a" ]] && [[ "$hb_age" -gt "$STALE_HEARTBEAT_ALERT_S" ]]; then
        _alert "process_dead" "Process dead and heartbeat stale for ${hb_age}s -- watchdog may not be recovering it. Stats: $stats"
    fi

    w_rate=$(echo "$stats" | grep -oP 'last_window_invalid_rate=\K[0-9.]+' || echo "0.0")
    n_done=$(echo "$stats" | grep -oP 'n=\K[0-9]+' || echo "0")
    if [[ "$n_done" -ge "$WINDOW" ]] && python3 -c "exit(0 if $w_rate > $INVALID_RATE_THRESHOLD else 1)" 2>/dev/null; then
        _alert "invalid_rate" "Invalid-rate over last $WINDOW results is $w_rate (> $INVALID_RATE_THRESHOLD threshold). Stats: $stats"
    fi

    sleep "$CHECK_INTERVAL"
done

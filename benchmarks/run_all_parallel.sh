#!/usr/bin/env bash
# run_all_parallel.sh — Run MoE-Eval against all benchmark templates simultaneously.
#
# Each template is pinned to different GPU nodes, so all cluster nodes are busy
# in parallel. After all runs complete, evaluator.py is invoked for every new
# result file.
#
# Usage (from moe-infra/ directory):
#   MOE_API_KEY=moe-sk-... bash benchmarks/run_all_parallel.sh
#
# Optional env vars:
#   MOE_API_BASE          (default: http://localhost:8002)
#   MOE_PARALLEL_TESTS    single_turn concurrency per runner (default: 3)
#   MOE_EVAL_JUDGE        template used by evaluator (default: moe-reference-30b-balanced)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
LOG_DIR="$RESULTS_DIR/parallel_run_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

API_BASE="${MOE_API_BASE:-http://localhost:8002}"
PARALLEL_TESTS="${MOE_PARALLEL_TESTS:-3}"

if [[ -z "${MOE_API_KEY:-}" ]]; then
    echo "ERROR: MOE_API_KEY is required." >&2
    exit 1
fi

# Templates to benchmark — one process per template → different GPU nodes busy in parallel
TEMPLATES=(
    "moe-reference-30b-balanced"
    "moe-benchmark-n04-rtx"
    "moe-benchmark-n07-n09"
    "moe-benchmark-n06-m10"
    "moe-benchmark-n11-m10"
)

echo "========================================================================"
echo "MoE-Eval Parallel Benchmark Run"
echo "  Templates:      ${#TEMPLATES[@]}"
echo "  Concurrency:    ${PARALLEL_TESTS} single_turn tests per runner"
echo "  API:            ${API_BASE}"
echo "  Log dir:        ${LOG_DIR}"
echo "  Started:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "========================================================================"

PIDS=()
RUN_LOGS=()

for tmpl in "${TEMPLATES[@]}"; do
    log="$LOG_DIR/run_${tmpl}.log"
    RUN_LOGS+=("$log")
    echo "[$(date +%H:%M:%S)] Starting runner for template: $tmpl  (log: $log)"
    MOE_API_KEY="$MOE_API_KEY" \
    MOE_API_BASE="$API_BASE" \
    MOE_TEMPLATE="$tmpl" \
    MOE_PARALLEL_TESTS="$PARALLEL_TESTS" \
    python3 "$SCRIPT_DIR/runner.py" > "$log" 2>&1 &
    PIDS+=($!)
done

echo ""
echo "--- Waiting for all ${#PIDS[@]} runner processes ---"
echo "  PIDs: ${PIDS[*]}"

FAILED=0
for i in "${!PIDS[@]}"; do
    pid="${PIDS[$i]}"
    tmpl="${TEMPLATES[$i]}"
    if wait "$pid"; then
        echo "[$(date +%H:%M:%S)] ✓  $tmpl (pid=$pid)"
    else
        echo "[$(date +%H:%M:%S)] ✗  $tmpl (pid=$pid) — exit code $?" >&2
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "========================================================================"
echo "All runners finished.  Failed: $FAILED / ${#PIDS[@]}"
echo "========================================================================"

# Show last 5 lines of each log (quick sanity check)
for i in "${!TEMPLATES[@]}"; do
    tmpl="${TEMPLATES[$i]}"
    log="${RUN_LOGS[$i]}"
    echo ""
    echo "--- $tmpl ---"
    tail -6 "$log" 2>/dev/null || echo "(log missing)"
done

# Run evaluator for every new run_*.json that was created during this session
echo ""
echo "========================================================================"
echo "Running evaluator on new result files..."
echo "========================================================================"

NEW_RUNS=()
for tmpl in "${TEMPLATES[@]}"; do
    # Pick up the latest run file for this template (just written)
    latest="$RESULTS_DIR/latest_${tmpl}.json"
    if [[ -f "$latest" ]]; then
        NEW_RUNS+=("$latest")
    fi
done

if [[ ${#NEW_RUNS[@]} -eq 0 ]]; then
    echo "No result files found — skipping evaluation."
else
    for run_file in "${NEW_RUNS[@]}"; do
        echo "[$(date +%H:%M:%S)] Evaluating: $run_file"
        MOE_API_KEY="$MOE_API_KEY" \
        MOE_API_BASE="$API_BASE" \
        MOE_EVAL_RESULTS="$run_file" \
        python3 "$SCRIPT_DIR/evaluator.py" >> "$LOG_DIR/eval.log" 2>&1 &
    done
    wait
    echo "Evaluation done. Combined log: $LOG_DIR/eval.log"
fi

echo ""
echo "Run logs:   $LOG_DIR/"
echo "Results:    $RESULTS_DIR/"
echo "Finished:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Inject results into documentation if evaluations completed
if [[ $FAILED -eq 0 ]]; then
    echo ""
    echo "========================================================================"
    echo "Injecting results into documentation..."
    echo "========================================================================"
    python3 "$SCRIPT_DIR/inject_results_into_docs.py" --run-dir "$LOG_DIR" \
        >> "$LOG_DIR/inject.log" 2>&1 && \
        echo "Documentation updated. See: $LOG_DIR/inject.log" || \
        echo "Documentation update failed. See: $LOG_DIR/inject.log"
fi

exit $FAILED

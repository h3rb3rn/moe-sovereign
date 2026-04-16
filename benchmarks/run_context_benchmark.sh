#!/usr/bin/env bash
# run_context_benchmark.sh — 2×2 matrix: compression ON/OFF × GraphRAG auto/fixed
# Uses the context-stress dataset (8–10 turn conversations) against AIHUB H200s.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/results/context_run_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

API_BASE="${MOE_API_BASE:-http://localhost:8002}"
DATASET="moe_eval_context_v1.json"

if [[ -z "${MOE_API_KEY:-}" ]]; then
    echo "ERROR: MOE_API_KEY is required." >&2
    exit 1
fi

TEMPLATES=(
    "moe-aihub-ctx-comp-auto"
    "moe-aihub-ctx-comp-fixed"
    "moe-aihub-ctx-nocomp-auto"
    "moe-aihub-ctx-nocomp-fixed"
)

echo "========================================================================"
echo "Context Window Abstraction Layer Benchmark"
echo "  Dataset:    $DATASET"
echo "  Templates:  ${#TEMPLATES[@]} (2×2 matrix)"
echo "  API:        $API_BASE"
echo "  Log dir:    $LOG_DIR"
echo "  Started:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "========================================================================"

SUMMARY="$LOG_DIR/summary.txt"
: > "$SUMMARY"

for tmpl in "${TEMPLATES[@]}"; do
    log="$LOG_DIR/run_${tmpl}.log"
    t0=$(date +%s)
    echo ""
    echo "[$(date +%H:%M:%S)] ▶ $tmpl"
    MOE_API_KEY="$MOE_API_KEY" \
    MOE_API_BASE="$API_BASE" \
    MOE_TEMPLATE="$tmpl" \
    MOE_EVAL_DATASET="$DATASET" \
    MOE_PARALLEL_TESTS="1" \
    python3 "$SCRIPT_DIR/runner.py" > "$log" 2>&1
    rc=$?
    t1=$(date +%s)
    dur=$((t1 - t0))
    echo "[$(date +%H:%M:%S)] ◀ $tmpl  rc=$rc  duration=${dur}s"
    printf "%-40s rc=%d  dur=%ds\n" "$tmpl" "$rc" "$dur" >> "$SUMMARY"
done

echo ""
echo "========================================================================"
echo "Context benchmark complete — summary:"
cat "$SUMMARY"
echo "========================================================================"

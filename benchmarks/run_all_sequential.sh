#!/usr/bin/env bash
# Run all benchmark templates sequentially — avoids N04-RTX GPU contention.
# AIHUB runs first (remote, no local GPU load), then local templates in order.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/results/all_run_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

API_BASE="${MOE_API_BASE:-http://localhost:8002}"
PARALLEL_TESTS="${MOE_PARALLEL_TESTS:-2}"

if [[ -z "${MOE_API_KEY:-}" ]]; then
    echo "ERROR: MOE_API_KEY is required." >&2
    exit 1
fi

TEMPLATES=(
    "moe-aihub-sovereign"
    "moe-reference-8b-fast"
    "moe-m10-8b-gremium"
    "moe-reference-30b-balanced"
    "moe-benchmark-n06-m10"
    "moe-benchmark-n11-m10"
    "moe-benchmark-n07-n09"
    "moe-benchmark-n04-rtx"
    "moe-m10-gremium-deep"
    "moe-reference-70b-deep"
)

echo "========================================================================"
echo "Sequential benchmark run — ${#TEMPLATES[@]} templates"
echo "Log dir: $LOG_DIR"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "========================================================================"

SUMMARY="$LOG_DIR/summary.txt"
: > "$SUMMARY"

for tmpl in "${TEMPLATES[@]}"; do
    log="$LOG_DIR/run_${tmpl}.log"
    t0=$(date +%s)
    echo ""
    echo "[$(date +%H:%M:%S)] ▶ $tmpl  → $log"
    MOE_API_KEY="$MOE_API_KEY" \
    MOE_API_BASE="$API_BASE" \
    MOE_TEMPLATE="$tmpl" \
    MOE_PARALLEL_TESTS="$PARALLEL_TESTS" \
    python3 "$SCRIPT_DIR/runner.py" > "$log" 2>&1
    rc=$?
    t1=$(date +%s)
    dur=$((t1 - t0))
    echo "[$(date +%H:%M:%S)] ◀ $tmpl  rc=$rc  duration=${dur}s"
    printf "%-40s rc=%d  dur=%ds\n" "$tmpl" "$rc" "$dur" >> "$SUMMARY"
done

echo ""
echo "========================================================================"
echo "All templates completed — summary:"
cat "$SUMMARY"
echo "========================================================================"

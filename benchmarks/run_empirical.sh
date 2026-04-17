#!/usr/bin/env bash
# run_empirical.sh — Empirical SLM study: ablation across 6 template configurations.
#
# Measures: context flooding threshold, graph compensation, mechanism leverage.
# Each template runs the same 7 tests (including 5/10/15/20 turn escalation).
#
# Usage:
#   MOE_API_KEY=moe-sk-... bash benchmarks/run_empirical.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATASET="moe_eval_empirical_v1.json"
API_BASE="${MOE_API_BASE:-http://localhost:8002}"
PROM_URL="${MOE_PROM_URL:-http://localhost:9090}"

RUN_ID="empirical_$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$SCRIPT_DIR/results/$RUN_ID"
mkdir -p "$RUN_DIR"

if [[ -z "${MOE_API_KEY:-}" ]]; then
    echo "ERROR: MOE_API_KEY is required." >&2
    exit 1
fi

# Ablation matrix — ordered from least to most mechanisms
TEMPLATES=(
    "moe-empirical-no-graph"       # Bare 8B LLMs — no graph, no cache, no web
    "moe-empirical-comp-on"        # + GraphRAG, compression ON (default)
    "moe-empirical-comp-off"       # + GraphRAG, compression OFF (unlimited history)
    "moe-empirical-graph-2k"       # + GraphRAG capped at 2000 chars
    "moe-empirical-graph-6k"       # + GraphRAG capped at 6000 chars
    "moe-empirical-full-stack"     # All mechanisms: graph + cache + web + corrections
)

echo "========================================================================"
echo "MoE Empirical SLM Study"
echo "  Run ID:     $RUN_ID"
echo "  Dataset:    $DATASET (7 tests incl. 5/10/15/20 turn escalation)"
echo "  Templates:  ${#TEMPLATES[@]} ablation configurations"
echo "  API:        $API_BASE"
echo "  Output:     $RUN_DIR"
echo "  Started:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "========================================================================"

SUMMARY="$RUN_DIR/summary.txt"
printf "%-35s %-10s %-10s\n" "Template" "Duration" "RC" > "$SUMMARY"

for tmpl in "${TEMPLATES[@]}"; do
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo "  Template: $tmpl — $(date +%H:%M:%S)"
    echo "════════════════════════════════════════════════════════════════"

    log="$RUN_DIR/run_${tmpl}.log"
    t0=$(date +%s)

    MOE_API_KEY="$MOE_API_KEY" \
    MOE_API_BASE="$API_BASE" \
    MOE_TEMPLATE="$tmpl" \
    MOE_EVAL_DATASET="$DATASET" \
    MOE_PARALLEL_TESTS="1" \
    python3 "$SCRIPT_DIR/runner.py" > "$log" 2>&1
    rc=$?

    t1=$(date +%s)
    dur=$((t1 - t0))

    echo "[$(date +%H:%M:%S)] $tmpl: rc=$rc duration=${dur}s"
    printf "%-35s %-10s %-10s\n" "$tmpl" "${dur}s" "$rc" >> "$SUMMARY"

    # Copy result file
    stable="$SCRIPT_DIR/results/latest_${tmpl}.json"
    if [ -f "$stable" ]; then
        cp "$stable" "$RUN_DIR/results_${tmpl}.json"
    fi

    # Run evaluator
    if [ -f "$RUN_DIR/results_${tmpl}.json" ]; then
        MOE_EVAL_RESULTS="$RUN_DIR/results_${tmpl}.json" \
        python3 "$SCRIPT_DIR/evaluator.py" > "$RUN_DIR/eval_${tmpl}.log" 2>&1 || true
        eval_latest="$SCRIPT_DIR/results/latest_eval.json"
        if [ -f "$eval_latest" ]; then
            cp "$eval_latest" "$RUN_DIR/eval_${tmpl}.json"
        fi
    fi

    # 30s pause for async ingest between templates
    sleep 30
done

echo ""
echo "========================================================================"
echo "Empirical study complete."
echo ""
cat "$SUMMARY"
echo ""
echo "Analyze: compare results_*.json across templates."
echo "Key metric: keyword retention rate in flood-5/10/15/20 tests."
echo "========================================================================"

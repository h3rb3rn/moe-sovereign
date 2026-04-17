#!/usr/bin/env bash
# run_rl_benchmark.sh — Reinforcement learning proof benchmark (5 epochs, ~30-60 min).
#
# Tests 5 focused cases per epoch across all nodes to prove that the MoE system
# measurably learns through:
#   - Cache hit rate growth (identical prompt → cache from epoch 2)
#   - GraphRAG accumulation (causal/synthesis nodes persist in Neo4j)
#   - Correction Memory (judge corrections stored and injected in later epochs)
#   - Thompson Sampling stability (routing confidence increases per domain)
#
# Configuration: reuses benchmarks/.env (same as run_overnight.sh).
#
# Usage:
#   bash benchmarks/run_rl_benchmark.sh
#   MOE_EPOCHS=3 bash benchmarks/run_rl_benchmark.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --------------------------------------------------------------------------
# Load .env
# --------------------------------------------------------------------------
ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "  Copy benchmarks/.env.example to benchmarks/.env and fill in your cluster config." >&2
    exit 1
fi
# shellcheck source=/dev/null
set -a; source "$ENV_FILE"; set +a

# --------------------------------------------------------------------------
# Runtime configuration — lean defaults for fast RL proof run
# --------------------------------------------------------------------------
EPOCHS="${MOE_EPOCHS:-5}"
TEMPLATE="${MOE_TEMPLATE:-moe-m10-gremium-deep}"
API_BASE="${MOE_API_BASE:-http://localhost:8002}"
PROM_URL="${MOE_PROM_URL:-http://localhost:9090}"
DATASET="moe_eval_rl_v1.json"
INGEST_PAUSE="${MOE_INGEST_PAUSE:-30}"     # Shorter than overnight (30s vs 60s)
EPOCH_MAX_RETRIES="${MOE_EPOCH_RETRIES:-2}" # Fewer retries — fail fast
FAILURE_THRESHOLD="${MOE_FAILURE_THRESHOLD:-3}"

# Timeouts scaled down for short tests (600s base, 1200s max)
BASE_TIMEOUT="${MOE_TIMEOUT:-600}"
MAX_TIMEOUT="${MOE_MAX_TIMEOUT:-1200}"

# --------------------------------------------------------------------------
# Inference node inventory (from .env)
# --------------------------------------------------------------------------
NODE_RTX_SSH="${BENCH_NODE_RTX_SSH:-}"
NODE_M10A_SSH="${BENCH_NODE_M10A_SSH:-}"
NODE_M10B_SSH="${BENCH_NODE_M10B_SSH:-}"
NODE_M60_SSH="${BENCH_NODE_M60_SSH:-}"
NODE_GT_SSH="${BENCH_NODE_GT_SSH:-}"

NODE_RTX_URL="${BENCH_NODE_RTX_URL:-}"
NODE_M10A_URL="${BENCH_NODE_M10A_URL:-}"
NODE_M10B_URL="${BENCH_NODE_M10B_URL:-}"
NODE_M60_URL="${BENCH_NODE_M60_URL:-}"
NODE_GT_URL="${BENCH_NODE_GT_URL:-}"

NODE_RTX_CONTAINERS="${BENCH_NODE_RTX_CONTAINERS:-ollama}"
NODE_M10A_CONTAINERS="${BENCH_NODE_M10A_CONTAINERS:-ollama}"
NODE_M10B_CONTAINERS="${BENCH_NODE_M10B_CONTAINERS:-ollama}"
NODE_M60_CONTAINERS="${BENCH_NODE_M60_CONTAINERS:-ollama}"
NODE_GT_CONTAINERS="${BENCH_NODE_GT_CONTAINERS:-ollama}"

JUDGE_OLLAMA_URL="${MOE_JUDGE_OLLAMA_URL:-http://localhost:11434}"

RUN_ID="rl_$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$SCRIPT_DIR/results/$RUN_ID"
mkdir -p "$RUN_DIR"

# --------------------------------------------------------------------------
# Admin UI lock file
# --------------------------------------------------------------------------
LOCK_FILE="$SCRIPT_DIR/.bench_running"
_write_lock() {
    python3 - <<PYEOF
import json, os
data = {
    "pid":        os.getpid(),
    "run_id":     "$RUN_ID",
    "template":   "$TEMPLATE",
    "epochs":     $EPOCHS,
    "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
with open("$LOCK_FILE", "w") as f:
    json.dump(data, f)
PYEOF
}
_clear_lock() { rm -f "$LOCK_FILE"; }
trap '_clear_lock' EXIT INT TERM
_write_lock

if [[ -z "${MOE_API_KEY:-}" ]]; then
    echo "ERROR: MOE_API_KEY is not set. Add it to benchmarks/.env." >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Prometheus metric snapshots (RL-focused subset)
# --------------------------------------------------------------------------
PROM_METRICS=(
    "moe_graph_entities_total"
    "moe_graph_relations_total"
    "sum(increase(moe_cache_hits_total[90d]))"
    "sum(increase(moe_cache_misses_total[90d]))"
    "sum(increase(moe_history_compressed_total[90d]))"
    "sum(increase(moe_corrections_stored_total[90d]))"
    "sum(increase(moe_corrections_injected_total[90d]))"
    "sum(increase(moe_synthesis_persisted_total[90d]))"
    "sum by (level) (increase(moe_expert_confidence_total[90d]))"
    "sum by (model) (increase(moe_tokens_total[90d]))"
    "histogram_quantile(0.50, rate(moe_response_duration_seconds_bucket[1h]))"
    "histogram_quantile(0.95, rate(moe_response_duration_seconds_bucket[1h]))"
)

snapshot_metrics() {
    local outfile="$1"
    echo "{" > "$outfile"
    echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"," >> "$outfile"
    echo "  \"metrics\": {" >> "$outfile"
    local first=true
    for query in "${PROM_METRICS[@]}"; do
        local encoded
        encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$query'))")
        local result
        result=$(curl -sf --max-time 10 "${PROM_URL}/api/v1/query?query=${encoded}" 2>/dev/null || echo '{}')
        if [ "$first" = true ]; then first=false; else echo "," >> "$outfile"; fi
        printf '    "%s": %s' "$query" "$result" >> "$outfile"
    done
    echo "" >> "$outfile"
    echo "  }" >> "$outfile"
    echo "}" >> "$outfile"
}

# --------------------------------------------------------------------------
# Node health & healing (identical to run_overnight.sh)
# --------------------------------------------------------------------------

check_ollama_http() {
    local url="$1"
    curl -sf --max-time 10 "${url}/api/tags" > /dev/null 2>&1
}

wait_ollama_http() {
    local url="$1"
    local max_wait="${2:-180}"
    local label="${3:-$url}"
    local deadline=$(( $(date +%s) + max_wait ))
    local attempt=0
    while [ "$(date +%s)" -lt "$deadline" ]; do
        attempt=$(( attempt + 1 ))
        if check_ollama_http "$url"; then
            [ "$attempt" -gt 1 ] && echo "    [heal] $label healthy after ${attempt} attempts"
            return 0
        fi
        echo "    [heal] $label not responding (attempt $attempt), retrying in 15s..."
        sleep 15
    done
    echo "    [heal] $label did not recover within ${max_wait}s"
    return 1
}

heal_node_ssh() {
    local ssh_host="$1"
    local ollama_url="$2"
    local containers="$3"
    local label="${4:-$ssh_host}"
    local max_wait="${5:-300}"
    echo "    [heal] Checking $label via SSH..."
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$ssh_host" "true" 2>/dev/null; then
        echo "    [heal] $label SSH unreachable"; return 1
    fi
    local unhealthy=false
    for container in $containers; do
        local state
        state=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$ssh_host" \
            "docker inspect --format='{{.State.Status}}' $container 2>/dev/null" 2>/dev/null || echo "unknown")
        echo "    [heal] $label/$container: $state"
        [ "$state" != "running" ] && unhealthy=true
    done
    if [ "$unhealthy" = false ] && ! check_ollama_http "$ollama_url"; then
        echo "    [heal] $label containers running but API unresponsive"; unhealthy=true
    fi
    if [ "$unhealthy" = true ]; then
        echo "    [heal] Restarting $label containers: $containers"
        ssh -o ConnectTimeout=10 -o BatchMode=yes "$ssh_host" \
            "docker restart $containers" >> "$RUN_DIR/heal.log" 2>&1 || return 1
        wait_ollama_http "$ollama_url" "$max_wait" "$label"
        return $?
    fi
    echo "    [heal] $label healthy — no action needed"; return 0
}

diagnose_and_heal() {
    echo "[$(date +%H:%M:%S)] ── Node diagnostic & healing pass ──"
    [ -n "$NODE_RTX_SSH"  ] && [ -n "$NODE_RTX_URL"  ] && \
        heal_node_ssh "$NODE_RTX_SSH"  "$NODE_RTX_URL"  "$NODE_RTX_CONTAINERS"  "$NODE_RTX_SSH"  300 || true
    [ -n "$NODE_M10A_SSH" ] && [ -n "$NODE_M10A_URL" ] && \
        heal_node_ssh "$NODE_M10A_SSH" "$NODE_M10A_URL" "$NODE_M10A_CONTAINERS" "$NODE_M10A_SSH" 180 || true
    [ -n "$NODE_M10B_SSH" ] && [ -n "$NODE_M10B_URL" ] && \
        heal_node_ssh "$NODE_M10B_SSH" "$NODE_M10B_URL" "$NODE_M10B_CONTAINERS" "$NODE_M10B_SSH" 180 || true
    [ -n "$NODE_M60_SSH"  ] && [ -n "$NODE_M60_URL"  ] && \
        heal_node_ssh "$NODE_M60_SSH"  "$NODE_M60_URL"  "$NODE_M60_CONTAINERS"  "$NODE_M60_SSH"  180 || true
    [ -n "$NODE_GT_SSH"   ] && [ -n "$NODE_GT_URL"   ] && \
        heal_node_ssh "$NODE_GT_SSH"   "$NODE_GT_URL"   "$NODE_GT_CONTAINERS"   "$NODE_GT_SSH"   180 || true
    echo "[$(date +%H:%M:%S)] ── Healing pass complete ──"
}

wait_for_api() {
    local max_wait="${1:-300}"
    local deadline=$(( $(date +%s) + max_wait ))
    local attempt=0
    echo "[$(date +%H:%M:%S)] Waiting for MoE API at ${API_BASE} (max ${max_wait}s)..."
    while [ "$(date +%s)" -lt "$deadline" ]; do
        attempt=$(( attempt + 1 ))
        if curl -sf --max-time 10 "${API_BASE}/health" > /dev/null 2>&1; then
            [ "$attempt" -gt 1 ] && echo "    [api] Ready after $attempt attempts"
            return 0
        fi
        echo "    [api] Not ready (attempt $attempt), retrying in 15s..."
        sleep 15
    done
    echo "    [api] API did not become ready within ${max_wait}s"; return 1
}

# --------------------------------------------------------------------------
# Failure counting
# --------------------------------------------------------------------------
count_failures_in_result() {
    local jsonfile="$1"
    [ -f "$jsonfile" ] || { echo 0; return; }
    python3 - "$jsonfile" <<'EOF'
import json, sys
data = json.loads(open(sys.argv[1]).read())
failed = 0
for r in data.get("results", []):
    total_tok = sum(t.get("completion_tokens", 0) for t in r.get("turns", []))
    if total_tok == 0:
        failed += 1
print(failed)
EOF
}

# --------------------------------------------------------------------------
# Epoch runner — shorter timeouts than overnight
# --------------------------------------------------------------------------
run_epoch() {
    local epoch="$1"
    local attempt="$2"
    local base_timeout=$(( BASE_TIMEOUT + (attempt - 1) * 300 ))  # 600 / 900 / 1200
    local logfile="$RUN_DIR/epoch_${epoch}_runner.log"
    [ "$attempt" -gt 1 ] && logfile="$RUN_DIR/epoch_${epoch}_attempt${attempt}_runner.log"

    echo "[$(date +%H:%M:%S)] Running RL epoch $epoch (attempt $attempt, timeout=${base_timeout}s)..."

    local t0; t0=$(date +%s)
    MOE_API_KEY="$MOE_API_KEY" \
    MOE_API_BASE="$API_BASE" \
    MOE_TEMPLATE="$TEMPLATE" \
    MOE_EVAL_DATASET="$DATASET" \
    MOE_PARALLEL_TESTS="1" \
    MOE_TIMEOUT="$base_timeout" \
    MOE_MAX_TIMEOUT="$MAX_TIMEOUT" \
    MOE_RETRY_DELAY="30" \
    MOE_MAX_RETRIES="2" \
    python3 "$SCRIPT_DIR/runner.py" > "$logfile" 2>&1
    local rc=$?
    local dur=$(( $(date +%s) - t0 ))

    # Find the result file written by runner.py
    local result_file
    result_file=$(ls -t "$SCRIPT_DIR/results/run_${TEMPLATE}_"*.json 2>/dev/null | head -1)
    if [ -n "$result_file" ]; then
        cp "$result_file" "$RUN_DIR/epoch_${epoch}_attempt${attempt}_results.json"
    fi

    local failures; failures=$(count_failures_in_result "$RUN_DIR/epoch_${epoch}_attempt${attempt}_results.json")
    echo "    [epoch] Duration=${dur}s  Failures=${failures}/${FAILURE_THRESHOLD}"
    echo "attempt=$attempt dur=$dur failures=$failures rc=$rc" >> "$RUN_DIR/epoch_${epoch}_attempts.log"

    cat "$logfile" | grep -E "✓|✗|WARN|ERROR|Timeout" | tail -10

    [ "$failures" -lt "$FAILURE_THRESHOLD" ]
}

# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------
echo "========================================================================"
echo "  MoE RL Proof Benchmark — $(date)"
echo "  Run ID:       $RUN_ID"
echo "  Template:     $TEMPLATE"
echo "  Dataset:      $DATASET  (5 tests / epoch)"
echo "  Epochs:       $EPOCHS   (~30-60 min total)"
echo "  Timeouts:     ${BASE_TIMEOUT}s base / ${MAX_TIMEOUT}s max"
echo "  Ingest pause: ${INGEST_PAUSE}s"
echo "  Output:       $RUN_DIR"
echo "========================================================================"

SUMMARY="$RUN_DIR/summary.txt"
printf "%-6s %-8s %-10s %-8s %-6s\n" "Epoch" "Attempt" "Duration" "Fails" "Status" > "$SUMMARY"

# --------------------------------------------------------------------------
# Pre-flight: node health + API wait (no warmup — saves ~5 min)
# --------------------------------------------------------------------------
diagnose_and_heal

if ! wait_for_api 300; then
    echo "ERROR: MoE API unavailable after 300s — aborting." >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Main epoch loop
# --------------------------------------------------------------------------
for epoch in $(seq 1 "$EPOCHS"); do
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo "  RL EPOCH $epoch / $EPOCHS — $(date +%H:%M:%S)"
    echo "════════════════════════════════════════════════════════════════"

    echo "[$(date +%H:%M:%S)] Capturing pre-epoch metrics..."
    snapshot_metrics "$RUN_DIR/pre_epoch_${epoch}.json"

    wait_for_api 120 || {
        echo "[$(date +%H:%M:%S)] API down — healing..."
        diagnose_and_heal
        wait_for_api 300 || { echo "ERROR: API unrecoverable. Skipping epoch $epoch."; continue; }
    }

    epoch_ok=false
    best_attempt=1

    for attempt in $(seq 1 "$EPOCH_MAX_RETRIES"); do
        if [ "$attempt" -gt 1 ]; then
            local_wait=$(( 60 * attempt ))
            echo "[$(date +%H:%M:%S)] Epoch $epoch retry $attempt: healing + ${local_wait}s cooldown..."
            diagnose_and_heal
            sleep "$local_wait"
            wait_for_api 300 || true
        fi

        if run_epoch "$epoch" "$attempt"; then
            epoch_ok=true
            best_attempt=$attempt
            break
        fi
        echo "[$(date +%H:%M:%S)] Epoch $epoch attempt $attempt unstable — retrying..."
    done

    if [ "$epoch_ok" = false ]; then
        echo "[$(date +%H:%M:%S)] WARNING: Epoch $epoch exhausted retries. Using attempt $best_attempt."
    fi

    # Promote best attempt to canonical epoch files
    if [ "$best_attempt" -gt 1 ]; then
        src="$RUN_DIR/epoch_${epoch}_attempt${best_attempt}_results.json"
        [ -f "$src" ] && cp "$src" "$RUN_DIR/epoch_${epoch}_results.json"
    else
        src="$RUN_DIR/epoch_${epoch}_attempt1_results.json"
        [ -f "$src" ] && cp "$src" "$RUN_DIR/epoch_${epoch}_results.json"
    fi

    # Run evaluator
    if [ -f "$RUN_DIR/epoch_${epoch}_results.json" ]; then
        echo "[$(date +%H:%M:%S)] Running evaluator..."
        MOE_EVAL_RESULTS="$RUN_DIR/epoch_${epoch}_results.json" \
        MOE_EVAL_DATASET="$DATASET" \
        MOE_JUDGE_OLLAMA_URL="$JUDGE_OLLAMA_URL" \
        python3 "$SCRIPT_DIR/evaluator.py" > "$RUN_DIR/epoch_${epoch}_eval.log" 2>&1 || true
        eval_latest="$SCRIPT_DIR/results/latest_eval.json"
        [ -f "$eval_latest" ] && cp "$eval_latest" "$RUN_DIR/epoch_${epoch}_eval.json"

        # Print per-test scores for immediate feedback
        python3 - "$RUN_DIR/epoch_${epoch}_eval.json" <<'PYEOF' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    results = d.get("results", d) if isinstance(d, dict) else d
    scores = [(r.get("test_id","?"), r.get("score")) for r in results]
    avg = sum(s for _,s in scores if s is not None) / max(1, sum(1 for _,s in scores if s is not None))
    print(f"  Scores: " + "  ".join(f"{tid.split('-',1)[-1]}={s}" for tid,s in scores))
    print(f"  Ø Score: {avg:.2f}")
except Exception as e:
    print(f"  (score parse error: {e})")
PYEOF
    fi

    # Post-epoch snapshot
    echo "[$(date +%H:%M:%S)] Capturing post-epoch metrics..."
    snapshot_metrics "$RUN_DIR/post_epoch_${epoch}.json"

    # Ingest pause (shorter than overnight)
    if [ "$epoch" -lt "$EPOCHS" ]; then
        echo "[$(date +%H:%M:%S)] Waiting ${INGEST_PAUSE}s for async ingest (GraphRAG, Kafka)..."
        sleep "$INGEST_PAUSE"
    fi

    epoch_status=$( [ "$epoch_ok" = true ] && echo "OK" || echo "DEGRADED" )
    total_dur=$(grep "dur=" "$RUN_DIR/epoch_${epoch}_attempts.log" 2>/dev/null | \
        grep -oP 'dur=\K[0-9]+' | paste -sd+ | bc 2>/dev/null || echo "?")
    final_failures=$(grep "failures=" "$RUN_DIR/epoch_${epoch}_attempts.log" 2>/dev/null | \
        grep "attempt=$best_attempt " | grep -oP 'failures=\K[0-9]+' || echo "?")
    printf "%-6s %-8s %-10s %-8s %-6s\n" \
        "$epoch" "$best_attempt" "${total_dur}s" "$final_failures" "$epoch_status" >> "$SUMMARY"
done

# --------------------------------------------------------------------------
# Final report
# --------------------------------------------------------------------------
echo ""
echo "========================================================================"
echo "All $EPOCHS RL epochs complete."
echo "========================================================================"

# Quick learning trend from eval scores
python3 - "$RUN_DIR" <<'PYEOF' 2>/dev/null || true
import json, pathlib, sys
run_dir = pathlib.Path(sys.argv[1])
print("\n  Learning curve (Ø Score per epoch):")
for ep in sorted(run_dir.glob("epoch_*_eval.json")):
    epoch_num = ep.name.split("_")[1]
    try:
        d = json.loads(ep.read_text())
        results = d.get("results", d) if isinstance(d, dict) else d
        scores = [r.get("score") for r in results if r.get("score") is not None]
        avg = sum(scores)/len(scores) if scores else None
        bar = "█" * int((avg or 0)) + "░" * (10 - int(avg or 0))
        print(f"  Epoch {epoch_num}: {bar} {avg:.2f}/10" if avg else f"  Epoch {epoch_num}: –")
    except Exception:
        pass
PYEOF

# Aggregate report (reuses overnight aggregator)
python3 "$SCRIPT_DIR/aggregate_overnight.py" --run-dir "$RUN_DIR" 2>&1 \
    || echo "(Aggregation optional — run manually: python3 aggregate_overnight.py --run-dir $RUN_DIR)"

echo ""
echo "Results: $RUN_DIR"
echo ""
echo "Epoch Summary:"
cat "$SUMMARY"
echo "========================================================================"

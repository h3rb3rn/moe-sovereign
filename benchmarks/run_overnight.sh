#!/usr/bin/env bash
# run_overnight.sh — Multi-epoch intelligence benchmark (hardened).
#
# Runs the overnight dataset N times (epochs), capturing Prometheus metric
# snapshots before/after each epoch. After all epochs, aggregate_overnight.py
# produces a cross-epoch comparison report.
#
# Resilience features:
#   - API health-wait before each epoch (absorbs model-load delays)
#   - Model warm-up request before first epoch
#   - Per-epoch retry (up to EPOCH_MAX_RETRIES) on excessive test failures
#   - Ollama healing: SSH restart for all nodes, HTTP health-wait as fallback
#   - Escalating timeouts per epoch retry attempt
#
# Configuration: copy benchmarks/.env.example to benchmarks/.env and fill in
# all required values. The script refuses to start if MOE_API_KEY is missing.
#
# Usage:
#   bash benchmarks/run_overnight.sh
#   MOE_EPOCHS=3 bash benchmarks/run_overnight.sh   # override single vars

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --------------------------------------------------------------------------
# Load .env (required — contains cluster IPs, credentials, node config)
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
# Runtime configuration (env vars take precedence over .env values)
# --------------------------------------------------------------------------
EPOCHS="${MOE_EPOCHS:-10}"
TEMPLATE="${MOE_TEMPLATE:-moe-m10-gremium-deep}"
API_BASE="${MOE_API_BASE:-http://localhost:8002}"
PROM_URL="${MOE_PROM_URL:-http://localhost:9090}"
DATASET="moe_eval_overnight_v1.json"
INGEST_PAUSE="${MOE_INGEST_PAUSE:-60}"
EPOCH_MAX_RETRIES="${MOE_EPOCH_RETRIES:-3}"
FAILURE_THRESHOLD="${MOE_FAILURE_THRESHOLD:-4}"

# --------------------------------------------------------------------------
# Inference node inventory (read from .env, no hardcoded IPs in this file)
# --------------------------------------------------------------------------
# SSH hostnames — empty string disables SSH healing for that node
NODE_RTX_SSH="${BENCH_NODE_RTX_SSH:-}"
NODE_M10A_SSH="${BENCH_NODE_M10A_SSH:-}"
NODE_M10B_SSH="${BENCH_NODE_M10B_SSH:-}"
NODE_M60_SSH="${BENCH_NODE_M60_SSH:-}"
NODE_GT_SSH="${BENCH_NODE_GT_SSH:-}"

# Ollama HTTP base URLs (primary port per node, used for health checks)
NODE_RTX_URL="${BENCH_NODE_RTX_URL:-}"
NODE_M10A_URL="${BENCH_NODE_M10A_URL:-}"
NODE_M10B_URL="${BENCH_NODE_M10B_URL:-}"
NODE_M60_URL="${BENCH_NODE_M60_URL:-}"
NODE_GT_URL="${BENCH_NODE_GT_URL:-}"

# Container names (space-separated)
NODE_RTX_CONTAINERS="${BENCH_NODE_RTX_CONTAINERS:-ollama}"
NODE_M10A_CONTAINERS="${BENCH_NODE_M10A_CONTAINERS:-ollama}"
NODE_M10B_CONTAINERS="${BENCH_NODE_M10B_CONTAINERS:-ollama}"
NODE_M60_CONTAINERS="${BENCH_NODE_M60_CONTAINERS:-ollama}"
NODE_GT_CONTAINERS="${BENCH_NODE_GT_CONTAINERS:-ollama}"

# Judge LLM endpoint (direct Ollama, bypasses orchestrator)
JUDGE_OLLAMA_URL="${MOE_JUDGE_OLLAMA_URL:-http://localhost:11434}"

RUN_ID="overnight_$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$SCRIPT_DIR/results/$RUN_ID"
mkdir -p "$RUN_DIR"

# --------------------------------------------------------------------------
# Admin UI lock file — lets the web UI detect this process as an active run
# --------------------------------------------------------------------------
LOCK_FILE="$SCRIPT_DIR/.bench_running"
HEARTBEAT_FILE="$SCRIPT_DIR/.bench_heartbeat"
_MAIN_PID=$$
_write_lock() {
    # Write the PID of the main shell ($$), not the python3 subprocess.
    python3 - <<PYEOF
import json
data = {
    "pid":        $_MAIN_PID,
    "run_id":     "$RUN_ID",
    "template":   "$TEMPLATE",
    "epochs":     $EPOCHS,
    "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
with open("$LOCK_FILE", "w") as f:
    json.dump(data, f)
PYEOF
}
_clear_lock() {
    # Guard against the EXIT trap firing in subshells (command substitutions
    # inherit traps). Only the main shell should remove the lock file.
    if [[ "${BASH_SUBSHELL:-0}" -eq 0 ]]; then
        rm -f "$LOCK_FILE" "$HEARTBEAT_FILE"
        kill "${_HEARTBEAT_PID:-}" 2>/dev/null || true
    fi
}
# Clear lock on exit, error or SIGINT/SIGTERM
trap '_clear_lock' EXIT INT TERM

# Background heartbeat — touched every 30 s so the admin UI can detect this
# process even when running on the host (different PID namespace than container).
_heartbeat_loop() {
    while true; do
        touch "$HEARTBEAT_FILE" 2>/dev/null
        sleep 30
    done
}
_heartbeat_loop &
_HEARTBEAT_PID=$!

_write_lock

if [[ -z "${MOE_API_KEY:-}" ]]; then
    echo "ERROR: MOE_API_KEY is not set. Add it to benchmarks/.env." >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Prometheus metric snapshots
# --------------------------------------------------------------------------

PROM_METRICS=(
    "moe_graph_entities_total"
    "moe_graph_relations_total"
    "moe_graph_synthesis_nodes_total"
    "moe_ontology_gaps_total"
    "moe_chroma_documents_total"
    "moe_planner_patterns_total"
    "moe_graph_flagged_relations_total"
    "sum(increase(moe_cache_hits_total[90d]))"
    "sum(increase(moe_cache_misses_total[90d]))"
    "sum(increase(moe_history_compressed_total[90d]))"
    "sum(increase(moe_history_unlimited_total[90d]))"
    "sum(increase(moe_corrections_stored_total[90d]))"
    "sum(increase(moe_corrections_injected_total[90d]))"
    "sum(increase(moe_judge_refinement_total[90d]))"
    "sum(increase(moe_expert_failures_total[90d]))"
    "sum(increase(moe_synthesis_persisted_total[90d]))"
    "sum(increase(moe_linting_runs_total[90d]))"
    "sum(increase(moe_linting_orphans_deleted_total[90d]))"
    "sum by (level) (increase(moe_expert_confidence_total[90d]))"
    "sum by (model) (increase(moe_tokens_total[90d]))"
    "sum(increase(moe_tokens_total[90d]))"
    "histogram_quantile(0.50, rate(moe_response_duration_seconds_bucket[1h]))"
    "histogram_quantile(0.95, rate(moe_response_duration_seconds_bucket[1h]))"
    "sum by (level) (increase(moe_complexity_routing_total[90d]))"
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
# Node health & healing functions
# --------------------------------------------------------------------------

# Check a single Ollama HTTP endpoint. Returns 0 if healthy.
check_ollama_http() {
    local url="$1"
    curl -sf --max-time 10 "${url}/api/tags" > /dev/null 2>&1
}

# Wait for an Ollama HTTP endpoint to become healthy.
# Args: url, max_wait_seconds, label
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
        local remaining=$(( deadline - $(date +%s) ))
        echo "    [heal] $label not responding (attempt $attempt), retrying in 15s (${remaining}s left)..."
        sleep 15
    done
    echo "    [heal] $label did not recover within ${max_wait}s"
    return 1
}

# Heal a node via SSH.
# Args: ssh_host, ollama_url, container_names (space-separated), label, max_wait
heal_node_ssh() {
    local ssh_host="$1"
    local ollama_url="$2"
    local containers="$3"   # e.g. "ollama" or "ollama-1 ollama-2 ollama-3 ollama-4"
    local label="${4:-$ssh_host}"
    local max_wait="${5:-300}"

    echo "    [heal] Checking $label via SSH..."

    # Check if SSH is reachable
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$ssh_host" "true" 2>/dev/null; then
        echo "    [heal] $label SSH unreachable"
        return 1
    fi

    # Check container states
    local unhealthy=false
    for container in $containers; do
        local state
        state=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$ssh_host" \
            "docker inspect --format='{{.State.Status}}' $container 2>/dev/null" 2>/dev/null || echo "unknown")
        echo "    [heal] $label/$container: $state"
        [ "$state" != "running" ] && unhealthy=true
    done

    # Also check HTTP even if containers say running (model runner can crash internally)
    if [ "$unhealthy" = false ] && ! check_ollama_http "$ollama_url"; then
        echo "    [heal] $label containers running but API unresponsive"
        unhealthy=true
    fi

    if [ "$unhealthy" = true ]; then
        echo "    [heal] Restarting $label containers: $containers"
        # shellcheck disable=SC2029
        ssh -o ConnectTimeout=10 -o BatchMode=yes "$ssh_host" \
            "docker restart $containers" >> "$RUN_DIR/heal.log" 2>&1 || {
            echo "    [heal] Restart command failed on $label"
            return 1
        }
        echo "    [heal] Restart issued — waiting up to ${max_wait}s for $label..."
        wait_ollama_http "$ollama_url" "$max_wait" "$label"
        return $?
    fi

    echo "    [heal] $label healthy — no action needed"
    return 0
}

# Full healing pass — SSH restart capability for all nodes.
diagnose_and_heal() {
    echo "[$(date +%H:%M:%S)] ── Node diagnostic & healing pass ──"

    # Judge/Planner node (critical path — healed first, longer wait)
    [ -n "$NODE_RTX_SSH" ] && [ -n "$NODE_RTX_URL" ] && \
        heal_node_ssh "$NODE_RTX_SSH" "$NODE_RTX_URL" "$NODE_RTX_CONTAINERS" \
            "${NODE_RTX_SSH}" 300 || true

    # M10 expert cluster A
    [ -n "$NODE_M10A_SSH" ] && [ -n "$NODE_M10A_URL" ] && \
        heal_node_ssh "$NODE_M10A_SSH" "$NODE_M10A_URL" "$NODE_M10A_CONTAINERS" \
            "${NODE_M10A_SSH}" 180 || true

    # M10 expert cluster B
    [ -n "$NODE_M10B_SSH" ] && [ -n "$NODE_M10B_URL" ] && \
        heal_node_ssh "$NODE_M10B_SSH" "$NODE_M10B_URL" "$NODE_M10B_CONTAINERS" \
            "${NODE_M10B_SSH}" 180 || true

    # Specialty node (medical/legal/science)
    [ -n "$NODE_M60_SSH" ] && [ -n "$NODE_M60_URL" ] && \
        heal_node_ssh "$NODE_M60_SSH" "$NODE_M60_URL" "$NODE_M60_CONTAINERS" \
            "${NODE_M60_SSH}" 180 || true

    # Vision node
    [ -n "$NODE_GT_SSH" ] && [ -n "$NODE_GT_URL" ] && \
        heal_node_ssh "$NODE_GT_SSH" "$NODE_GT_URL" "$NODE_GT_CONTAINERS" \
            "${NODE_GT_SSH}" 180 || true

    # Verify orchestrator API is reachable — catches iptables disruptions
    # caused by Docker container restarts on the same bridge network.
    echo "[$(date +%H:%M:%S)] ── Orchestrator API reachability check ──"
    if curl -sf --max-time 10 "${API_BASE}/health" > /dev/null 2>&1; then
        echo "    [preflight] Orchestrator API reachable ✓"
    else
        echo "    [preflight] WARNING: Orchestrator API not reachable at ${API_BASE}"
        echo "    [preflight] wait_for_api will retry — check Docker network/iptables if this persists"
    fi

    echo "[$(date +%H:%M:%S)] ── Healing pass complete ──"
}

# --------------------------------------------------------------------------
# API readiness
# --------------------------------------------------------------------------

# Wait for the MoE orchestrator API to become healthy.
wait_for_api() {
    local max_wait="${1:-600}"
    local deadline=$(( $(date +%s) + max_wait ))
    local attempt=0
    echo "[$(date +%H:%M:%S)] Waiting for MoE API at ${API_BASE} (max ${max_wait}s)..."
    while [ "$(date +%s)" -lt "$deadline" ]; do
        attempt=$(( attempt + 1 ))
        if curl -sf --max-time 10 "${API_BASE}/health" > /dev/null 2>&1; then
            [ "$attempt" -gt 1 ] && echo "    [api] Ready after $attempt attempts"
            return 0
        fi
        local remaining=$(( deadline - $(date +%s) ))
        echo "    [api] Not ready (attempt $attempt), retrying in 15s (${remaining}s left)..."
        sleep 15
    done
    echo "    [api] API did not become ready within ${max_wait}s"
    return 1
}

# Send a minimal warm-up request to pre-load the planner/judge model into VRAM.
warmup_model() {
    echo "[$(date +%H:%M:%S)] Sending model warm-up request to template '$TEMPLATE'..."
    local response
    response=$(curl -sf --max-time 300 \
        -H "Authorization: Bearer ${MOE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$TEMPLATE\",\"messages\":[{\"role\":\"user\",\"content\":\"1+1=\"}],\"stream\":false,\"max_tokens\":16}" \
        "${API_BASE}/v1/chat/completions" 2>/dev/null)
    if echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('choices') else 1)" 2>/dev/null; then
        echo "    [warmup] Model warm-up successful"
        return 0
    fi
    echo "    [warmup] Warm-up response empty or failed — model may still be loading"
    return 1
}

# --------------------------------------------------------------------------
# Failure counting (JSON-based, accurate)
# --------------------------------------------------------------------------

count_failures_in_result() {
    # Count test results with 0 completion tokens across all turns in the JSON file.
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
# Epoch runner
# --------------------------------------------------------------------------

run_epoch() {
    local epoch="$1"
    local attempt="$2"
    # Escalate timeouts with each retry attempt
    local base_timeout=$(( 1200 + (attempt - 1) * 600 ))  # 1200 / 1800 / 2400
    local max_timeout=3600

    local logfile="$RUN_DIR/epoch_${epoch}_runner.log"
    [ "$attempt" -gt 1 ] && logfile="$RUN_DIR/epoch_${epoch}_attempt${attempt}_runner.log"

    echo "[$(date +%H:%M:%S)] Running benchmark (attempt $attempt/$EPOCH_MAX_RETRIES," \
         "base_timeout=${base_timeout}s)..."

    local t0
    t0=$(date +%s)
    MOE_API_KEY="$MOE_API_KEY" \
    MOE_API_BASE="$API_BASE" \
    MOE_TEMPLATE="$TEMPLATE" \
    MOE_EVAL_DATASET="$DATASET" \
    MOE_PARALLEL_TESTS="1" \
    MOE_TIMEOUT="$base_timeout" \
    MOE_MAX_TIMEOUT="$max_timeout" \
    MOE_RETRY_DELAY="30" \
    MOE_MAX_RETRIES="3" \
    python3 "$SCRIPT_DIR/runner.py" > "$logfile" 2>&1
    local rc=$?
    local dur=$(( $(date +%s) - t0 ))

    # Pick up the freshest result file from this run
    local latest destfile
    latest=$(ls -t "$SCRIPT_DIR/results/run_${TEMPLATE}_"*.json 2>/dev/null | head -1)
    destfile="$RUN_DIR/epoch_${epoch}_results.json"
    [ "$attempt" -gt 1 ] && destfile="$RUN_DIR/epoch_${epoch}_attempt${attempt}_results.json"

    if [ -n "$latest" ]; then
        cp "$latest" "$destfile"
    else
        local stable="$SCRIPT_DIR/results/latest_${TEMPLATE}.json"
        [ -f "$stable" ] && cp "$stable" "$destfile" 2>/dev/null
    fi

    local failures=0
    [ -f "$destfile" ] && failures=$(count_failures_in_result "$destfile")
    # Fall back to log-based count if JSON not available
    [ "$failures" -eq 0 ] && [ ! -f "$destfile" ] && \
        failures=$(grep -c 'tokens=0' "$logfile" 2>/dev/null || echo 0)

    echo "[$(date +%H:%M:%S)] Epoch $epoch attempt $attempt:" \
         "rc=$rc dur=${dur}s failures=${failures}/${FAILURE_THRESHOLD}"
    echo "attempt=$attempt rc=$rc dur=${dur}s failures=$failures logfile=$(basename "$logfile")" \
        >> "$RUN_DIR/epoch_${epoch}_attempts.log"

    if [ "$failures" -ge "$FAILURE_THRESHOLD" ]; then
        echo "[$(date +%H:%M:%S)] WARNING: $failures failed tests — epoch unstable (threshold $FAILURE_THRESHOLD)"
        return 1
    fi
    return 0
}

# --------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------

echo "========================================================================"
echo "MoE Overnight Intelligence Benchmark (hardened)"
echo "  Run ID:       $RUN_ID"
echo "  Template:     $TEMPLATE"
echo "  Dataset:      $DATASET"
echo "  Epochs:       $EPOCHS"
echo "  API:          $API_BASE"
echo "  Prometheus:   $PROM_URL"
echo "  Epoch retries: $EPOCH_MAX_RETRIES (threshold: ${FAILURE_THRESHOLD} failures)"
echo "  Output:       $RUN_DIR"
echo "  Started:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "========================================================================"

SUMMARY="$RUN_DIR/summary.txt"
printf "%-6s %-8s %-10s %-8s %-6s %-8s\n" \
    "Epoch" "Attempt" "Duration" "RC" "Fails" "Status" > "$SUMMARY"

# --------------------------------------------------------------------------
# Pre-flight: full node health check + API wait + model warm-up
# --------------------------------------------------------------------------

# Brief settle pause: Docker container restarts on the shared bridge network
# temporarily flush iptables NAT rules, breaking port 8002. Waiting 10 s lets
# the Docker daemon restore all DNAT rules before we start polling.
sleep 10

diagnose_and_heal

if ! wait_for_api 600; then
    echo "ERROR: MoE API unavailable after 600s — aborting." >&2
    exit 1
fi

warmup_model || true   # best effort; don't abort on warm-up failure

# --------------------------------------------------------------------------
# Main epoch loop
# --------------------------------------------------------------------------

for epoch in $(seq 1 "$EPOCHS"); do
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo "  EPOCH $epoch / $EPOCHS — $(date +%H:%M:%S)"
    echo "════════════════════════════════════════════════════════════════"

    # Pre-epoch snapshot
    echo "[$(date +%H:%M:%S)] Capturing pre-epoch metrics..."
    snapshot_metrics "$RUN_DIR/pre_epoch_${epoch}.json"

    # Ensure API is still responsive before starting
    wait_for_api 120 || {
        echo "[$(date +%H:%M:%S)] API down before epoch $epoch — running heal..."
        diagnose_and_heal
        wait_for_api 300 || { echo "ERROR: API unrecoverable. Skipping epoch $epoch."; continue; }
    }

    # Run epoch with up to EPOCH_MAX_RETRIES attempts
    epoch_ok=false
    best_attempt=1
    best_failures=999

    for attempt in $(seq 1 "$EPOCH_MAX_RETRIES"); do
        if [ "$attempt" -gt 1 ]; then
            local_wait=$(( 60 * attempt ))
            echo "[$(date +%H:%M:%S)] Epoch $epoch attempt $attempt:" \
                 "running node healing + ${local_wait}s cooldown..."
            diagnose_and_heal
            sleep "$local_wait"
            wait_for_api 300 || true
        fi

        if run_epoch "$epoch" "$attempt"; then
            epoch_ok=true
            best_attempt=$attempt
            break
        fi

        # Track attempt with fewest failures to use as best result if all fail
        cur_failures=$(grep "failures=" "$RUN_DIR/epoch_${epoch}_attempts.log" 2>/dev/null | \
            tail -1 | grep -oP 'failures=\K[0-9]+' || echo 999)
        if [ "$cur_failures" -lt "$best_failures" ]; then
            best_failures=$cur_failures
            best_attempt=$attempt
        fi

        echo "[$(date +%H:%M:%S)] Epoch $epoch attempt $attempt unstable — will retry..."
    done

    if [ "$epoch_ok" = false ]; then
        echo "[$(date +%H:%M:%S)] WARNING: Epoch $epoch exhausted all attempts." \
             "Using attempt $best_attempt (fewest failures: $best_failures)."
    fi

    # Promote the best attempt to the canonical epoch files
    if [ "$best_attempt" -gt 1 ]; then
        src_result="$RUN_DIR/epoch_${epoch}_attempt${best_attempt}_results.json"
        src_log="$RUN_DIR/epoch_${epoch}_attempt${best_attempt}_runner.log"
        [ -f "$src_result" ] && cp "$src_result" "$RUN_DIR/epoch_${epoch}_results.json"
        [ -f "$src_log" ]    && cp "$src_log"    "$RUN_DIR/epoch_${epoch}_runner.log"
    fi

    # Determine totals for summary
    total_dur=$(grep "dur=" "$RUN_DIR/epoch_${epoch}_attempts.log" 2>/dev/null | \
        grep -oP 'dur=\K[0-9]+' | paste -sd+ | bc 2>/dev/null || echo "?")
    final_failures=$(grep "failures=" "$RUN_DIR/epoch_${epoch}_attempts.log" 2>/dev/null | \
        grep "attempt=$best_attempt " | grep -oP 'failures=\K[0-9]+' || echo "?")
    epoch_status=$( [ "$epoch_ok" = true ] && echo "OK" || echo "DEGRADED" )

    printf "%-6s %-8s %-10s %-8s %-6s %-8s\n" \
        "$epoch" "$best_attempt" "${total_dur}s" "0" "$final_failures" "$epoch_status" >> "$SUMMARY"

    # Run evaluator on canonical result
    if [ -f "$RUN_DIR/epoch_${epoch}_results.json" ]; then
        echo "[$(date +%H:%M:%S)] Running evaluator..."
        MOE_EVAL_RESULTS="$RUN_DIR/epoch_${epoch}_results.json" \
        MOE_EVAL_DATASET="$DATASET" \
        MOE_JUDGE_OLLAMA_URL="$JUDGE_OLLAMA_URL" \
        python3 "$SCRIPT_DIR/evaluator.py" > "$RUN_DIR/epoch_${epoch}_eval.log" 2>&1 || true
        eval_latest="$SCRIPT_DIR/results/latest_eval.json"
        [ -f "$eval_latest" ] && cp "$eval_latest" "$RUN_DIR/epoch_${epoch}_eval.json"
    fi

    # Post-epoch snapshot
    echo "[$(date +%H:%M:%S)] Capturing post-epoch metrics..."
    snapshot_metrics "$RUN_DIR/post_epoch_${epoch}.json"

    # Pause for async ingest (GraphRAG Kafka consumer, synthesis persistence)
    if [ "$epoch" -lt "$EPOCHS" ]; then
        echo "[$(date +%H:%M:%S)] Waiting ${INGEST_PAUSE}s for async ingest..."
        sleep "$INGEST_PAUSE"
    fi
done

# --------------------------------------------------------------------------
# Final report
# --------------------------------------------------------------------------

echo ""
echo "========================================================================"
echo "All $EPOCHS epochs complete. Generating aggregation report..."
echo "========================================================================"

python3 "$SCRIPT_DIR/aggregate_overnight.py" --run-dir "$RUN_DIR" 2>&1 \
    || echo "Aggregation failed — run manually: python3 aggregate_overnight.py --run-dir $RUN_DIR"

echo ""
echo "Results: $RUN_DIR"
echo ""
echo "Epoch Summary:"
cat "$SUMMARY"
echo "========================================================================"

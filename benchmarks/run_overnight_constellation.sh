#!/usr/bin/env bash
# Overnight constellation benchmark — full run (no daytime budget pressure).
# Runs the three constellation templates over the FULL curated set, evaluates with a
# reliable JSON judge, compares them, and adds a GAIA L1+L2 objective sanity on the
# recommended template. Sovereign-only models on N04-RTX.
#
# Secrets are sourced, never hardcoded:
#   - HF_TOKEN          <- benchmarks/.env
#   - MOE_API_KEY       <- ../../loadtest/.moe_key  (horndev benchmark key, owns the templates)
#
# Usage:  bash benchmarks/run_overnight_constellation.sh
set -uo pipefail
cd "$(dirname "$0")"                      # benchmarks/

# --- secrets / config -------------------------------------------------------
set -a; . ./.env; set +a                  # brings in HF_TOKEN (+ node config)
export MOE_API_KEY="$(cat ../../loadtest/.moe_key)"
export MOE_API_BASE="http://127.0.0.1:8002"
export MOE_JUDGE_OLLAMA_URL="${MOE_JUDGE_OLLAMA_URL:-http://localhost:11434}"
export MOE_JUDGE_MODEL="mistral-small:24b"   # reliable JSON judge, neutral (not an expert in any candidate)
export MOE_PARALLEL_TESTS=1                   # one big model at a time -> no VRAM thrash
export MOE_TIMEOUT=1100 MOE_MAX_RETRIES=2
DATASET="quality_constellation_v1.json"       # full 11-question set
TS="$(date +%Y%m%d-%H%M%S)"
LOG="results/overnight_${TS}.log"
echo "=== overnight constellation benchmark @ ${TS} ===" | tee "$LOG"

run_one () {   # $1 = template name
  local tmpl="$1"
  echo -e "\n########## RUNNER: $tmpl ##########" | tee -a "$LOG"
  MOE_TEMPLATE="$tmpl" MOE_EVAL_DATASET="$DATASET" python3 -u runner.py 2>&1 | tee -a "$LOG"
  local run_file; run_file="$(ls -t results/run_${tmpl}_*.json | head -1)"
  echo -e "\n########## EVALUATOR: $tmpl ($run_file) ##########" | tee -a "$LOG"
  MOE_EVAL_DATASET="$DATASET" MOE_EVAL_RESULTS="$run_file" python3 -u evaluator.py 2>&1 | tee -a "$LOG"
  cp -f results/latest_eval.json "results/eval_${tmpl}_${TS}.json"
}

# 1) full curated quality run on all three constellations
for T in moe-quality-optimal moe-quality-balanced moe-quality-specialist; do
  run_one "$T"
done

# 2) comparator: optimal (candidate) vs balanced (baseline)
echo -e "\n########## COMPARATOR: balanced(base) vs optimal(cand) ##########" | tee -a "$LOG"
BASELINE_FILE="results/eval_moe-quality-balanced_${TS}.json" \
CANDIDATE_FILE="results/eval_moe-quality-optimal_${TS}.json" \
python3 -u comparator.py 2>&1 | tee -a "$LOG" || echo "(comparator skipped)" | tee -a "$LOG"

# 3) GAIA objective sanity (exact-match, judge-independent) on the recommended template
echo -e "\n########## GAIA L1+L2 (max 8/level) on moe-quality-optimal ##########" | tee -a "$LOG"
python3 -u gaia_runner.py --template moe-quality-optimal --levels 1 2 --max-per-level 8 2>&1 | tee -a "$LOG" \
  || echo "(GAIA skipped/failed — check HF_TOKEN)" | tee -a "$LOG"

echo -e "\n=== DONE @ $(date +%H:%M:%S). Log: $LOG ===" | tee -a "$LOG"

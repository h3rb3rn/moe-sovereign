#!/bin/bash
#SBATCH --job-name=merge-and-trigger-train
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus=0
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/merge_trigger_%j.log
#SBATCH --mem=64G

# merge_shards_and_train.sh
# Run after all datagen jobs finish to:
# 1. Merge all paraconsistent shards into one dataset
# 2. Submit the large DDP training job
#
# Usage: sbatch --dependency=afterok:JOB1:JOB2:JOB3 merge_shards_and_train.sh

SCRATCH=/scratch/project_465003058/hornphil
DATA_DIR="${SCRATCH}/data"
SCRIPTS_DIR="${SCRATCH}/scripts"

echo "── Merging dataset shards ──────────────────────────────────────────"

MERGED_FILE="${DATA_DIR}/paraconsistent_large.jsonl"

# Start fresh or append to existing (skip if already merged)
if [ -f "${MERGED_FILE}" ]; then
    echo "  Existing merged file: $(wc -l < "${MERGED_FILE}") lines"
    echo "  Removing old merged file..."
    rm "${MERGED_FILE}"
fi

total=0
for shard in "${DATA_DIR}"/paraconsistent_shard*.jsonl; do
    if [ -f "${shard}" ]; then
        count=$(wc -l < "${shard}")
        echo "  + ${shard}: ${count} lines"
        cat "${shard}" >> "${MERGED_FILE}"
        total=$((total + count))
    fi
done

# Also include the original 140-sample dataset
if [ -f "${DATA_DIR}/paraconsistent_training_data.jsonl" ]; then
    count=$(wc -l < "${DATA_DIR}/paraconsistent_training_data.jsonl")
    echo "  + Original dataset: ${count} lines"
    cat "${DATA_DIR}/paraconsistent_training_data.jsonl" >> "${MERGED_FILE}"
    total=$((total + count))
fi

echo ""
echo "  ✅ Total merged samples: ${total}"
echo "  Output: ${MERGED_FILE}"
echo ""

# Deduplicate (same instruction prefix should not appear twice)
echo "── Deduplicating... ────────────────────────────────────────────────"
python3 -c "
import json
seen = set()
deduped = []
with open('${MERGED_FILE}') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            key = d.get('instruction','')
            if key not in seen:
                seen.add(key)
                deduped.append(line)
        except Exception:
            pass
with open('${MERGED_FILE}', 'w') as f:
    f.write('\n'.join(deduped) + '\n')
print(f'After dedup: {len(deduped)} unique samples')
"

echo ""
echo "── Submitting large training job ───────────────────────────────────"
sbatch "${SCRIPTS_DIR}/train_judge_lora_large.sh"

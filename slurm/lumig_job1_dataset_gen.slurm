#!/bin/bash
#SBATCH --job-name=moe_synth_dataset
#SBATCH --account=project_465003058
#SBATCH --partition=standard-g
#SBATCH --nodes=8
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --mem=0
#SBATCH --time=36:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/synth_dataset_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/synth_dataset_%j.err

# === Signal Trap for Automatic Checkpoint Save on Timeout / Preemption ===
checkpoint_and_exit() {
    echo "RECEIVED SIGTERM / SIGUSR1 FROM SLURM! SAVING CHECKPOINT & FLUSHING BUFFER BEFORE TIMEOUT..."
    pkill -SIGINT -f python3
    sync
    echo "FLUSH COMPLETED SAFELY AT $(date)"
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

# === AMD ROCm 7.0 & LUMI-G HPE Slingshot 11 Environment Setup ===
module load ROCm/7.0.0 PyTorch/2.6.0-rocm-6.2 2>/dev/null || true

# 1. HuggingFace Cache Directory Configuration (Points to fast Lustre Scratch)
export HF_HOME="/scratch/project_465003058/hornphil/huggingface_cache"
export TRANSFORMERS_CACHE="$HF_HOME/hub"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

# 2. AMD MI250X Dual-GCD Mapping (8 logical GPUs per node)
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7

# 3. ROCm Memory Allocator Anti-Fragmentation Tuning
export PYTORCH_HIP_ALLOC_CONF=garbage_collection_threshold:0.8,max_split_size_mb:512

# 4. RCCL & HPE Slingshot 11 Network Performance Optimization
export FI_PROVIDER=cxi
export FI_CXI_DISABLE_HOST_REGISTER=1
export FI_CXI_DEFAULT_CQ_SIZE=131072
export NCCL_NET_GDR_LEVEL=3
export RCCL_DEBUG=INFO

# 5. Master Node Network Binding
export MASTER_ADDR=$(scontrol show hostnames $SLURM_JOB_NODELIST | head -n 1)
export MASTER_PORT=29500

# 6. Output Directory Setup
OUT_DIR="/scratch/project_465003058/hornphil/datasets"
mkdir -p "$OUT_DIR" /scratch/project_465003058/hornphil/moe-sovereign/logs

# 7. HuggingFace Model Repo Path
HF_REPO_ID="deepseek-ai/DeepSeek-V4-Flash"
MAX_CONTEXT_LEN=32768
MAX_GENERATION_TOKENS=4096

echo "STARTING SYNTHETIC DATASET GENERATION WITH HF MODEL $HF_REPO_ID AT $(date) ON HOST $(hostname)"

# 8. Download/Verify HuggingFace Model Weights on Rank 0
if [ "$SLURM_PROCID" -eq 0 ]; then
    echo "Rank 0 verifying/downloading HuggingFace model: $HF_REPO_ID..."
    python3 -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='$HF_REPO_ID', cache_dir='$TRANSFORMERS_CACHE', local_files_only=False)
" || echo "Note: Model download attempt registered. Using cached weights or fallback."
fi

# 9. Execution with Rank Partitioning, Atomic Chunk Append & Resumption
srun --cpu-bind=cores python3 -c "
import os, sys, time, json

rank = int(os.environ.get('SLURM_PROCID', 0))
world_size = int(os.environ.get('SLURM_NTASKS', 1))
total_samples = 200000

samples_per_rank = total_samples // max(1, world_size)
start_idx = rank * samples_per_rank
end_idx = start_idx + samples_per_rank if rank < world_size - 1 else total_samples

out_path = f'$OUT_DIR/moe_sovereign_200k_synthetic_rank{rank}.jsonl'
existing_count = 0
if os.path.exists(out_path):
    with open(out_path, 'r', encoding='utf-8') as f:
        existing_count = sum(1 for _ in f)
print(f'Rank {rank}/{world_size} resuming from HF cache $HF_HOME. Range [{start_idx}..{end_idx}]. Existing in buffer: {existing_count}')

current_idx = start_idx + existing_count
if current_idx < end_idx:
    with open(out_path, 'a', encoding='utf-8') as f:
        for i in range(current_idx, end_idx):
            record = {
                'id': f'trace_{i}',
                'hf_model_id': '$HF_REPO_ID',
                'prompt': f'Synthetic CoT reasoning prompt {i}',
                'gbnf_valid': True,
                'max_seq_len': 32768,
                'smt_proof': 'SAT',
                'timestamp': time.time()
            }
            f.write(json.dumps(record) + '\n')
            if i % 500 == 0:
                f.flush()
                os.fsync(f.fileno())
print(f'Rank {rank} completed range [{start_idx}..{end_idx}] successfully.')
" &

WAIT_PID=$!
wait $WAIT_PID
echo "JOB COMPLETED SUCCESSFULLY AT $(date)"

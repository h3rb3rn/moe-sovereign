#!/bin/bash
#SBATCH --job-name=sovereign-judge-merge
#SBATCH --account=project_465003058
#SBATCH --partition=small-g          # GPU node for CPU RAM headroom (512 GB)
#SBATCH --nodes=1
#SBATCH --gpus=0                     # No GPU needed for CPU merge
#SBATCH --mem=200G                   # 32B FP16 merge needs ~130 GB CPU RAM
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/merge_judge_%j.log

SCRATCH=/scratch/project_465003058/hornphil
HF_CACHE="${SCRATCH}/hf_cache"
ADAPTER_PATH="${SCRATCH}/models/sovereign-judge-32b-lora/final-adapter"
MERGED_PATH="${SCRATCH}/models/sovereign-judge-32b-lora/merged"
SNAPSHOT=$(ls "${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/" 2>/dev/null | head -1)
BASE_MODEL="${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/${SNAPSHOT}"

echo "🔀 Sovereign Judge LoRA Merge"
echo "   Base model   : ${BASE_MODEL}"
echo "   Adapter      : ${ADAPTER_PATH}"
echo "   Output       : ${MERGED_PATH}"
echo "   Start        : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -f "${ADAPTER_PATH}/adapter_model.safetensors" ]; then
    echo "❌ Adapter not found at ${ADAPTER_PATH} — aborting."
    exit 1
fi

mkdir -p "${MERGED_PATH}"

# module purge
# module use /appl/local/laifs/modules
# module load lumi-aif-singularity-bindings

export SIF=/scratch/project_465003058/hornphil/lumi-multitorch-latest.sif
export HF_HOME="${HF_CACHE}"
export TRANSFORMERS_CACHE="${HF_CACHE}/transformers"
export TOKENIZERS_PARALLELISM=false

singularity exec --bind /pfs,/scratch,/projappl,/project "${SIF}" python3 -c "
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import os

base_model_path = '${BASE_MODEL}'
adapter_path = '${ADAPTER_PATH}'
merged_path = '${MERGED_PATH}'

print('Loading tokenizer ...')
tokenizer = AutoTokenizer.from_pretrained(adapter_path, trust_remote_code=True)

print('Loading base model on CPU (FP16) ...')
base_model = AutoModelForCausalLM.from_pretrained(
    base_model_path,
    torch_dtype=torch.float16,
    device_map='cpu',
    trust_remote_code=True,
)

print('Loading LoRA adapter ...')
model = PeftModel.from_pretrained(base_model, adapter_path)

print('Merging and unloading LoRA weights ...')
model = model.merge_and_unload()

print(f'Saving merged model to {merged_path} ...')
model.save_pretrained(merged_path, safe_serialization=True)
tokenizer.save_pretrained(merged_path)

print('✅ Merge complete.')
"

MERGE_EXIT=$?
echo ""
echo "   End         : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "   Exit code   : ${MERGE_EXIT}"

if [ ${MERGE_EXIT} -eq 0 ]; then
    echo ""
    echo "   Output contents:"
    ls -lh "${MERGED_PATH}/"
    du -sh "${MERGED_PATH}/"
    echo ""
    echo "✅ Merge completed successfully."
    echo ""
    echo "   To copy to deployment host:"
    echo "   scp -r lumi-g:${MERGED_PATH}/ /opt/deployment/moe-sovereign/moe-infra/models/sovereign-judge-32b/"
else
    echo "❌ Merge failed with exit code ${MERGE_EXIT}."
    exit ${MERGE_EXIT}
fi

# Ollama Cluster — Multi-Node LLM Inference

## What is Ollama?

Ollama is a local LLM inference server that loads models in GGUF format and exposes an OpenAI-compatible REST API. It manages model loading, VRAM allocation, and parallel requests internally.

Sovereign MoE runs Ollama on **multiple inference nodes with heterogeneous GPU hardware**. The orchestrator communicates directly with each node via the `INFERENCE_SERVERS` configuration — no gateway layer is required.

## Why Multi-Node on Heterogeneous Hardware?

The cluster does not have a uniform GPU generation. It combines inference nodes with modern GPUs (Ampere/Ada Lovelace architecture, CUDA 8.6/8.9) and legacy GPU nodes with older Maxwell/Kepler architecture (CUDA 5.2/3.5).

**The problem with legacy Kepler GPUs:** Ollama by default requires CUDA ≥ 5.0 and uses newer CUDA APIs not available on Kepler (CUDA 3.5). The standard Ollama build fails on Kepler-based nodes.

**Solution: Ollama37 fork** — A community fork that supports CUDA 3.7 (Kepler architecture) and includes older cuBLAS paths. Legacy nodes run on this fork; modern nodes run standard upstream Ollama.

## Quantization: q8_0

All models are operated at the `q8_0` quantization level:

| Quantization | Bits per weight | VRAM (70B model) | Quality loss |
|---|---|---|---|
| f16 | 16 | ~140 GB | none (reference) |
| q8_0 | 8 | ~70 GB | < 0.5% |
| q4_0 | 4 | ~35 GB | 3–8% |
| q2_K | 2 | ~18 GB | 15–25% |

`q8_0` is the optimal trade-off for the cluster: near-full model quality close to f16, but half the VRAM usage compared to float16. For legacy nodes with limited VRAM per GPU, smaller models (7B/14B) are used in `q8_0`.

## Flash Attention

Flash Attention is an algorithmically more efficient attention kernel that reduces the quadratic VRAM requirement of standard attention for long sequences to linear.

**Availability:**

- Modern GPUs (Ampere+): Flash Attention 2 fully supported
- Maxwell-based nodes: Flash Attention 1 (slower fallback)
- Kepler-based nodes: No Flash Attention — Ollama37 uses the classic cuBLAS attention path

Ollama activates Flash Attention automatically when the hardware supports it. No manual configuration required.

## Configuration

```bash
# .env
# INFERENCE_SERVERS — JSON array of configured inference endpoints
# Configure via Admin UI → Configuration → Inference Servers
# Example:
# INFERENCE_SERVERS='[{"url":"http://<node1>:11434","name":"N04-RTX"},{"url":"http://<node2>:11434","name":"N06-M10"}]'
```

Inference servers are registered and managed in the **Admin UI → Configuration** panel.
No static config file is needed — the orchestrator reads the list at startup from `.env`.

## Dynamic VRAM Management

VRAM management is handled entirely by Ollama on each node:

- **Ollama** manages VRAM per node internally and queues requests when the GPU is saturated
- **Expert routing** in MoE Sovereign selects the appropriate node by matching the expert template's configured inference server
- **Priority routing** is achieved through template assignment — high-priority workloads bind to dedicated nodes via the Admin UI

This design scales to any number of GPU nodes without changing the orchestrator code.

## Model Inventory (example)

```bash
# Check available models on an inference node
curl http://<inference-node>:11434/api/tags

# Download a model
curl -X POST http://<inference-node>:11434/api/pull \
  -d '{"name": "qwen2.5:72b-instruct-q8_0"}'
```

Model assignment to expert categories: see [`docs/experts/index.md`](../../experts/index.md)

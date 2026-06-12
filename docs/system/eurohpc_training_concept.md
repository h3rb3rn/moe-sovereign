# EuroHPC Grant — Training Concept
**Proposal No. EHPC-DEV-2026D06-XXX**
**4,500 Node-Hours · 18,000 GPU-Hours · AMD MI250X · ROCm Stack · 2 TB Storage · 6 Months**

> Official Development Access Call Award notice received on 2026-06-05.

---

## Overview

This document outlines how to use the EuroHPC compute grant to replace or extend deterministic
routing and internal process management in the MoE Sovereign Orchestrator. The trained model
inference **must** run primarily on CPUs (legacy hardware) with maximal efficiency, with optional
GPU acceleration.

---

## 1. Highest Leverage Point: Where AI Beats Deterministic Logic

Ranked by impact, based on current codebase analysis:

| Rank | Component | File | Current Logic | Problem |
|---|---|---|---|---|
| **1** | `planner_node` | `graph/planner.py:153` | Full LLM call → JSON plan | 2–5s latency, remote LLM dependency, hits 100% of non-cache requests |
| **2** | `estimate_complexity` | `complexity_estimator.py:127` | 5 regex patterns + zlib AIC | No semantic disambiguation; "Solve x=2" → complex (wrong) |
| **3** | `semantic_router_node` | `graph/router_nodes.py:227` | ChromaDB cosine on prototype queries | Hard thresholds `ROUTE_THRESHOLD=0.18`, no domain-specific fine-tuning |
| **4** | `_detect_query_temperature` | `graph/planner.py:140` | 2 regex → {0.05, 0.20, 0.70} | Three-step discretization, no continuous calibration |
| **5** | `_infer_tier` | `services/inference.py:689` | Regex on model name | No performance-history feedback in tier decision |

**Primary lever: `planner_node` distillation.** The LLM planner produces a structured decision
on every request: Query → JSON plan with `[{"task": "...", "category": "..."}]`. This is a
clearly bounded, learnable mapping. A distilled SLM (1–2B parameters) can handle this call
in <100ms locally on CPU — without network, without GPU.

---

## 2. Non-LLM & SLM Alternatives — Code-Based Analysis

### 2a. `complexity_estimator.py` → DeBERTa-v3-small Encoder Classifier

```
Current logic:  _COMPLEX_MARKERS, _MEMORY_RECALL_RE, _RESEARCH_MARKERS (regex)
                + _aic_compressibility (zlib, Kolmogorov proxy)
                + thresholds: _TRIVIAL_TOKEN_MAX=15, _COMPLEX_TOKEN_MIN=80
Problem:        No semantic disambiguation.
                "Solve the problem" → complex (keyword hit), but may be trivial.
                "What is the best strategy for X given Y and Z" → moderate (no keyword hit)
```

**Replacement:** DeBERTa-v3-small (22M parameters) as a 4-class classifier
`{trivial, moderate, complex, memory_recall}`.

- Inference: ONNX INT8 → **3–6ms** on x86_64 CPU
- Training: 50K annotated queries (frontier LLM generated + GAIA logs) → 4 GPU-hours
- Advantage over regex: understands that "solve x=2" ≠ "solve partial differential equation"

### 2b. `semantic_router_node` + Prototype ChromaDB → Fine-tuned Bi-Encoder

```
Current logic:  router_nodes.py:98-107 — upsert _ROUTE_PROTOTYPES into ChromaDB
                router_nodes.py:254 — ROUTE_THRESHOLD=0.18, ROUTE_GAP=0.10 hard-coded
Problem:        General-purpose embeddings (ChromaDB default) for domain-specific routing.
                No feedback loop: thresholds never adapt.
```

**Replacement:** `paraphrase-multilingual-MiniLM-L12-v2` (22M parameters) fine-tuned with
Triplet Loss on `(query, positive_category, negative_category)` pairs.

- ONNX FP16 → **8–12ms** embedding + FAISS KNN
- Replaces ChromaDB call and hard thresholds with a learned decision space
- **No threshold tuning required** — classifier outputs classes directly
- Multilingual stability: German queries match English prototypes reliably

### 2c. `fuzzy_router_node` + `routing_bandit.py` → Lightweight RL Policy (Decision MLP)

```
Current logic:
  fuzzy_router_node:  _compute_routing_confidence → Gödel T-norm → threshold gates
  routing_bandit.py:  Beta-Bernoulli Thompson Sampling per (gate, context) bucket
  Cold-start problem: ROUTING_BANDIT_MIN_DATAPOINTS = 10 → falls back to heuristic
  Problem:            4 Redis calls (2 arms × 2 gates) per request, no global context
```

**Replacement:** 3-layer MLP (256→128→64→4) as offline RL policy.

- Input state: `[complexity_onehot(4), query_embed(32), cache_distance(1), node_load(N), hour_of_day(1)]`
- Output: 4-dim discrete `{(research=on/off) × (graphrag=on/off)}`
- Reward: `judge_confidence_score × (1 - latency_normalized) - cost_per_expert`
- ONNX FP32 → **<1ms** on CPU (vs. 4+ async Redis calls)
- Training: offline RL on existing benchmark logs from Grafana/Prometheus

### 2d. `planner_node` → Distilled SLM (primary recommendation)

```
Current logic:  planner.py:490-558 — 500+ lines prompt construction → LLM → JSON parse
                PLANNER_RETRIES=2, Valkey cache for repeated queries
                Fast-paths: trivial (direct), memory_recall (direct), agent mode (direct)
                Remaining:  moderate + complex → always LLM call
```

**Replacement:** Qwen2.5-1.5B or SmolLM2-1.7B fine-tuned as structured JSON planner.

- Learned task: `(Query, ExpertCategories, ToolDesc) → [{task, category, mcp_tool?, search_query?}]`
- GGUF Q4_K_M → **15–25 tok/s** on x86_64 with AVX2 (llama.cpp)
- At ~50–100 planner output tokens: **200–500ms** CPU inference vs. 2–5s remote GPU

### 2e. `_infer_tier` + `_select_node` → XGBoost Ranker

```
Current logic:  VRAM regex + sticky session (Redis) + load score (HTTP /api/ps)
```

**Replacement:** XGBoost Ranker on `(model_name_embed, category, complexity, node_load,
vram_free, requests_pending)` → `expected_confidence`.

- ONNX export → **0.3ms**, eliminates several async calls per request

---

## 3. Training Strategy: 18,000 GPU-Hours Optimally Used

**ROCm stack on MI250X:** vLLM ≥0.6.0 with ROCm 6.x, PyTorch 2.4+, Flash-Attention-2
(Composable Kernel for ROCm). MI250X has **2×64 GB HBM2e (128 GB total)** per node →
trains 70B models in float16 without offloading.

### Phase 1 — Synthetic Data Generation (Months 1–2, **2,500 GPU-h**)

| Dataset | Method | Volume | GPU-Hours |
|---|---|---|---|
| Query → Complexity labels | Llama-3.3-70B batch inference on MI250X | 500K pairs | 600 h |
| Query → Expert-category labels | Llama-3.3-70B + existing template definitions | 300K pairs | 400 h |
| (Query, Plan-JSON, Judge-Score) triples | Qwen3.6:35b as teacher, GAIA as gold standard | 200K triples | 800 h |
| (State, Action, Reward) for RL | Replay benchmark logs with simulated judge reward | 500K transitions | 200 h |
| DPO preference pairs (planner) | Two plans per query → judge ranks, builds preferred/rejected | 100K pairs | 500 h |

**Quality assurance:** Each data batch validated by a separate frontier judge (`judge_prompt`
pattern from templates). GAIA-2023-Validation held out as untouched test set.

### Phase 2 — Encoder/Classifier Training (Month 2, **800 GPU-h**)

- **Complexity classifier:** DeBERTa-v3-small, HuggingFace Trainer, bfloat16, lr=3e-5, 10 epochs → **200 GPU-h**
- **Routing classifier (semantic router):** `paraphrase-multilingual-MiniLM-L12-v2`, Triplet Loss, batch=512 → **300 GPU-h**
- **Reward model (for Phase 5):** DeBERTa-v3-large fine-tuned on (Plan, GAIA-Correct: bool) → **300 GPU-h**

### Phase 3 — Planner SFT + DPO (Months 2–4, **9,000 GPU-h**)

```
Base model:    Qwen2.5-1.5B (best benchmark performance in 1B–2B segment)
Context:       4096 tokens (planner prompt ~2000–3000 tokens + output ~200 tokens)
Batch size:    128 (bfloat16, gradient checkpointing)

SFT Phase 1 (teacher forcing on planner outputs):
  Dataset:     200K (Prompt, JSON-Plan) pairs from Qwen3.6:35b teacher
  3 epochs, lr=2e-4, cosine schedule, AdamW
  Budget:      3,000 GPU-h

DPO Phase 2 (Direct Preference Optimization):
  Dataset:     100K (Prompt, Preferred-Plan, Rejected-Plan) triples
  β=0.1, lr=5e-5, 2 epochs
  Budget:      2,000 GPU-h

Ablations (model sizes, hyperparameters, data quality):
  SmolLM2-1.7B as alternative  → 2,000 GPU-h
  Qwen2.5-3B as upper bound    → 2,000 GPU-h
```

**Target metric:** GAIA plan quality score ≥90% of 35B teacher at 1/20 inference cost.

### Phase 4 — Offline RL Routing Policy (Months 4–5, **3,500 GPU-h**)

```
Method:     Decision Transformer (offline RL) on historical benchmark logs
            Alternative: Behavior Cloning + Advantage Weighted Regression (AWR)
State:      [complexity_embed(32), query_embed(32), cache_distance(1), load_vector(N), bandit_context(8)]
Action:     4-dim discrete {research, graphrag} × {on, off}
Reward:     judge_confidence_score × (1 - latency_normalized) - 0.1 × cost_per_expert_call
Training:   3 epochs on 500K (State, Action, Reward, Next-State) transitions
Budget:     2,000 GPU-h (Transformer) + 1,500 GPU-h (ablations, online eval on GAIA)
```

### Phase 5 — Planner RLHF Fine-Tuning (Months 5–6, **2,200 GPU-h**)

```
Reward model from Phase 2 (DeBERTa-v3-large) as reward signal
PPO on Qwen2.5-1.5B (best model from Phase 3)
  KL-penalty: β=0.05 (conservative, prevents reward hacking)
  1 epoch, 50K queries from GAIA + own benchmark queries
Budget: 2,200 GPU-h
```

### Budget Summary

| Phase | Content | GPU-Hours |
|---|---|---|
| 1 | Data synthesis (frontier batch inference) | 2,500 |
| 2 | Encoder/classifier + reward model | 800 |
| 3 | Planner SFT + DPO + ablations | 9,000 |
| 4 | Offline RL routing policy | 3,500 |
| 5 | Planner RLHF | 2,200 |
| **Total** | | **18,000 h** ✓ |

---

## 4. Export and Inference Format for x86_64 Legacy CPUs

**Target hardware:** x86_64 with AVX2 (Haswell+) or AVX-512 (Skylake-SP+), no dedicated
inference accelerator.

| Model | Format | Quantization | Compile Flags | CPU Latency | Size |
|---|---|---|---|---|---|
| Planner Qwen2.5-1.5B | **GGUF** | **Q4_K_M** (4.3 bit/weight) | `llama.cpp: -DLLAMA_AVX512=ON -DLLAMA_AVX512_BF16=ON` | 15–25 tok/s (Xeon Skylake) | **~1.1 GB** |
| Planner (alternative) | ONNX | INT8 Dynamic Quant via ORT | `ort.SessionOptions: enable_mem_pattern=True` | 20–40 tok/s (ORT optimized) | ~800 MB |
| Complexity classifier | **ONNX** | **INT8 Dynamic** (22M params) | ORT 1.18+ CPUExecutionProvider, AVX2 | **3–6 ms** | ~22 MB |
| Semantic router embedding | **ONNX** | **FP16** | ORT with `graph_optimization_level=ORT_ENABLE_ALL` | **8–12 ms** | ~44 MB |
| RL routing policy MLP | ONNX | FP32 (trivial, <1M params) | Standard ORT | **<1 ms** | <5 MB |
| XGBoost node ranker | ONNX | FP32 | XGBoost ≥2.0 native ONNX export | **<0.3 ms** | <2 MB |

### Quantization Rationale

- **Q4_K_M (GGUF):** K-Quant with mixed 4-bit/6-bit quantization in critical layers. Retains
  ~97–98% model quality at 4× compression. Standard for llama.cpp CPU deployment.
- **INT8 Dynamic (ONNX):** Weights INT8, activations FP32. No calibration dataset required.
  2–3× speedup on AVX2 vs FP32 via VNNI instructions on Intel CPUs.
- **FP16 for embeddings:** INT8 quant of sentence encoders measurably degrades cosine similarity
  (ΔnDCG ~2–4%); FP16 is the safe compromise.

### OpenVINO as Alternative for Intel Xeon Fleets

- ONNX → OpenVINO IR via `mo --input_model model.onnx --data_type FP16`
- INT8 via POT (Post-Training Optimization Toolkit) without quality loss on encoder models
- Latency advantage: +30–50% over ORT on Intel Xeon Scalable (Cooper Lake with AVX-512 VNNI)

---

## 5. New Capabilities After Successful Training

| # | Capability | Current Code Limitation | New Capability |
|---|---|---|---|
| 1 | **Offline planner without remote LLM** | `planner.py:566` — `_invoke_llm_with_fallback` to external LLM | GGUF planner runs locally on CPU, no network, no GPU node. Enables air-gap deployment |
| 2 | **Planner latency <100ms** | 2–5s on remote call; 1800s Valkey cache TTL as workaround | Local GGUF inference: 200–500ms for complete plan. Cache dependency drops drastically |
| 3 | **Semantic complexity recognition** | `complexity_estimator.py` — token count + regex only | Encoder distinguishes "solve x=2" (trivial) from "solve nonlinear PDE" (complex), keyword-independent |
| 4 | **Cold-start-free adaptive routing** | `routing_bandit.py:87` — `ROUTING_BANDIT_MIN_DATAPOINTS` guard | RL policy generalizes to new query types via transfer, no minimum data volume before activation |
| 5 | **Continuous policy updates without redeployment** | Thompson bandit is stateless except Redis counters; new categories = new cold starts | RL policy can be incrementally fine-tuned with new `(State, Reward)` data (LoRA) |
| 6 | **Routing cost-quality Pareto optimization** | No explicit cost model in routing | RL agent minimizes `latency + cost_per_token` under quality constraint from judge feedback |
| 7 | **Multilingual semantic routing stability** | ChromaDB prototypes are language-dependent; German queries match English prototypes poorly | `paraphrase-multilingual-MiniLM-L12-v2` fine-tuned → equal routing quality on DE/EN/FR |
| 8 | **Confidence score per plan** | Planner LLM outputs no confidence signal | Distilled SLM can quantify planning uncertainty via temperature sampling + token log-probs → fallback trigger when conf < threshold |

### What Does Not Change (Intentional Non-Substitution)

- `services/routing.py` — template binding remains deterministic (admin-configured, no ML)
- `pipeline/logic_types.py` — fuzzy/paraconsistent types remain as formal correctness guarantees
- `_select_node` sticky session logic — remains deterministic for UX consistency
- Judge node — remains large LLM (35B+); quality bottleneck is not compressible

### Configuration Switch Design

Following the existing pattern of `TRIVIAL_FAST_PATH_ENABLED` (`planner.py:223`), each new
AI component should be gated by a feature flag:

```
PLANNER_MODE={"llm", "slm_local", "hybrid"}
```

Hybrid mode: SLM as first pass, LLM fallback when confidence < threshold. No hard switch.

---

## 6. Dynamic Template Synthesizer

### Concept

**Current system:** Template = **user-profile binding**
```
Admin creates template → assigns to user → same prompt always gets same configuration
```

**New (optional, feature-flagged):** Template = **prompt-adaptive synthesis**
```
User sends prompt → synthesizer analyzes → generates optimal expert dict → pipeline
```

The synthesizer does not replace static templates but extends them as an optional mode.
Admin configuration overhead for special cases is eliminated.

### Code Integration Point

**Current flow:**
```
main.py → _resolve_user_experts() → user_experts dict → stream_response() → Graph
                                            ↑ loaded statically from DB
```

**New flow with synthesizer:**
```
main.py → [Dynamic Template Synthesizer] → user_experts dict → stream_response() → Graph
                      ↑
          Prompt-adaptive generation
          (replaces or overlays DB template)
```

**Concrete code location (`main.py:1283–1347`):**
```python
# Today:
user_experts = _resolve_user_experts(permissions_json, override_tmpl_id, ...)

# With synthesizer (optional path):
if TEMPLATE_SYNTHESIS_ENABLED and not override_tmpl_id:
    user_experts = await _synthesize_template(user_input, available_categories)
else:
    user_experts = _resolve_user_experts(permissions_json, ...)
```

### Synthesizer Output Structure

Identical structure to `_resolve_user_experts()` in `routing.py:72–116`:

```json
{
  "legal_advisor": [
    {
      "model": "qwen3.6:35b",
      "endpoint": "n04-rtx",
      "forced": true,
      "_system_prompt": "Specialist for §613a BGB business transfers and employment law. Focus on dismissal protection and collective agreement continuity.",
      "_tier": 2
    }
  ],
  "code_reviewer": [
    {
      "model": "qwen3-coder:30b",
      "endpoint": "n04-rtx",
      "_system_prompt": "Python FastAPI Security Review. Check for OWASP Top 10, async patterns, dependency injection vulnerabilities.",
      "_tier": 2
    }
  ]
}
```

Additionally synthesized (mapped to `_resolve_template_prompts()` output, `routing.py:128–220`):

```json
{
  "planner_prompt": "Prioritize §-precise research before implementation. Combine legal review + code security.",
  "judge_prompt": "Evaluate by: legal correctness, GDPR compliance, code security.",
  "enable_graphrag": true,
  "enable_web_research": false,
  "force_think": true
}
```

### Three Implementation Tiers

#### Tier 1 — Retrieval-Augmented Template Assembly (RATA)
**No generative training required, deployable immediately**

```
Prompt
  ↓
DeBERTa classifier → {legal: 0.91, code: 0.78, technical: 0.12}
  ↓
Threshold filter (>0.5) → ["legal_advisor", "code_reviewer"]
  ↓
FAISS search: for each category retrieve top-3 semantically similar proven system prompts
              from a curated library (curated + GAIA-labelled)
  ↓
Top-1 system prompt per category → composite template
```

- Routing flags (`enable_graphrag`, `web_research`) → derived from category classifier (rule-based: legal → graphrag=True)
- Latency: **10–25ms** (classifier + FAISS)
- No generative model required

#### Tier 2 — SLM Template Generator (primary EuroHPC target)
**Generates system prompts adapted to the concrete prompt context**

```
Prompt
  ↓
Qwen2.5-1.5B fine-tuned → JSON template (fully structured)
  ↓
Validation (known categories, model endpoints via config)
  ↓
Composite template
```

- Model generates contextually refined system prompts instead of generic category defaults
- Latency: **200–500ms** (GGUF Q4_K_M on CPU) — bypassed by cache when query repeated
- **Key value-add**: Prompt "Explain Docker for a beginner" → `_system_prompt: "Explain step-by-step, use analogies, avoid jargon"` vs. "Optimize our Docker Compose security" → `_system_prompt: "Senior Security Engineer, focus on non-root user, read-only filesystems, secret management"`

#### Tier 3 — Hierarchical Synthesizer (maximum quality)

```
Prompt
  ↓
Tier 1: DeBERTa categorizes quickly
  ↓
Tier 2: SLM refinement of system prompts for selected categories only
  ↓
Tier 3: Optional RL policy adjusts routing flags based on historical performance
```

### EuroHPC Training Strategy for Synthesizer

#### Training Data Generation (~2,000 GPU-hours re-allocated from Phase 1)

**Gold standard pairs:** `(Prompt, Optimal_Template_JSON)`

```
Prompt:  "Our FastAPI app transfers user data to US service providers under Art. 46 GDPR."
→ Optimal template:
  {
    "legal_advisor": {"_system_prompt": "GDPR Art. 46/47, standard contractual clauses,
                       Schrems-II, Transfer Impact Assessment"},
    "code_reviewer": {"_system_prompt": "FastAPI data protection audit: check consents,
                       data minimization, encryption in transit"},
    "enable_graphrag": true,
    "enable_web_research": true,
    "judge_prompt": "Priority: legal correctness > code correctness"
  }
```

**Generation method:**
1. Frontier LLM (Llama-3.3-70B on MI250X) generates 300K `(Prompt, Template)` pairs
2. Existing GAIA logs: prompts that led to **high judge confidence** → extract their effective routing decisions as gold standard
3. Own benchmark logs (Prometheus/Grafana): `(input, expert_calls_made, judge_confidence)` → reconstruct templates retroactively

**DPO preference pairs for system prompt quality:**
```
Prompt: "Explain Kubernetes Networking to a junior developer"

Preferred:  _system_prompt = "Explain with analogies (postman = Pod, street = Service),
             step-by-step, avoid CIDR/iptables details, use diagrams"
Rejected:   _system_prompt = "You are a Kubernetes expert. Answer technical questions."
```

Evaluation criterion for Preferred/Rejected: **judge score** after full pipeline execution
with the respective preferred vs. rejected system prompt.

#### Training Budget (within overall Phase 3 budget)

| Step | Budget |
|---|---|
| Data synthesis (300K template pairs via frontier LLM) | 800 GPU-h |
| DPO pair generation (100K pairs, judge-scored) | 600 GPU-h |
| SFT Qwen2.5-1.5B on template generation | 1,500 GPU-h |
| DPO fine-tuning | 800 GPU-h |
| Ablations (category count, prompt length, retrieval vs. generation) | 500 GPU-h |
| **Total** | **~4,200 GPU-h** |

These 4,200 hours are drawn from the Phase 3 budget — planner SFT and template synthesizer
share the Qwen2.5-1.5B base checkpoint.

### Impact: What Changes Fundamentally

| Dimension | Today | With Synthesizer |
|---|---|---|
| System prompt specificity | Generic per category ("You are a code reviewer") | Prompt-contextual ("You are a Python async performance reviewer for FastAPI focusing on DB bottlenecks") |
| Template management overhead | Admin must maintain 50+ templates for every scenario combination | One base template per user group; synthesizer handles fine granularity |
| Cold start for new domains | New domain → admin must build new template | Synthesizer generalizes to unknown domains via transfer learning |
| Multi-domain prompts | Require manually crafted combination templates | Automatically provided with matching, coordinated system prompts per expert |
| User-specific adaptation | Template binding = coarse role segmentation | Synthesizer can incorporate user history from cross-session memory → personalized expert configuration |
| Routing flags (graphrag, web) | Set statically in template | Decided prompt-adaptively by synthesizer |

### Relationship to Planner (Synergy, Not Competition)

```
Synthesizer:  "WHO" and "HOW" (which experts, with what focus)
Planner:      "WHAT" and "IN WHAT ORDER" (which subtasks, which tools)
```

A distilled planner (Phase 3) and a synthesizer can share the same base checkpoint
`Qwen2.5-1.5B` — different LoRA adapters for different tasks. This reduces the VRAM
footprint in production deployment: one model, two adapters, sequentially loaded in
<50ms via llama.cpp hot-swap.

---

## Appendix: Key Config Constants Referenced

| Constant | File | Value | Role in Decision |
|---|---|---|---|
| `CACHE_HIT_THRESHOLD` | `config.py:178` | 0.15 | ChromaDB L1 cache hit gate |
| `ROUTE_THRESHOLD` | `config.py:181` | 0.18 | Semantic router direct-path gate |
| `ROUTE_GAP` | `config.py:182` | 0.10 | Minimum confidence gap for routing |
| `EXPERT_MIN_SCORE` | `config.py:223` | 0.3 | Thompson sampling minimum usable score |
| `ROUTING_BANDIT_MIN_DATAPOINTS` | `config.py` | 10 | Cold-start guard for bandit gates |
| `TRIVIAL_FAST_PATH_ENABLED` | `config.py` | False | Reference pattern for new feature flags |
| `THOMPSON_SAMPLING_ENABLED` | `config.py` | True | Expert scoring mode |
| `JUDGE_REFINE_MAX_ROUNDS` | `config.py` | — | Judge refinement loop cap |
| `PLANNER_RETRIES` | `config.py` | 2 | Planner JSON parse retry count |
| `_TRIVIAL_TOKEN_MAX` | `complexity_estimator.py:28` | 15 | Word count threshold for trivial |
| `_COMPLEX_TOKEN_MIN` | `complexity_estimator.py:29` | 80 | Word count threshold for complex |
| `_AIC_TRIVIAL_FLOOR` | `complexity_estimator.py:34` | 0.55 | Kolmogorov compressibility → trivial |
| `_AIC_COMPLEX_CEILING` | `complexity_estimator.py:35` | 0.15 | Kolmogorov compressibility → complex |

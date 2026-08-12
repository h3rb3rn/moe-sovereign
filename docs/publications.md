# Publications & Papers

All publications documenting the MoE Sovereign project — available directly
from this repository.

---

## Technical Whitepaper (v2.8 · August 2026)

The full technical whitepaper covers the complete system architecture:
formal logic layer, GraphRAG pipeline, self-correction loop, MCP precision
tools, evaluation, and sovereignty principles.

| Language | Download | Pages |
|----------|----------|-------|
| **English** | [moe-sovereign-whitepaper-en-v2.8.pdf](assets/papers/moe-sovereign-whitepaper-en-v2.8.pdf) | 116 |
| **Deutsch** | [moe-sovereign-whitepaper-de-v2.8.pdf](assets/papers/moe-sovereign-whitepaper-de-v2.8.pdf) | 116 |

**v2.8 changes (August 2026):**

- §8 Attribution table: corrected de Vries (2007) author name (Andreas, not Madelon);
  added Belnap (1977) four-valued semantics {T, F, B, N} as separate entry
- §10.1 Judge node: activated Constitutional AI citation (Bai et al. 2022)
- §10.7 Agentic loop: activated Self-Refine citation (Madaan et al. 2023)
- §10.8 Paraconsistent resolution: separated algebraic motivation (de Vries 2007)
  from the formal semantics (Belnap 1977)
- MCP tool count corrected: 28 → 51 deterministic tools
- Full DE section mirroring all EN corrections

---

## LUMI-G Training Paper (August 2026)

Empirical practitioner's report on the EuroHPC LUMI-G training campaign
for the Judge and Planner models. Documents 20+ SLURM job iterations,
OOM root causes, resume bugs, and the distillation pipeline.

| Format | Link | Pages |
|--------|------|-------|
| **HTML** (this site) | [system/lumi_training_paper/](system/lumi_training_paper.md) | — |
| **PDF** | [moe-sovereign-lumi-paper.pdf](assets/papers/moe-sovereign-lumi-paper.pdf) | 23 |
| **DE** | [system/de/lumi_training_paper/](system/de/lumi_training_paper.md) | — |

**Key findings:** Judge deployed 2026-07-19; `max_seq_len` 8192→4096 yields
only **1.2× speedup** with `--no-packing`; `expandable_segments` silently
inactive on LUMI-G ROCm build (July 2026).

---

## arXiv Paper (IEEE format)

Short paper in IEEE two-column format covering the formal routing and
Optimal Transport layer — suitable for submission.

| Format | Link |
|--------|------|
| PDF | [arxiv-paper.pdf](https://moe-sovereign.org/arxiv-paper.pdf) |

---

## Citation

```bibtex
@techreport{moe_sovereign_wp,
  author    = {Horn, Philipp},
  title     = {Sovereign MoE -- A Self-Hosted Multi-Model Orchestrator
               with Template-Based Expert Routing},
  year      = {2026},
  version   = {2.8},
  url       = {https://docs.moe-sovereign.org/publications/},
  note      = {EuroHPC Grant EHPC-DEV-2026D06-XXX}
}
```

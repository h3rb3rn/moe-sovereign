# Intelligence & Learning

This section documents the two mechanisms by which MoE Sovereign goes beyond simple retrieval and generation: **Causal Reasoning** and **Context Extension**.

Both address fundamental limitations of small local LLMs — not by replacing them, but by restructuring how information flows around them.

---

## Contents

| Document | What it covers |
|---|---|
| [Causal Learning Loop](causal_learning.md) | How the system learns world-rules and procedural dependencies from its own responses, stores them in Neo4j, and surfaces them back into future queries |
| [Context Extension](context_extension.md) | How the MoE architecture effectively extends the usable context window for coding agents and large-document workflows, despite the small native context of local LLMs |
| [Graph-basierte Wissensakkumulation](compounding_knowledge.md) | How the system actively maintains its own knowledge base: synthesis persistence (novel insights → `:Synthesis` Neo4j nodes) and graph linting (orphan cleanup + contradiction resolution via background LLM) |
| [Memory Palace](memory_palace.md) | Domain-scoped retrieval via metadata filters, isolated expert memory with `expert_domain` tagging in ChromaDB and Neo4j, and Claude Code auto-save hooks that persist session knowledge before context loss |
| [CLI Agent Integration](cli_agent_integration.md) | Architectural analysis of how execution-loop agents (Aider, Open Interpreter) and infra-orchestrators (Hermes, Continue.dev) leverage all four MoE core components simultaneously — with delta table, Mermaid data-flow diagrams, and measured thresholds from the implementation |
| [7B Ensemble Capability](7b_ensemble_capability.md) | Measured benchmark results showing that 8 domain-specialist 7–9B models on legacy Tesla M10 hardware achieve GPT-4o mini class performance (6.11/10 on MoE-Eval) with full data sovereignty — overnight stability run, per-category analysis, and comparison to public cloud models |
| [Agentic Re-Planning Loop](agentic_loop.md) | How MoE Sovereign autonomously detects knowledge gaps after synthesis and re-plans with targeted tool calls — enabling multi-step GAIA L3-class reasoning without manual prompt chaining |

---

## Design Philosophy

Local LLMs typically have two hard constraints:

1. **Small context windows** (4k–32k tokens) make it impossible to fit entire codebases, long documents, or rich conversation histories into a single prompt.
2. **No persistent memory** across requests — every call starts cold, with no knowledge of previous answers.

MoE Sovereign addresses both constraints architecturally:

- **Context** is distributed: multiple experts each receive a focused subset of relevant information, rather than one model receiving everything at once.
- **Memory** is structured: factual and procedural knowledge extracted from responses is stored in Neo4j and retrieved in future requests as structured `[Knowledge Graph]` and `[Procedural Requirements]` blocks.
- **Insights compound:** Novel multi-source syntheses produced by the merger are captured as `:Synthesis` nodes, enriching future graph traversals. A background linting process resolves contradictions and removes orphaned nodes.
- **Memory is domain-aware:** Each piece of stored knowledge is tagged with its source expert (`expert_domain`). The planner can attach metadata filters to its plan so that downstream retrieval is scoped to the relevant namespace — a `code_reviewer` query does not surface medical facts. Session-level knowledge from external tools (Claude Code) flows into the same pipeline via the `/v1/memory/ingest` endpoint and hook scripts.

The result is a system where **the collective context is larger than any individual LLM can hold** — knowledge compounds over time, is organised by domain, and persists across tool sessions rather than being lost after each response.

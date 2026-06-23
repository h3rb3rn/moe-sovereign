# System Documentation

This section contains the technical system documentation for **Sovereign MoE** — the fully self-hosted Multi-Model LLM-System.

---

## Contents

| Page | Description |
|------|-------------|
| [Overview & System Docs](overview.md) | Complete system documentation: hardware, pipeline, experts, API, deployment |
| [Architecture](architecture.md) | LangGraph pipeline, service topology, caching architecture, expert routing |
| [Kafka Event Streaming](kafka.md) | Kafka architecture, topics, message schemas, HowTo admin & dev |
| [Docker Services](services.md) | Service overview, ports, volumes, operational commands |
| [HABE 2.0 Operations Manual](habe_operations_manual.md) | Vector Symbolic Architecture (VSA), hierarchical graph compilation, virtual prefix attention modulation |
| [Advice-Taker Rule-Engine](advice_taker_operations_manual.md) | John McCarthy's rule-engine, Jaccard 3-gram semantic matching, regex parameter extraction |
| [Eurisko Heuristic Breeder](eurisko_operations_manual.md) | Self-referential template optimizer, roulette-wheel selection, feedback loops, crossover |
| [Dynamic System Prompts](dynamic_prompts_operations_manual.md) | Meta-prompter architecture, zero-latency structured fallback pathways |

---

## Quick Overview

The system consists of the following core components:

- **LangGraph Orchestrator** (Port 8002) — FastAPI + LangGraph pipeline, OpenAI API-compatible
- **MCP Precision Tools Server** (Port 8003) — 16 deterministic calculation tools
- **Admin UI** (Port 8088) — Web configuration, user management, monitoring
- **ChromaDB** (Port 8001) — Vector cache for semantic caching
- **Valkey** (Port 6379) — LangGraph checkpoints, expert performance scores, user cache
- **Neo4j** (Port 7687) — Knowledge graph (GraphRAG)
- **Kafka** (Port 9092) — Decoupled event streaming for ingest and audit logging
- **Prometheus** (Port 9090) + **Grafana** (Port 3001) — Observability

# System Documentation

This section contains the technical system documentation for **Sovereign MoE** — the fully self-hosted Mixture-of-Experts LLM system.

---

## Contents

| Page | Description |
|------|-------------|
| [Overview & System Docs](overview.md) | Complete system documentation: hardware, pipeline, experts, API, deployment |
| [Architecture](architecture.md) | LangGraph pipeline, service topology, caching architecture, expert routing |
| [Kafka Event Streaming](kafka.md) | Kafka architecture, topics, message schemas, HowTo admin & dev |
| [Docker Services](services.md) | Service overview, ports, volumes, operational commands |

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

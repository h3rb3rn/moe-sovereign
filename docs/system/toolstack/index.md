# Tool Stack Overview

Sovereign MoE combines several specialized open-source components into a coherent orchestration stack. Each component solves a specific problem that could not be solved with a single, monolithic LLM system.

## Architecture Diagram

```mermaid
flowchart TD
    CLIENT["Client\n(Open WebUI · curl · SDK)"]

    subgraph ORCH["LangGraph Orchestrator · Port 8002"]
        direction TB
        CACHE["cache_lookup\n(ChromaDB Semantic)"]
        PLAN["planner\n(Judge-LLM)"]
        WORKERS["workers\n(Expert LLMs)"]
        RESEARCH["research\n(SearXNG)"]
        MATH["math\n(SymPy internal)"]
        MCP_N["mcp\n(Precision Tools)"]
        GRAPH_N["graph_rag\n(Neo4j)"]
        MERGER["merger\n(Judge-LLM)"]
        THINKING["thinking\n(CoT, conditional)"]
        CRITIC["critic\n(fact-check)"]
    end

    subgraph INFERENCE["Inference Servers (configured via Admin UI)"]
        direction LR
        SRV1["Inference Server 1\nOllama-compatible"]
        SRV2["Inference Server 2\noptional"]
    end

    subgraph PERSIST["Persistence Layer"]
        REDIS[("Valkey\nPort 6379\nScoring · Session Cache")]
        CHROMA[("ChromaDB\nPort 8001\nSemantic Cache")]
        NEO4J[("Neo4j\nPort 7687\nKnowledge Graph")]
    end

    subgraph STREAMING["Async Streaming"]
        KAFKA[("Kafka\nPort 9092\nmoe.ingest · moe.requests · moe.feedback")]
        KCONS["Kafka Consumer\n→ Neo4j Ingest"]
        KAFKA --> KCONS --> NEO4J
    end

    MCP_SERVER["MCP Precision Tools\nPort 8003\n16 deterministic tools"]
    SEARXNG["SearXNG\nPort 8888\nPrivate web search"]

    CLIENT -->|"POST /v1/chat/completions"| CACHE
    CACHE -->|"Hit"| CLIENT
    CACHE -->|"Miss"| PLAN
    PLAN --> WORKERS & RESEARCH & MATH & MCP_N & GRAPH_N
    WORKERS --> INFERENCE
    PLAN --> INFERENCE
    MERGER --> INFERENCE
    RESEARCH --> SEARXNG
    MCP_N --> MCP_SERVER
    GRAPH_N --> NEO4J
    WORKERS -->|"Confidence < threshold"| THINKING
    THINKING --> MERGER
    WORKERS & RESEARCH & MATH & MCP_N & GRAPH_N --> MERGER
    MERGER --> CRITIC --> CLIENT
    MERGER --> CHROMA
    MERGER --> REDIS
    MERGER -->|"moe.ingest + moe.requests"| KAFKA
```

## Component Overview

| Component | Role | Port | Documentation |
|---|---|---|---|
| **LangGraph** | Orchestration, parallel fan-out, state management | internal | [langgraph.md](langgraph.md) |
| **Ollama** | Multi-node LLM inference | 11434 | [ollama_cluster.md](ollama_cluster.md) |
| **Neo4j** | Temporal GraphRAG, knowledge graph | 7687 | [graphrag_neo4j.md](graphrag_neo4j.md) |
| **Valkey** | Expert scoring, session cache | 6379 | — |
| **ChromaDB** | Semantic response cache | 8001 | — |
| **Kafka** | Async ingest buffer, audit log | 9092 | [Kafka docs](../kafka.md) |
| **SearXNG** | Private web search (no Google tracking) | 8888 | — |
| **MCP Server** | 16 deterministic precision tools | 8003 | [mcp_tools.md](mcp_tools.md) |

## Design Principles

**Determinism over LLM estimation** — calculations, hashes, date operations, and network subnet calculations always run through the MCP server, never through a language model.

**Decoupling via Kafka** — the HTTP response path and data persistence are completely separated. A Kafka outage blocks no responses, only later graph learning.

**Heterogeneous hardware** — Ollama abstracts different GPU generations (consumer cards to enterprise Tesla) behind a unified OpenAI API. Inference servers are configured via Admin UI → Servers, with priority routing weighted by availability.

**No vendor lock-in** — all components are self-hosted. SearXNG instead of Google, Ollama instead of OpenAI, Neo4j Community instead of vector-based cloud services.

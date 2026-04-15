# MoE Sovereign

Self-hosted Multi-Model Orchestration system. Routes queries to specialized local LLMs, enriches context via Neo4j knowledge graph and web search, synthesizes answers with a judge LLM. OpenAI-compatible API — works with Claude Code, Continue.dev, Open Code, and any OpenAI-compatible client.

## Stack

| Component | Version | Role |
|---|---|---|
| LangGraph | 0.2.x | Pipeline orchestration |
| Ollama | latest | Local LLM inference |
| ChromaDB | latest | Semantic vector cache |
| Valkey | latest | Plan cache, scoring, API key cache |
| Neo4j | 5 Community | Knowledge graph (GraphRAG) |
| Apache Kafka | 7.7.0 (KRaft) | Event streaming & async learning |
| Prometheus + Grafana | latest | Metrics & dashboards |
| FastAPI + uvicorn | latest | HTTP API layer |

## Prerequisites

- Docker + Docker Compose v2
- GPU with ≥16 GB VRAM (RTX recommended) for expert models
- Ollama instance(s) with models pulled
- SearXNG instance for web research (optional)

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env: set URL_RTX, SEARXNG_URL, GF_SECURITY_ADMIN_PASSWORD, ...

# 2. Start all services
sudo docker compose up -d

# 3. Open Admin UI
open http://localhost:8088
```

## Service URLs

| Service | URL | Purpose |
|---|---|---|
| **Orchestrator API** | `http://localhost:8002/v1` | Main OpenAI-compatible endpoint |
| **Admin UI** | `http://localhost:8088` | Configuration & monitoring |
| **Grafana** | `http://localhost:3001` | Metrics dashboards |
| **Prometheus** | `http://localhost:9090` | Raw metrics |
| **Neo4j Browser** | `http://localhost:7474` | Knowledge graph explorer |
| **ChromaDB** | `http://localhost:8001` | Vector store |
| **MCP Server** | `http://localhost:8003` | Precision tools |

## Connect with Claude Code

```bash
# ~/.claude/settings.json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:8002/v1",
    "ANTHROPIC_API_KEY": "any-string"
  }
}
```

Or configure a profile via Admin UI → Profiles, then activate it.

## Documentation

| Document | Format | Audience |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Markdown + Mermaid | Developers — pipeline, topology, caching, config reference |
| [HANDOUT.md](HANDOUT.md) | Markdown | Users — modes, experts, skills, best practices |
| [handbook/](handbook/) | LaTeX | Extended handbook (compile with `bash compile.sh`) |
| [best-practices/](best-practices/) | LaTeX | Deployment, model selection, security |
| [whitepaper/](whitepaper/) | LaTeX | Academic whitepaper with references |

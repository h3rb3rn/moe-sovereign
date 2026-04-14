# Contributing to Sovereign MoE

Thank you for your interest in contributing to **Sovereign MoE** — a self-hosted, modular Mixture-of-Experts LLM orchestration system. This guide explains how to contribute effectively while keeping the core system stable.

---

## 1. Project Philosophy

Sovereign MoE is designed as a **modular LLM laboratory**. The architecture is intentionally split into a stable orchestration core and loosely coupled extension points:

| Layer | Stability | Where to contribute |
|---|---|---|
| `main.py` — LangGraph Orchestrator | Stable core | Via GitHub Issue first |
| `mcp_server/` — Precision Tools | Extension point | Direct PRs welcome |
| `docs/experts/` + `prompts/` — Templates | Extension point | Direct PRs welcome |
| `graph_rag/` — GraphRAG Manager | Stable core | Via GitHub Issue first |

**Core changes to `main.py` require a GitHub Issue first.** Open an issue describing the problem, proposed solution, and any affected pipeline nodes before writing code. This prevents duplicate work and protects system stability across the heterogeneous GPU cluster.

---

## 2. Plug-and-Play Contributions (Preferred)

These are the preferred ways to contribute — they extend the system without touching the core orchestrator.

### 2a. New MCP Precision Tools

MCP Tools provide deterministic, hallucination-free computation. Adding a new tool is self-contained within `mcp_server/server.py`.

**Steps:**

1. Add your tool function to `mcp_server/server.py`:

```python
async def my_tool(args: dict) -> dict:
    """
    Brief description of what this tool computes.
    Args: {"input_param": "description"}
    Returns: {"result": ..., "unit": "optional"}
    """
    try:
        value = args.get("input_param")
        # ... deterministic computation, no LLM calls ...
        return {"result": computed_value}
    except Exception as e:
        return {"error": str(e)}
```

2. Register the tool in the `TOOLS` dict and the `/tools` endpoint description.

3. Add a test case in your PR description demonstrating input → output.

4. Document the tool in `docs/system/toolstack/mcp_tools.md`.

**Good candidates:** domain converters, protocol parsers, deterministic validators, cryptographic utilities, scientific constants lookup.

### 2b. New LLM Expert Templates

Expert templates define how Sovereign MoE routes and prompts specialized LLM calls. There are two files per expert:

| File | Purpose |
|---|---|
| `prompts/systemprompt/<expert_name>.md` | The system prompt defining the expert's persona and behavior |
| `docs/experts/<expert_name>.md` | Documentation shown in the admin UI and MkDocs |

**Steps:**

1. Copy `prompts/systemprompt/general.md` as a starting point.
2. Create `prompts/systemprompt/<your_expert>.md` — define scope, output format, and constraints clearly.
3. Create `docs/experts/<your_expert>.md` — document: purpose, example queries, recommended models.
4. Reference the existing `agentic_coder.md` pair as a worked example.

**Good candidates:** domain-specific experts (legal, medical, financial), language-specific experts, task-specific processors (summarizer, extractor, classifier).

---

## 3. Code Standards

All Python code in this repository must follow these standards:

### Type Hints

Use type hints for all function signatures:

```python
async def invoke_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    ...
```

### Async/Await for I/O

All I/O operations (HTTP calls, database queries, file reads) must use `async/await`. Blocking calls inside async functions degrade the entire pipeline:

```python
# Good
async with httpx.AsyncClient() as client:
    response = await client.post(url, json=payload)

# Bad — blocks the event loop
response = requests.post(url, json=payload)
```

### Error Handling

Wrap all external API calls in `try/except` to prevent cascade failures. Return structured error dicts rather than raising:

```python
try:
    result = await call_external_api(params)
    return {"status": "ok", "data": result}
except httpx.TimeoutException:
    return {"status": "error", "error": "timeout", "tool": tool_name}
except Exception as e:
    logger.error(f"Tool {tool_name} failed: {e}")
    return {"status": "error", "error": str(e)}
```

### No Hardcoded Configuration

Read all configuration from environment variables. Never hardcode IPs, ports, model names, or credentials:

```python
# Good
ollama_url = os.getenv("INFERENCE_SERVER_URL", "http://localhost:11434")

# Bad
ollama_url = "http://10.0.0.5:11434"
```

---

## 4. Pull Request Process

### Before Opening a PR

- [ ] For core changes: a linked GitHub Issue exists and has been discussed
- [ ] For MCP tools: the tool is fully self-contained and has no external state
- [ ] For expert templates: the system prompt has been tested against at least 5 representative queries

### PR Description Template

```markdown
## What does this PR do?
Brief description (1-3 sentences).

## Type of change
- [ ] New MCP Tool (`mcp_server/`)
- [ ] New Expert Template (`prompts/` + `docs/experts/`)
- [ ] Bug fix (non-breaking)
- [ ] Core change (requires linked Issue)

## Testing
- Describe how you tested this (manual curl, unit test, example input/output)
- For MCP tools: include a sample `/invoke` request and response

## Documentation
- [ ] Relevant `.md` file created or updated
- [ ] `mkdocs.yml` updated if a new docs page was added
```

### Review Expectations

- MCP tools and templates are reviewed within 3–5 days.
- Core changes may take longer as they require testing on the physical GPU cluster.
- All PRs must pass the MkDocs build check (`mkdocs build --strict`) if documentation was changed.

---

## Getting Started Locally

The system requires Docker and a GPU with at least 8 GB VRAM for minimal operation:

```bash
# Clone and configure
git clone https://github.com/<org>/moe-infra.git
cd moe-infra
cp temp.env .env
# Edit .env: set CLUSTER_HARDWARE, Ollama URLs, Neo4j credentials

# Start core services
sudo docker compose up -d

# Verify
curl http://localhost:8002/health
curl http://localhost:8003/health
```

For full cluster setup (multi-node Ollama, Kafka, GPU routing), see [`docs/system/infrastructure.md`](docs/system/infrastructure.md).

---

## Questions?

Open a [GitHub Discussion](../../discussions) for architecture questions or ideas. For bugs, use [GitHub Issues](../../issues).

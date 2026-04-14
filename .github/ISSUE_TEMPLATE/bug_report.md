---
name: Bug Report
about: Something is not working as expected
title: '[BUG] '
labels: bug
assignees: ''
---

## Describe the Bug

A clear and concise description of what the bug is.

## Steps to Reproduce

1. ...
2. ...
3. See error

## Expected Behaviour

What you expected to happen.

## Actual Behaviour

What actually happened. Include the full error message if applicable.

## Environment

| Field | Value |
|---|---|
| OS | e.g. Debian 12 |
| Docker version | `docker --version` |
| Compose version | `docker compose version` |
| MoE Sovereign version / commit | `git rev-parse --short HEAD` |

## Hardware Setup

| Field | Value |
|---|---|
| GPU model(s) | e.g. NVIDIA RTX 3090, Tesla M10 |
| Total VRAM | e.g. 24 GB |
| Number of inference nodes | e.g. 2 |
| Inference backend | e.g. Ollama 0.3.x / LiteLLM / OpenAI API |

## Relevant Logs

```
# Run: sudo docker compose logs <service-name> --tail=100
# Common services: langgraph-app, moe-admin-ui, mcp-precision, neo4j-knowledge
```

Paste relevant log output here.

## Additional Context

Add any other context, screenshots, or configuration snippets that might help.

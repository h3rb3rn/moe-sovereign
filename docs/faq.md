# FAQ — MoE Sovereign Platform

## Table of Contents

1. [Claude Code & .bashrc Configuration](#1-claude-code-bashrc-configuration)
2. [API Keys — Create & Use](#2-api-keys-create-use)
3. [Authentication](#3-authentication)
4. [Request Modes (Models)](#4-request-modes-models)
5. [Token Budget & Costs](#5-token-budget-costs)
6. [Expert Templates](#6-expert-templates)
7. [Giving Feedback](#7-giving-feedback)
8. [Admin UI — Key Functions](#8-admin-ui-key-functions)
9. [MoE Portal & Web Interface](#9-moe-portal-web-interface)
10. [Best Practices](#10-best-practices)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Claude Code & .bashrc Configuration

### How should my `.bashrc` look to show the CC profiles?

Claude Code reads two environment variables to connect to the MoE API:

```bash
# ~/.bashrc or ~/.zshrc

# Use MoE API as Anthropic backend
export ANTHROPIC_BASE_URL=http://localhost:8002          # local
# export ANTHROPIC_BASE_URL=https://api.moe-sovereign.org  # external

# Your personal API key (from Admin UI → Users → API Keys)
export ANTHROPIC_API_KEY=moe-sk-<YOUR_KEY_HERE>
```

Then run `source ~/.bashrc` or restart the terminal.

### How are CC profiles (Claude Code Profiles) displayed and switched?

Profiles are managed in the **Admin UI** under **Users → CC Profiles**.
Each profile defines:

| Field | Meaning | Example value |
|------|-----------|--------------|
| `tool_model` | Local model for tool execution | `gemma4:31b` |
| `tool_endpoint` | GPU node | `N04-RTX` |
| `moe_mode` | Orchestration mode | `native` / `moe_orchestrated` / `moe_reasoning` |
| `tool_max_tokens` | Max output tokens per tool call | `8192` |
| `reasoning_max_tokens` | Max tokens for reasoning | `16384` |
| `system_prompt_prefix` | Additional system instructions | _(optional)_ |
| `stream_think` | Output thinking tokens in stream | `true` / `false` |

**Switch profile**: Admin UI → Users → CC Profiles → set desired profile to "Active".

### What is the difference between `moe_mode` values?

| Mode | Description | Latency |
|-------|-------------|--------|
| `native` | Claude Code → MoE API → directly to GPU (no MoE fanout) | low |
| `moe_orchestrated` | Request fully routed through MoE pipeline | medium–high |
| `moe_reasoning` | MoE pipeline with thinking node enabled | high |

**Recommendation**: `native` for daily coding tasks, `moe_orchestrated` for complex analysis.

### Which Claude model IDs are available for Claude Code?

```bash
# Configured via CLAUDE_CODE_MODELS in .env:
claude-opus-4-6
claude-sonnet-4-6
claude-haiku-4-5-20251001
claude-opus-4-5
claude-sonnet-4-5
claude-haiku-4-5
```

---

## 2. API Keys — Create & Use

### How do I create an API key?

**Admin UI** → Users → select desired user → **API Keys** → **"Create new key"**

The raw key (`moe-sk-...`) is displayed **only once** — copy it immediately!

### What format do API keys have?

```
moe-sk-<48 random hex characters>
```

Example: `moe-sk-<48_hex_chars_replace_me_with_real_key>`

### How do I use the API key with curl?

```bash
# Variant 1: Authorization header (recommended)
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "Authorization: Bearer moe-sk-XXXXXXXX..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moe-orchestrator",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Variant 2: x-api-key header
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "x-api-key: moe-sk-XXXXXXXX..." \
  -H "Content-Type: application/json" \
  -d '{"model": "moe-orchestrator", "messages": [{"role": "user", "content": "Test"}]}'
```

### How do I revoke a key?

Admin UI → Users → API Keys → **"Delete key"** (takes effect immediately, even if cached).

---

## 3. Authentication

### What authentication methods are available?

| Method | When to use | Header |
|---------|---------------|--------|
| **API key** | Standard, direct API use | `Authorization: Bearer moe-sk-...` |
| **OIDC/JWT** | SSO via Authentik (browser flow) | `Authorization: Bearer <JWT>` |

### How does OIDC login (browser) work?

1. Browser opens **https://admin.moe-sovereign.org** (or localhost:8088)
2. Click **"Login with SSO"** → redirect to Authentik
3. Authentik login → redirect back with JWT
4. JWT is used as Bearer token for subsequent API calls

### What is the difference between OIDC and API key?

- **OIDC**: Short-lived (1h), for browser/interactive use
- **API key**: Long-lived, for scripts/automation/Claude Code

---

## 4. Request Modes (Models)

### Which model IDs can I use?

| Model ID | Mode | Use case |
|----------|-------|-----------------|
| `moe-orchestrator` | `default` | Full answers with context — default choice |
| `moe-orchestrator-code` | `code` | Source code only, no prose |
| `moe-orchestrator-concise` | `concise` | Short & precise (max ~120 words) |
| `moe-orchestrator-agent` | `agent` | Coding agent (OpenCode, Continue.dev) |
| `moe-orchestrator-agent-orchestrated` | `agent_orchestrated` | Claude Code — full MoE fanout |
| `moe-orchestrator-research` | `research` | Deep research with multiple web searches |
| `moe-orchestrator-report` | `report` | Professional Markdown report |
| `moe-orchestrator-plan` | `plan` | Structured planning for complex tasks |

### When to use which mode?

```
General questions       → moe-orchestrator
Code problems           → moe-orchestrator-code
Quick answers           → moe-orchestrator-concise
Deep research           → moe-orchestrator-research
Complex planning        → moe-orchestrator-plan
Claude Code (daily)     → moe-orchestrator-agent
Claude Code (complex)   → moe-orchestrator-agent-orchestrated
```

### How do I enable streaming?

```bash
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "Authorization: Bearer moe-sk-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moe-orchestrator",
    "stream": true,
    "messages": [{"role": "user", "content": "Explain Docker"}]
  }'
```

---

## 5. Token Budget & Costs

### How are token budgets set?

Admin UI → Users → **Budget** → three configurable limits:

| Limit | Description | Example |
|-------|-------------|---------|
| `daily_limit` | Max tokens/day (reset 00:00 UTC) | `100 000` |
| `monthly_limit` | Max tokens/month | `3 000 000` |
| `total_limit` | Lifetime limit | `50 000 000` |

`NULL` = unlimited (default for admin accounts).

### How do I view my current consumption?

```bash
# Via Admin API
curl -H "Authorization: Bearer moe-sk-..." \
  http://localhost:8088/api/users/{user_id}/usage
```

Or: Admin UI → Users → **Usage** tab.

### How much does a request cost?

`TOKEN_PRICE_EUR = 0.00002 €` per token (for display/reporting only, no real billing).

### What happens when the budget is exceeded?

The API responds with:

```json
{"error": {"type": "budget_exceeded", "message": "Daily token limit exceeded"}}
```

HTTP status: `429 Too Many Requests`

---

## 6. Expert Templates

### What is an expert template?

A predefined configuration package that for a user:

- Prescribes specific models per expert category
- Sets custom system prompts for individual experts
- Overrides judge and planner models
- Carries a custom `cost_factor` (token weighting)

### How do I create an expert template?

Admin UI → **Expert Templates** → **"New template"**

Minimal JSON structure:

```json
{
  "name": "My Template",
  "description": "Optimized for Python development",
  "experts": {
    "code_reviewer": {
      "system_prompt": "Senior Python developer focused on PEP 8 and type hints.",
      "models": [
        {"model": "devstral:24b", "endpoint": "N04-RTX", "required": true}
      ]
    }
  }
}
```

### How do I assign a template to a user?

Admin UI → Users → select user → **Permissions** → select template from dropdown → save.

Alternatively via API:

```bash
curl -X POST http://localhost:8088/api/users/{user_id}/permissions \
  -H "Authorization: Bearer moe-sk-..." \
  -H "Content-Type: application/json" \
  -d '{"resource_type": "expert_template", "resource_id": "tmpl-xxxx"}'
```

### Are template changes applied immediately?

Yes — templates are reloaded from `.env` every **60 seconds**, without container restart.
Immediate activation: `docker compose restart langgraph-orchestrator`

---

## 7. Giving Feedback

### How do I give feedback on a response?

```bash
curl -X POST http://localhost:8002/v1/feedback \
  -H "Authorization: Bearer moe-sk-..." \
  -H "Content-Type: application/json" \
  -d '{
    "response_id": "chatcmpl-abcd1234",
    "rating": 5,
    "correction": "Optional correction of the response..."
  }'
```

### What do the ratings mean?

| Rating | Meaning | Effect |
|--------|-----------|--------|
| 1–2 | Negative | Expert model loses score; few-shot errors are saved |
| 3 | Neutral | No effect on scoring |
| 4–5 | Positive | Expert model gains score; planner patterns are saved |

### Where do I find the `response_id`?

In the API response in the `id` field:

```json
{"id": "chatcmpl-a1b2c3d4", "object": "chat.completion", ...}
```

---

## 8. Admin UI — Key Functions

### How do I access the Admin UI?

- **Local**: http://localhost:8088
- **External**: https://admin.moe-sovereign.org (via Authentik SSO)

### What are the main functions?

| Section | Function |
|---------|---------|
| **Users** | Create users, API keys, budgets, CC profiles, assign templates |
| **Expert Templates** | Create, edit, delete templates |
| **Models** | Configure expert models, view VRAM status |
| **Live Logs** | Real-time pipeline logs via WebSocket |
| **System Health** | Docker container status, GPU utilization |
| **Metrics** | Token consumption, feedback statistics, cache hit rate |

### How do I create a new user?

Admin UI → Users → **"Create new user"** → enter username, email, password → save.
Then: create API key + set budget + optionally assign template.

---

## 9. MoE Portal & Web Interface

### What web interfaces are available?

| Portal | URL | Access |
|--------|-----|--------|
| **MoE Web** (public) | https://moe-sovereign.org | No login |
| **Admin UI** | https://admin.moe-sovereign.org | Authentik SSO |
| **API endpoint** | https://api.moe-sovereign.org | API key / OIDC |
| **Documentation** | http://localhost:8010 | Internal |
| **Grafana** | http://localhost:3001 | Monitoring |
| **Neo4j Browser** | http://localhost:7474 | Graph inspection |

### How do I connect Open WebUI to MoE?

In Open WebUI → Settings → **Connections** → OpenAI API:

```
Base URL: http://localhost:8002/v1
API Key:  moe-sk-xxxxxxxx...
```

Then all `moe-orchestrator-*` models appear in the model selection.

### How do I connect Continue.dev / Cursor to MoE?

`~/.continue/config.json`:

```json
{
  "models": [{
    "title": "MoE Agent",
    "provider": "openai",
    "model": "moe-orchestrator-agent",
    "apiBase": "http://localhost:8002/v1",
    "apiKey": "moe-sk-xxxxxxxx..."
  }]
}
```

---

## 10. Best Practices

### Which mode should I use by default?

- **Interactive chats**: `moe-orchestrator` (default)
- **Code reviews**: `moe-orchestrator-code`
- **Long analyses**: `moe-orchestrator-research`
- **Claude Code daily**: `.bashrc` with `moe-orchestrator-agent-orchestrated`

### How do I optimize latency?

1. **Trivial questions** are automatically classified as `trivial` → tier-1 model, no research
2. Use `moe-orchestrator-concise` when short answers are sufficient
3. Research/thinking node is automatically skipped for `trivial`/`moderate` requests

### How do I use the self-correction loop?

Give negative feedback (rating 1–2) after each wrong answer — the system learns automatically:

- Faulty models lose score → are selected less often
- Numeric errors are saved as few-shot examples → planner avoids them in future

### How do I secure sensitive data?

- Never check in API keys in scripts → use `.env` or vault
- `.bashrc` entries only for local development, not on production servers
- Set budget limits for all non-admin users

### When should I use expert templates?

- **Specialized teams**: e.g. only `code_reviewer` + `technical_support` for DevOps
- **Privacy-critical use**: `medical_consult` on dedicated GPU without logging
- **Performance tuning**: tier-1-only template for fast, simple answers

---

## 11. Troubleshooting

### API responds with 401 Unauthorized

- API key correct? Format: `moe-sk-` + 48 hex characters
- Key active? Admin UI → Users → API Keys → check status
- `AUTHENTIK_URL` set but Authentik unreachable? → check `.env`

### API responds with 429 Too Many Requests

- Token budget exceeded → Admin UI → Users → increase budget or wait for reset
- Too many parallel requests → wait briefly, MoE has GPU semaphores

### Response is empty or very short

- VRAM exhausted? → `docker logs langgraph-orchestrator | grep VRAM`
- Judge LLM timeout? → increase `JUDGE_TIMEOUT` in `.env` (default: 900s)
- Model not yet loaded? → first request after cold start takes longer

### CC profiles do not appear in Claude Code

1. `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` set in `.bashrc`?
2. `source ~/.bashrc` executed?
3. Restart Claude Code
4. API reachable? `curl -s http://localhost:8002/v1/models -H "Authorization: Bearer moe-sk-..." | jq .`

### Self-correction fails / few-shot data missing

- Valkey running? `docker ps | grep terra_cache`
- Check key prefix: `docker exec terra_cache valkey-cli -a "$REDIS_PASSWORD" keys "moe:few_shot:*"`
- Directory present? `ls /opt/moe-infra/few_shot_examples/`

---

*Last updated: 2026-04-04 — Version 1.0*

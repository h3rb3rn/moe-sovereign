# MoE API — User Guide

**Sovereign Multi-Model Orchestrator**
Internal AI platform · Access documentation for API users

---

## Table of Contents

1. [Access & First Login](#1-access-first-login)
2. [User Portal — Overview](#2-user-portal-overview)
3. [Managing API Keys](#3-managing-api-keys)
4. [Using the API](#4-using-the-api)
5. [Token Budget & Consumption](#5-token-budget-consumption)
6. [Change Profile & Password](#6-change-profile-password)
7. [Errors & FAQ](#7-errors-faq)

---

## 1. Access & First Login

### Receiving credentials

Your account is created by the administrator. You will receive:

- **Username** (e.g. `max.mustermann`)
- **Initial password** (set by the admin)
- **URL of the User Portal** (e.g. `http://moe.intern:8088/user/login`)

### First login

1. Open the User Portal in your browser: `http://<server>:8088/user/login`
2. Enter username and password
3. You will be redirected to the **Dashboard**
4. Change your password immediately under **Profile & Password**

> **Note:** Your account has **no permissions** by default. The administrator must
> explicitly grant access rights (models, modes, skills). Contact the admin if needed.

---

## 2. User Portal — Overview

The portal is available at: `http://<server>:8088/user/`

| Section | URL | Description |
|---------|-----|--------------|
| Dashboard | `/user/dashboard` | Budget status, recent activity, API keys |
| Billing | `/user/billing` | Token consumption by model/mode |
| Usage History | `/user/usage` | All requests with token count |
| API Keys | `/user/keys` | Create & revoke keys |
| Profile | `/user/profile` | Display name, email, password |

### Dashboard at a glance

The dashboard shows:

- **Budget bars** for daily, monthly, and total limits
  - Green (< 70%), Orange (70–90%), Red (> 90%)
- **14-day chart** with daily token consumption
- **Active API keys** with timestamp of last use

---

## 3. Managing API Keys

### Why API keys?

The MoE API cannot be used directly via browser — you need an **API key** for each
application (Claude Code, Open WebUI, custom scripts).

### Create a new key

1. Navigate to **API Keys** (`/user/keys`)
2. Enter a label, e.g. `Claude Code Laptop`
3. Click **Create key**
4. The full key is displayed **once** — **copy it immediately!**

```
moe-sk-<48_hex_chars_replace_me_with_real_key>
```

> **Important:** After closing the window, the key is never fully visible again.
> Only the prefix (e.g. `moe-sk-a3f8...`) remains for identification.

### Revoke a key

If a key is compromised or no longer needed:

1. Go to **API Keys**
2. Click **Revoke** next to the relevant key
3. The key is immediately invalid (Valkey cache is invalidated)

### Recommendations

- Create **one key per device / application**
- Name keys descriptively (`Claude Code Server`, `Open WebUI`, `Python Script`)
- Rotate keys regularly (every 90 days recommended)

---

## 4. Using the API

### Endpoint

```
http://<server>:8002
```

The platform provides **two compatible API interfaces**:

| Interface | Endpoint | Usage |
|---------------|----------|------------|
| **Anthropic Messages API** | `/v1/messages` | Claude Code, Anthropic SDK |
| **OpenAI Chat Completions API** | `/v1/chat/completions` | Open WebUI, native LLMs, OpenAI SDK |

> **Important:** Claude Code communicates exclusively via the **Anthropic Messages API**
> (via `ANTHROPIC_BASE_URL`). The OpenAI-compatible API is intended for native LLM access
> (Open WebUI, custom scripts using the openai SDK).

### Authentication

Pass the API key as an `Authorization: Bearer` header or as an `x-api-key` header:

```bash
# Anthropic Messages API — for Claude Code
curl http://<server>:8002/v1/messages \
  -H "Authorization: Bearer moe-sk-xxxxxxxx..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Explain Docker Compose."}]
  }'

# OpenAI Chat Completions API — for native LLMs / Open WebUI
curl http://<server>:8002/v1/chat/completions \
  -H "x-api-key: moe-sk-xxxxxxxx..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.3:70b@N04-RTX",
    "messages": [{"role": "user", "content": "Explain Docker Compose."}]
  }'
```

### Configuration in Claude Code

Claude Code uses the **Anthropic Messages API**. Set the following environment variables:

```bash
export ANTHROPIC_BASE_URL=http://<server>:8002
export ANTHROPIC_API_KEY=moe-sk-xxxxxxxx...
```

Or persistently in `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://<server>:8002",
    "ANTHROPIC_API_KEY": "moe-sk-xxxxxxxx..."
  }
}
```

Claude Code forwards your requests to the MoE Orchestrator, which uses the configured
**Claude Code Profile** (`cc_profile`) for tool execution and routing.

### Configuration in Open WebUI (native LLMs)

Open WebUI uses the **OpenAI-compatible API**:

1. Go to **Settings → Connections → OpenAI API**
2. **API Base URL**: `http://<server>:8002/v1`
3. **API Key**: `moe-sk-xxxxxxxx...`

### Python (Anthropic SDK)

```python
import anthropic

client = anthropic.Anthropic(
    api_key="moe-sk-xxxxxxxx...",
    base_url="http://<server>:8002",
)

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
)
print(message.content[0].text)
```

### Python (OpenAI SDK — for native LLMs)

```python
from openai import OpenAI

client = OpenAI(
    api_key="moe-sk-xxxxxxxx...",
    base_url="http://<server>:8002/v1",
)

response = client.chat.completions.create(
    model="llama3.3:70b@N04-RTX",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

### Available Model IDs

Which models you can use depends on your **permissions** (see section 5).
Ask your administrator which model IDs have been enabled for you.

Typical Claude model IDs (for Claude Code / Anthropic Messages API):

- `claude-sonnet-4-6` — Standard (MoE orchestration)
- `claude-opus-4-6` — Extended MoE orchestration
- `claude-haiku-4-5-20251001` — Fast & compact

Native LLM IDs follow the format `model:tag@server`, e.g. `llama3.3:70b@N04-RTX`.

---

## 5. Token Budget & Consumption

### What is a token budget?

Your account has limits for:

| Limit | Description | Reset |
|-------|-------------|-------|
| **Daily** | Max tokens per day | Midnight (UTC) |
| **Monthly** | Max tokens per month | First of month |
| **Total** | Lifetime limit (if configured) | No reset |

1 token ≈ 0.75 words in English, approx. 0.5 words in German.
A typical chat request consumes 500–3,000 tokens.

### Budget exceeded?

When your budget is exhausted:

- The API responds with HTTP `429 Too Many Requests`
- The budget bar appears in red in the portal
- Contact the administrator for an increase

### View consumption

Under **Billing** you see:

- Consumption today / this month / total
- Breakdown by model and mode
- How many tokens remain

Under **Usage History** you find:

- Every individual request with timestamp
- Prompt tokens, completion tokens, total
- Status (ok / budget_exceeded / error)

### Permissions

By default **all access is blocked**. Unlocked resources are:

- **expert_template** — Expert configuration package (defines which LLMs are used for which domains)
- **cc_profile** — Claude Code integration profile (tool model, MoE mode, reasoning settings)
- **model_endpoint** — Native LLMs on which inference server (OpenAI API access)
- **moe_mode** — Processing mode (`native`, `moe_orchestrated`, `moe_reasoning`)
- **skill** — Claude Code skills available to you
- **mcp_tool** — MCP tools (precision calculator etc.)

---

## 6. Change Profile & Password

1. Navigate to **Profile & Password** (`/user/profile`)
2. Change display name and/or email
3. To change the password: enter new password in both fields (min. 8 characters)
4. Click **Save**

> **Note:** The username cannot be changed yourself — contact the admin if needed.

---

## 7. Errors & FAQ

### `401 Unauthorized`

**Cause:** API key invalid, revoked, or not present.
**Solution:** Check in the portal whether your key is active. Create a new key if needed.

### `429 Too Many Requests`

**Cause:** Daily or monthly token budget exhausted.
**Solution:** Wait until reset (midnight / first of month) or contact the admin.

### `403 Forbidden`

**Cause:** No permission for the requested model, mode, or skill.
**Solution:** Ask the administrator to grant the corresponding permission.

### Login does not work

- Check capitalization in the username
- Ensure your account is not blocked (the admin can check this)
- Use the browser console (F12) for error details

### Key forgotten / lost

There is no way to view an existing key again. Create a new key and
revoke the old one.

### Who is the administrator?

For account questions, budget increases, or permissions, contact the responsible person in
your organization (IT department or the MoE platform operator).

---

*MoE Sovereign Orchestrator — Internal — As of April 2026*

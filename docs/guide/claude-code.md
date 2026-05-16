# Claude Code Integration

Claude Code can use MoE Sovereign as an Anthropic backend.
All requests from Claude Code are then routed through the MoE pipeline.

!!! tip "Using Claude Desktop?"
    If you run Claude Code **inside Claude Desktop** (or use Claude Cowork),
    you can configure MoE Sovereign as a gateway once in the app and skip the
    environment variables below. See [Claude Desktop & Cowork](claude-desktop.md)
    for the one-command setup.

## Basic Configuration (.bashrc)

```bash
# ~/.bashrc or ~/.zshrc
export ANTHROPIC_BASE_URL=https://api.moe-sovereign.org
export ANTHROPIC_API_KEY=<YOUR-API-KEY>
```

After setting the variables: `source ~/.bashrc`, then start `claude`.

## Claude Code Profiles (CC Profiles)

CC profiles can be configured in the portal ([portal.moe-sovereign.org](https://portal.moe-sovereign.org)) to control Claude Code behavior.

### Profile Fields

| Field | Description | Example value |
|------|--------------|--------------|
| `name` | Profile name | `dev-assistant` |
| `model` | Base model ID | `moe-orchestrator-agent-orchestrated` |
| `moe_mode` | MoE routing mode | `full` / `fast` / `code` |
| `expert_hints` | Preferred experts | `["code_reviewer", "technical_support"]` |
| `max_tokens` | Token limit per request | `4096` |
| `temperature` | Creativity (0.0–1.0) | `0.2` |

### moe_mode Options

| Mode | Behavior | Recommended for |
|-------|-----------|---------------|
| `full` | Full MoE fanout with all relevant experts | Complex architecture questions |
| `fast` | Complexity routing (trivial → 1 expert) | Interactive code completion |
| `code` | Code experts only (code_reviewer, technical_support) | Code-focused work |
| `research` | Experts + SearXNG research | Technology comparisons |

## Recommended Setup for Claude Code

```bash
# Optimal setup for code work
export ANTHROPIC_BASE_URL=https://api.moe-sovereign.org
export ANTHROPIC_API_KEY=<YOUR-API-KEY>

# Model for Claude Code (agent-orchestrated = full MoE fanout)
# Use this model in claude --model=... or in Claude Code Settings
```

Select the model within Claude Code:
```
/model moe-orchestrator-agent-orchestrated
```

## CLAUDE.md Integration

Add to your project's `CLAUDE.md` which experts should be preferred:

```markdown
# Project-specific AI instructions

Prefer:
- code_reviewer for all security reviews
- technical_support for Docker/Kubernetes questions
- math for performance calculations
```

## Troubleshooting

### "Invalid API Key"
```bash
# Check the key
curl https://api.moe-sovereign.org/v1/models \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

### Very slow responses
- Use model `moe-orchestrator-concise` for faster responses
- Or configure `moe_mode: fast` in the portal profile

### 504 Gateway Timeout on long tasks

Orchestrated pipeline requests (Planner → Experts → Judge) regularly take 2–10 minutes
on local GPU hardware. A 504 after exactly **60 seconds** means the Nginx reverse proxy
in front of the API endpoint is using its default `proxy_read_timeout`.

The fix must be applied to the **Nginx virtual host** on the host that terminates TLS for
`api.your-domain.com` — not to the orchestrator's `.env`. Add to the `location /` block:

```nginx
proxy_connect_timeout    75s;
proxy_read_timeout       3600s;   # match EXPERT_TIMEOUT / JUDGE_TIMEOUT in .env
proxy_send_timeout       3600s;
proxy_buffering          off;     # required for SSE streaming
proxy_cache              off;
chunked_transfer_encoding on;
```

Then reload Nginx: `sudo nginx -t && sudo systemctl reload nginx`

See [Webserver & Reverse Proxy](../system/webserver.md) for the full reference virtual host
configuration.

### Claude Code won't start / connection error
```bash
# Test connection
curl https://api.moe-sovereign.org/health

# Check variables
echo $ANTHROPIC_BASE_URL
echo ${ANTHROPIC_API_KEY:0:10}...
```

### Model not found
```bash
# List available models
curl https://api.moe-sovereign.org/v1/models \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" | jq '.data[].id'
```

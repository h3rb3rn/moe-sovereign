# Agent Integration Profiles

Connect your favorite coding agent to MoE Sovereign in under five minutes.
This page provides quick-start configurations for every supported agent,
explains which API endpoint each one uses, and maps out feature compatibility.

---

## How It Works

MoE Sovereign exposes two API endpoints:

| Endpoint | Protocol | Used by |
|----------|----------|---------|
| `/v1/messages` | Anthropic Messages API | Claude Code |
| `/v1/chat/completions` | OpenAI Chat Completions API | OpenCode, Claw Code, Codex CLI, Aider, Continue.dev, Cursor, Open WebUI |

Both endpoints route requests through the same MoE pipeline. The endpoint
you use depends solely on what your agent expects — the processing behind
it is identical.

---

## Claude Code

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) is Anthropic's
official CLI agent. It speaks the Anthropic Messages API natively, which means
it can use every MoE Sovereign feature without an adapter layer.

### Quick Start

```bash
# ~/.bashrc or ~/.zshrc
export ANTHROPIC_BASE_URL=https://your-moe-instance.example.com
export ANTHROPIC_API_KEY=moe-sk-xxxxxxxx...

# Start Claude Code — all requests now route through MoE Sovereign
claude
```

After editing your shell profile, run `source ~/.bashrc` (or `source ~/.zshrc`)
before starting `claude`.

### VS Code Extension

If you use the Claude Code VS Code extension instead of the CLI:

```json
{
  "claude-code.apiEndpoint": "https://your-moe-instance.example.com",
  "claude-code.apiKey": "moe-sk-xxxxxxxx..."
}
```

### What Works

| Capability | Status | Notes |
|------------|--------|-------|
| `/v1/messages` endpoint | Fully supported | Native Anthropic protocol |
| Streaming (SSE) | Fully supported | Real-time token output |
| Tool use / function calling | Fully supported | File edits, bash, MCP tools |
| Multi-turn conversations | Fully supported | Full conversation history preserved |
| Thinking blocks (`<think>`) | Fully supported | Requires `moe_reasoning` or `moe_orchestrated` mode |
| Image inputs | Supported | Forwarded to vision-capable models |
| System prompts | Supported | CLAUDE.md, project instructions passed through |

### Profile Switching via API Key

Each API key can be bound to a specific **CC Profile** in the Admin UI.
This controls which MoE mode (native, reasoning, orchestrated) is used
for all requests made with that key.

**Setup:**

1. Admin UI > **Users** > select user > **API Keys**
2. Set the **CC Profile** dropdown for the desired key
3. All requests with that key automatically use the bound profile

This lets you create multiple API keys for different workflows:

```bash
# Key bound to cc-ref-native — fast interactive coding
export ANTHROPIC_API_KEY=moe-sk-native-key...

# Key bound to cc-ref-orchestrated — deep research
export ANTHROPIC_API_KEY=moe-sk-deep-key...
```

Switch between profiles by changing which key is exported.

### Limitations

- Claude Code only speaks the Anthropic `/v1/messages` protocol — it cannot
  use the OpenAI-compatible endpoint directly.
- Model selection inside Claude Code (`/model` command) selects from models
  reported by `/v1/models`. The actual LLM routing is controlled by the
  expert template bound to your CC Profile, not by the model name alone.

---

## OpenCode

[OpenCode](https://github.com/opencode-ai/opencode) is a Go-based, open-source
terminal coding agent with support for 75+ LLM providers. It uses the
OpenAI-compatible `/v1/chat/completions` endpoint.

### Quick Start

Create or edit `~/.config/opencode/config.toml`:

```toml
[providers.moe]
name = "MoE Sovereign"
base_url = "https://your-moe-instance.example.com/v1"
api_key = "moe-sk-xxxxxxxx..."
models = ["moe-reference-30b-balanced"]
```

Then start OpenCode:

```bash
opencode
```

### Notes

- OpenCode discovers available models from `/v1/models`. All expert templates
  configured in MoE Sovereign appear as selectable models.
- Tool use support depends on the OpenCode version. Check the project
  documentation for the latest capabilities.
- Streaming is fully supported.

---

## Claw Code

[Claw Code](https://github.com/claw-project/claw-code) is a Python-based,
open-source coding agent inspired by Claude Code. It works via the
OpenAI-compatible endpoint.

### Quick Start

```bash
export OPENAI_BASE_URL=https://your-moe-instance.example.com/v1
export OPENAI_API_KEY=moe-sk-xxxxxxxx...
export OPENAI_MODEL=moe-reference-30b-balanced

claw-code
```

### Notes

- Claw Code supports tool use (file edits, bash execution) through the
  OpenAI function calling protocol.
- Streaming is fully supported.
- Because Claw Code uses the OpenAI endpoint, CC Profile selection via API
  key binding is not available. The model name in `OPENAI_MODEL` determines
  which expert template is used.

---

## Codex CLI (OpenAI)

[Codex CLI](https://github.com/openai/codex) is OpenAI's official terminal
agent. It supports custom base URLs for OpenAI-compatible backends.

### Quick Start

```bash
export OPENAI_BASE_URL=https://your-moe-instance.example.com/v1
export OPENAI_API_KEY=moe-sk-xxxxxxxx...

codex --model moe-reference-30b-balanced
```

### Notes

- Codex CLI expects a fully OpenAI-compatible backend. MoE Sovereign's
  `/v1/chat/completions` endpoint satisfies this requirement.
- Tool use and streaming are supported.
- The `--model` flag selects the expert template by its model ID as reported
  by `/v1/models`.

---

## Aider

[Aider](https://github.com/paul-gauthier/aider) is the oldest terminal AI
pair-programming tool (39K+ GitHub stars). It supports OpenAI-compatible
backends via command-line flags or environment variables.

### Quick Start

```bash
aider --openai-api-base https://your-moe-instance.example.com/v1 \
      --openai-api-key moe-sk-xxxxxxxx... \
      --model openai/moe-reference-30b-balanced
```

### Environment Variables (Alternative)

```bash
export OPENAI_API_BASE=https://your-moe-instance.example.com/v1
export OPENAI_API_KEY=moe-sk-xxxxxxxx...

aider --model openai/moe-reference-30b-balanced
```

### Notes

- The `openai/` prefix in the model name tells Aider to use the OpenAI
  provider. This is required.
- Aider supports tool use for file editing and git operations.
- Streaming is fully supported.
- For best results with the MoE pipeline, use a `30b-balanced` or larger
  template — Aider's edit format benefits from strong instruction following.

---

## Continue.dev / Cursor

These IDE-integrated agents are covered in the dedicated
[Continue / Cursor Integration](continue-dev.md) page.

Both use the OpenAI-compatible `/v1/chat/completions` endpoint and support
model selection, streaming, and tool use.

---

## Discovering Available Models

All agents can query available models (expert templates) from MoE Sovereign:

```bash
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer moe-sk-xxxxxxxx..." | jq '.data[].id'
```

Each expert template configured in the Admin UI appears as a model in the
`/v1/models` response. Select the model ID that matches your desired
quality/speed trade-off.

---

## Compatibility Matrix

| Feature | Claude Code | OpenCode | Claw Code | Codex CLI | Aider |
|---------|:-----------:|:--------:|:---------:|:---------:|:-----:|
| `/v1/messages` (Anthropic) | Yes | No | No | No | No |
| `/v1/chat/completions` (OpenAI) | No | Yes | Yes | Yes | Yes |
| Tool Use | Yes | Limited | Yes | Yes | Yes |
| Streaming | Yes | Yes | Yes | Yes | Yes |
| MoE Pipeline | Yes | Yes | Yes | Yes | Yes |
| CC Profile Selection | Yes (via key) | No | No | No | No |
| Expert Template Routing | Yes | Yes | Yes | Yes | Yes |
| Thinking Blocks | Yes | No | No | No | No |
| Image Inputs | Yes | No | No | No | No |

!!! note "Endpoint determines features"
    Agents using the Anthropic `/v1/messages` endpoint (Claude Code) get access
    to thinking blocks and the full CC Profile system. Agents using the OpenAI
    `/v1/chat/completions` endpoint select their expert template via the model
    name instead.

---

## Troubleshooting

### "Invalid API Key"

```bash
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

If this returns an error, verify the key in the User Portal under **API Keys**.

### Connection Refused

```bash
curl https://your-moe-instance.example.com/health
```

If this fails, the MoE Sovereign instance is not reachable. Check DNS,
firewall rules, and whether the service is running.

### Model Not Found

```bash
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer moe-sk-xxxxxxxx..." | jq '.data[].id'
```

Use one of the returned model IDs in your agent configuration.

### Slow Responses

- Switch to a `native` CC Profile or a fast expert template (e.g., `8b-fast`)
  for interactive coding.
- Use `moe_orchestrated` mode only for deep research or complex analysis
  where 2-10 minute latency is acceptable.
- See [Expert Templates & Profiles](templating-guide.md) for guidance on
  choosing the right quality/speed trade-off.

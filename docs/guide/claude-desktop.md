# Claude Desktop & Cowork

MoE Sovereign implements the full
[Anthropic Third-Party Inference Gateway](https://support.claude.com/en/articles/14680729-use-claude-cowork-with-third-party-platforms)
specification. This means Claude Desktop (including Claude Cowork and the
embedded Claude Code CLI) can route all inference through your self-hosted
MoE pipeline — with no changes to the client.

---

## How It Works

Claude Desktop connects to MoE Sovereign like any other Anthropic-compatible
gateway. The handshake is:

1. Claude Desktop reads the `thirdPartyInference.baseUrl` from
   `claude_desktop_config.json`.
2. On startup it queries `GET /v1/models` to discover available models —
   any entry whose ID starts with `claude-` or `anthropic-` appears as
   a **"From gateway"** item in the model picker, labeled with the
   human-readable `display_name` field.
3. All inference requests go to `POST /v1/messages`.
   The `anthropic-beta` and `anthropic-version` headers are forwarded
   transparently.
4. Context-budget warnings use `POST /v1/messages/count_tokens`.

Everything that Claude Desktop routes to Anthropic by default is instead
processed by the MoE pipeline on your own hardware.

---

## Quick Start (Automated)

The fastest path is the bundled setup script:

```bash
# From the moe-infra checkout or server
bash scripts/setup-claude-desktop.sh
```

The script will:

- Detect the `claude_desktop_config.json` location on macOS, Linux, and WSL
- Prompt for your MoE Sovereign URL and API key
- Run a live connectivity check against `/v1/models`
- Create a timestamped backup of the existing config
- Merge the gateway block without touching other settings

After the script finishes, **restart Claude Desktop**.

---

## Quick Start (Manual)

Locate `claude_desktop_config.json`:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add (or merge) the following block:

```json
{
  "thirdPartyInference": {
    "type":    "gateway",
    "baseUrl": "https://your-moe-instance.example.com",
    "apiKey":  "moe-sk-xxxxxxxx..."
  }
}
```

Restart Claude Desktop. The gateway becomes active immediately.

---

## Selecting the Gateway in Claude Desktop UI

After restarting, open **Developer → Configure Third-Party Inference**.
Claude Desktop shows the gateway URL you configured.

The `/model` picker in Claude Code (embedded in Claude Desktop) will list
all models from `/v1/models` that match the `claude-` prefix — these are
your CC Profile-enabled API keys' model aliases. They appear as:

```
Claude Sonnet 4.6 → MoE (Gateway)    [From gateway]
Claude Opus 4.6 → MoE (Gateway)      [From gateway]
...
```

The label is the `display_name` field returned by MoE Sovereign.

---

## Claude Cowork

Claude Cowork (the web-based workspace inside Claude Desktop) uses the
same gateway configuration. All Cowork conversations route through your
MoE pipeline. This satisfies data-residency requirements: prompts and
completions never reach Anthropic's infrastructure.

!!! note "Connectors in Cowork"
    Anthropic-native Cowork connectors (e.g. Google Workspace, Jira) depend
    on Anthropic's cloud layer and are not available when using a third-party
    gateway. Use [MCP servers](https://docs.moe-sovereign.org/system/toolstack/mcp_tools/)
    as an alternative for tool integrations.

---

## Feature Compatibility

| Feature | Status | Notes |
|---------|--------|-------|
| Claude Code (embedded CLI) | Fully supported | `ANTHROPIC_BASE_URL` set automatically |
| Claude Cowork chat | Fully supported | All conversations route through MoE |
| Model picker (`/model`) | Fully supported | `claude-*` aliases with `display_name` |
| Context-budget warnings | Fully supported | `/v1/messages/count_tokens` endpoint |
| Streaming (SSE) | Fully supported | Real-time token output |
| Tool use / file edits | Fully supported | MCP tool pass-through |
| Thinking blocks (`<think>`) | Supported | Requires `moe_reasoning` CC Profile |
| Cowork connectors (Google, Jira…) | Not available | Use MCP servers instead |
| Web search (Anthropic-native) | Not available | MoE uses SearXNG internally |

---

## CC Profile Binding

When Claude Desktop authenticates with an API key that has a **CC Profile**
bound in the Admin UI, the profile controls which MoE routing mode is used:

- `moe_native` — single-model fast path (best for interactive chat)
- `moe_reasoning` — reasoning expert with thinking blocks
- `moe_orchestrated` — full MoE pipeline (all experts, GraphRAG, MCP tools)

Create multiple API keys with different profiles for different use-cases and
switch between them in `claude_desktop_config.json`.

See [Claude Code Profiles](claude-code-profiles.md) for full configuration
details.

---

## Troubleshooting

### Gateway not showing up after restart

Verify JSON syntax in `claude_desktop_config.json`:

```bash
python3 -c "import json; json.load(open('/path/to/claude_desktop_config.json'))" && echo OK
```

### "Invalid API Key"

```bash
curl https://your-moe-instance.example.com/v1/models \
  -H "x-api-key: moe-sk-xxxxxxxx..."
```

A `200` response confirms the key is valid. If you receive `401`, check
the key in the User Portal under **API Keys**.

### Models not appearing in picker

The model picker only shows entries whose ID starts with `claude-` or
`anthropic-`. These are shown to users whose API key has a **CC Profile**
(`cc_profile` permission). Ask your admin to grant this permission, or
create a key with an explicit CC Profile in the Admin UI.

### Context-budget warnings

If Claude Code warns about context limits, this is normal — the
`/v1/messages/count_tokens` endpoint provides an estimate (chars / 3.5).
It is accurate enough for budget warnings but not identical to Anthropic's
exact tokenizer.

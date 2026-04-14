# Continue.dev / Cursor Integration

MoE Sovereign is OpenAI-compatible and can be used as a backend for Continue.dev and Cursor.

## Continue.dev

### config.json

```json
{
  "models": [
    {
      "title": "MoE Sovereign",
      "provider": "openai",
      "model": "moe-orchestrator-agent",
      "apiBase": "https://api.moe-sovereign.org/v1",
      "apiKey": "<YOUR-API-KEY>"
    },
    {
      "title": "MoE Code Review",
      "provider": "openai",
      "model": "moe-orchestrator-code",
      "apiBase": "https://api.moe-sovereign.org/v1",
      "apiKey": "<YOUR-API-KEY>"
    }
  ],
  "tabAutocompleteModel": {
    "title": "MoE Fast",
    "provider": "openai",
    "model": "moe-orchestrator-concise",
    "apiBase": "https://api.moe-sovereign.org/v1",
    "apiKey": "<YOUR-API-KEY>"
  }
}
```

### Configuration path

- **Linux/macOS**: `~/.continue/config.json`
- **Windows**: `%USERPROFILE%\.continue\config.json`

## Cursor

### Settings → Models → Add Model

```
Provider: OpenAI Compatible
Base URL: https://api.moe-sovereign.org/v1
API Key: <YOUR-API-KEY>
Model: moe-orchestrator-agent
```

### Cursor Settings (JSON)

```json
{
  "cursor.openai.baseUrl": "https://api.moe-sovereign.org/v1",
  "cursor.openai.apiKey": "<YOUR-API-KEY>",
  "cursor.openai.model": "moe-orchestrator-agent"
}
```

## Recommended Models

| Use Case | Model ID |
|----------|-----------|
| Chat / Questions | `moe-orchestrator` |
| Code Completion | `moe-orchestrator-concise` |
| Code Review | `moe-orchestrator-code` |
| Agent Tasks | `moe-orchestrator-agent` |

## Notes

- **Context Window**: MoE Sovereign supports up to 32k token context
- **Streaming**: Fully supported
- **Function Calling**: Supported (for tool use in agents)
- **Vision**: Image analysis via Base64 available

# Continue.dev / Cursor Integration

MoE Sovereign is OpenAI-compatible and can be used as a backend for Continue.dev and Cursor.

## Continue.dev

Continue.dev uses `config.yaml` (v0.8+). The file is located at:

- **Linux / macOS**: `~/.continue/config.yaml`
- **Windows**: `%USERPROFILE%\.continue\config.yaml`

### config.yaml

Replace every `<PLACEHOLDER>` before saving.

| Placeholder | Where to find it |
|-------------|-----------------|
| `<YOUR-API-KEY>` | Admin → Users → your user → API Keys tab |
| `<YOUR-BASE-URL>` | The public API URL of your MoE Sovereign instance (e.g. `https://api.example.org/v1`) |
| `<CHAT-TEMPLATE>` | Admin → Expert Templates — pick a template for general chat |
| `<AUTOCOMPLETE-TEMPLATE>` | Admin → Expert Templates — pick a fast/concise template for tab autocomplete |
| `<REVIEW-TEMPLATE>` | Admin → Expert Templates — pick a code-review template (optional) |

```yaml
models:
  - title: MoE Sovereign — Chat
    provider: openai
    model: <CHAT-TEMPLATE>
    apiBase: <YOUR-BASE-URL>
    apiKey: <YOUR-API-KEY>

  - title: MoE Sovereign — Code Review
    provider: openai
    model: <REVIEW-TEMPLATE>
    apiBase: <YOUR-BASE-URL>
    apiKey: <YOUR-API-KEY>

tabAutocompleteModel:
  title: MoE Sovereign — Autocomplete
  provider: openai
  model: <AUTOCOMPLETE-TEMPLATE>
  apiBase: <YOUR-BASE-URL>
  apiKey: <YOUR-API-KEY>
```

### Minimal setup (single model)

If you only want to use one template for everything:

```yaml
models:
  - title: MoE Sovereign
    provider: openai
    model: <CHAT-TEMPLATE>
    apiBase: <YOUR-BASE-URL>
    apiKey: <YOUR-API-KEY>
```

## Cursor

### Settings → Models → Add Model

```
Provider: OpenAI Compatible
Base URL:  <YOUR-BASE-URL>
API Key:   <YOUR-API-KEY>
Model:     <CHAT-TEMPLATE>
```

## Notes

- **Streaming**: Fully supported
- **Function Calling**: Supported (tool use in agent templates)
- **Vision**: Image analysis via Base64 available
- **Context Window**: Depends on the chosen template and underlying model — check the template definition in the Admin UI
- **Template names**: Use the exact template identifier shown in Admin → Expert Templates (the ID, not the display name)

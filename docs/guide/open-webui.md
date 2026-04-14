# Open WebUI & Chat

## Direct Access

The chat interface is available at:
[chat.moe-sovereign.org](https://chat.moe-sovereign.org)

Log in with your portal account ([portal.moe-sovereign.org](https://portal.moe-sovereign.org)).

## Configure Open WebUI Connection

If you want to connect your own Open WebUI instance to MoE Sovereign:

### Settings → Admin Panel → Connections

**OpenAI API:**
```
Base URL: https://api.moe-sovereign.org/v1
API Key:  <YOUR-API-KEY>
```

After saving, all `moe-orchestrator-*` models will appear in the model selection.

## Model Selection in Open WebUI

| Model ID | Recommended for |
|-----------|---------------|
| `moe-orchestrator` | General chats |
| `moe-orchestrator-research` | Research queries |
| `moe-orchestrator-code` | Code assistance |
| `moe-orchestrator-concise` | Quick short answers |
| `moe-orchestrator-report` | Structured reports |

## System Prompts in Open WebUI

You can configure custom system prompts in Open WebUI.
MoE Sovereign respects the system prompt and passes it to the merger.

**Example system prompt for code focus:**
```
You are an experienced software developer. Always respond with concrete,
executable code. Prefer Python and TypeScript. Do not explain basics.
```

## Features in Open WebUI

- **Model selection**: All MoE models directly selectable
- **Streaming**: Responses streamed live
- **Image analysis**: Upload images for the `vision` expert
- **Conversation**: Chat history managed server-side
- **Document upload**: PDFs and text processed before the request

## Give Feedback

A thumbs up/down can be given after each response.
This feedback flows directly into the [self-correction system](../system/dataflow.md#feedback-loop-valkey-expert-scoring).

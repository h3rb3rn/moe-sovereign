# Kontext-bezogene Variablen — Gesamte Codebase

> Automatisch generiert aus der aktuellen Codebase. Alle Variablen, die mit Context-Window, Token-Budget, LLM-Parametern oder Streaming zu tun.

---

## 1. Umgebungsvariablen (.env / .env.example / config.py)

| Variable | Standardwert | Bedeutung | Verwendet in |
|---|---|---|---|
| `MAX_EXPERT_TOKENS` | `4096` | Hardcap Expert-LLM-Ausgabetokens | `config.py`, `main.py` |
| `MAX_EXPERT_TOKENS_CODE` | `16384` | Hardcap Code-Expert-Ausgabetokens | `config.py`, `main.py` |
| `MAX_JUDGE_TOKENS` | `32768` | Hardcap Judge-Ausgabetokens | `config.py`, `main.py` |
| `MAX_PLANNER_TOKENS` | `16384` (→ 8192 in config.py) | Hardcap Planner-Ausgabetokens | `config.py`, `main.py` |
| `JUDGE_NUM_CTX` | `0` (auto) | Context-Window Judge-Modell | `config.py`, `main.py`, `.env` |
| `PLANNER_NUM_CTX` | `0` (auto) | Context-Window Planner-Modell | `config.py` |
| `AGENTIC_GAP_THRESHOLD_TOKENS` | `80000` | Ab hier wird agentic Gap-Detection übersprungen | `config.py` |
| `WM_EXTRACT_THRESHOLD_TOKENS` | `90000` | Ab hier wird Working-Memory-Extraktion übersprungen | `config.py` |
| `MAX_EXPERT_OUTPUT_CHARS` | `2400` | Max Expert-Ausgabe in Zeichen | `config.py`, `dashboard.html` |
| `MAX_GRAPH_CONTEXT_CHARS` | `6000` | Max Graph-Kontext in Zeichen | `.env.example` |
| `CC_SAFETY_BUFFER_TOKENS` | `500` | Safety-Buffer für Token-Kalkulation | `.env.example` |
| `EXPERT_CHARS_PER_TOKEN` | `3` | Zeichen-pro-Token-Faktor | `.env.example` |
| `CC_CONTEXT_INDEX_ENABLED` | `true` | Context-Index-Modus aktivieren | `config.py` |
| `EXPERT_MODELS` | `{"general":[...]}` | Model-Endpunkt-Zuweisung | `.env.example` |
| `TOKEN_PRICE_EUR` | `0.00002` | Token-Preis pro EUR | `.env.example` |

---

## 2. Profile-DB-Felder (CC Profile JSON / Admin UI)

| Feld | Default | Typ | Profile-Werte | Verwendet in |
|---|---|---|---|---|
| `tool_model` | — | String | `gemma4:31b`, `Qwen3-Coder-Next-GGUF:Q4_0` | Alle Profile |
| `tool_max_tokens` | `8192` | Int | `8192` (meiste), `4096` (innovator-fast) | `admin_ui/app.py`, state.py, profiles.html |
| `context_window` | `0` (auto) | Int | `32768` (alle Profile) | `admin_ui/app.py`, state.py, profiles.html, planner.py |
| `reasoning_max_tokens` | `16384` | Int | `16384`, `4096`, `32768` | `admin_ui/app.py`, state.py, profiles.html |
| `tool_choice` | `"auto"` | String | `"required"` (alle Profile) | `admin_ui/app.py`, state.py, profiles.html, user_portal.html |
| `moe_mode` | — | String | `native`, `moe_reasoning`, `moe_orchestrated` | cc_profiles JSON |
| `stream_think` | `false` | Bool | `true` (innovator-*), `false` (cc-ref-*) | profiles.html, user_portal.html |
| `accepted_models` | — | Array | `["claude-sonnet-4-*"]` | user_portal.html |
| `expert_template_id` | — | String | `cc-expert-deep/balanced/fast` | innovator-* Profiles |
| `system_prompt_prefix` | `""` | String | leer oder Prefix | innovator-* Profiles |
| `tool_endpoint` | `""` | String | leer (alle) | profiles.html, user_portal.html |

---

## 3. LangGraph AgentState (pipeline/state.py)

| Feld | Default | Typ | Bedeutung |
|---|---|---|---|
| `tool_max_tokens` | `0` | Int | Aus Profil — wird an LLM übergeben |
| `context_window` | `0` | Int | Aus Profil — `0` = auto-detect |
| `reasoning_max_tokens` | `0` | Int | Aus Profil — reasoning output tokens |
| `tool_choice` | `"auto"` | String | Aus Profil |
| `planner_model_override` | `""` | String | Overrides planner model |
| `input_tokens` | `0` | Int | Kumulierte Input-Tokens aller Calls |
| `output_tokens` | `0` | Int | Kumulierte Output-Tokens |
| `prompt_tokens` | `0` | Int | Gleich `input_tokens` |
| `completion_tokens` | `0` | Int | Gleich `output_tokens` |
| `total_tokens` | `0` | Int | `prompt_tokens + completion_tokens` |
| `context_index` | `None` | Bool/None | Deprecated context-index Flag |

---

## 4. CCSession dataclass (pipeline/state.py)

| Feld | Default | Typ |
|---|---|---|
| `tool_max_tokens` | `0` | Int |
| `context_window` | `0` | Int |
| `tool_choice` | `"auto"` | String |
| `reasoning_max_tokens` | `0` | Int |

---

## 5. OpenAI ChatCompletion API-Parameter

| Parameter | Default | Typ | Pydantic Model |
|---|---|---|---|
| `max_tokens` | `None` | Int/None | `ChatCompletionRequest` (main.py:448, chat.py:105) |
| `temperature` | `None` | Float/None | `ChatCompletionRequest` (main.py:447, chat.py:104) |
| `tool_choice` | `None` | String/None | `ChatCompletionRequest` (main.py:446, chat.py:103) |
| `tools` | `None` | Array/None | `ChatCompletionRequest` (chat.py:102) |
| `max_output_tokens` | `None` | Int/None | `_ResponsesRequest` (responses.py:93) |
| `previous_response_id` | `None` | String/None | `_ResponsesRequest` (responses.py:96) |

---

## 6. Ollama API-Optionen (main.py CC warmup/keepalive)

| Option | Default | Quelle | Zeilen |
|---|---|---|---|
| `num_ctx` | `32768` (wenn `JUDGE_NUM_CTX` ≤ 0) | `_jnctx` aus config.py | main.py:1040, 1050, 1087, 1099 |

---

## 7. Token Accounting / Logging / Neo4j

| Feld | Quelle | Bedeutung |
|---|---|---|
| `input_tokens` / `output_tokens` | Anthropic SSE `message_start` (anthropic.py:455) | Streaming-Tokens |
| `prompt_tokens` / `completion_tokens` | `main.py:1620-1621, 1632` | Log-Felder |
| `total_tokens` | `episodic_memory.py:214` — `prompt_tokens + completion_tokens` | Neo4j Episode Store |

---

## 8. Routing-Hints (complexity_estimator.py)

| Feld | Wert | Bedeutung |
|---|---|---|
| `skip_thinking` | `true` / `false` | Routing-Hint an Planner — `true` für trivial/moderate/research, `false` für complex |

---

## 9. Admin UI Dashboard-Formulare

| Feld | Default | Typ |
|---|---|---|
| `JUDGE_TIMEOUT` | `900` | Sekunden |
| `EXPERT_TIMEOUT` | `900` | Sekunden |
| `PLANNER_TIMEOUT` | `300` | Sekunden |
| `PLANNER_RETRIES` | `2` | Int |
| `TOOL_MAX_TOKENS` | `8192` | Int |
| `REASONING_MAX_TOKENS` | `16384` | Int |
| `MAX_EXPERT_OUTPUT_CHARS` | `2400` | Int |

---

## 10. Planner-Kontextbudget (graph/planner.py)

| Variable | Default | Quelle | Bedeutung |
|---|---|---|---|
| `num_ctx` (planner, Admin UI) | `0` = auto | `expert_templates.html:215,225` | Auto-detect vom Modell |
| `_planner_ctx_budget()` | Funktion | `planner.py:88` | Leitet Zeichen-Budgets aus Context-Window ab |
| `ctx_tokens` | `state_num_ctx` → `PLANNER_NUM_CTX` → statisch | `planner.py:100` | Resolve-Kette für Planner-Context |

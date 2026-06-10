# Kontext-bezogene Variablen — Gesamte Codebase

> Automatisch generiert aus der aktuellen Codebase. Alle Variablen, die mit Context-Window, Token-Budget, LLM-Parametern oder Streaming zu tun.

---

## 1. Umgebungsvariablen (.env / .env.example / config.py)

| Variable | Standardwert | Bedeutung | Verwendet in |
|---|---|---|---|
| `MAX_EXPERT_TOKENS` | `4096` | Hardcap Expert-LLM-Ausgabetokens | `config.py`, `main.py` |
| `MAX_EXPERT_TOKENS_CODE` | `16384` | Hardcap Code-Expert-Ausgabetokens | `config.py`, `main.py` |
| `MAX_JUDGE_TOKENS` | `32768` | Hardcap Judge-Ausgabetokens | `config.py`, `main.py` |
| `MAX_PLANNER_TOKENS` | `16384` | Hardcap Planner-Ausgabetokens | `config.py`, `main.py` |
| `JUDGE_NUM_CTX` | `0` (auto) | Context-Window Judge-Modell | `config.py`, `main.py`, `.env` |
| `PLANNER_NUM_CTX` | `0` (auto) | Context-Window Planner-Modell | `config.py` |
| `AGENTIC_GAP_THRESHOLD_TOKENS` | `80000` | Ab hier wird agentic Gap-Detection übersprungen | `config.py` |
| `WM_EXTRACT_THRESHOLD_TOKENS` | `90000` | Ab hier wird Working-Memory-Extraktion übersprungen | `config.py` |
| `MAX_EXPERT_OUTPUT_CHARS` | `2400` | Max Expert-Ausgabe in Zeichen | `config.py`, `dashboard.html` |
| `MAX_GRAPH_CONTEXT_CHARS` | `6000` | Max Graph-Kontext in Zeichen | `.env.example` |
| `CC_SAFETY_BUFFER_TOKENS` | `1000` | Safety-Buffer für Token-Kalkulation | `config.py:205`, `services/pipeline/anthropic.py` |
| `EXPERT_CHARS_PER_TOKEN` | `3` | Zeichen-pro-Token-Faktor | `.env.example` |
| `CC_CONTEXT_INDEX_ENABLED` | `false` | Tier-3 Context-Index aktivieren (siehe [Tier-3 Context Index](#13-tier-3-context-index-status)) | `config.py` |
| `EXPERT_MODELS` | `{"general":[...]}` | Model-Endpunkt-Zuweisung | `.env.example` |
| `TOKEN_PRICE_EUR` | `0.00002` | Token-Preis pro EUR | `.env.example` |
| `EXPERT_OUTPUT_DIVISOR` | `4` | Reserviert `1/N` des Expert-Context-Window für Output | `config.py:199`, `graph/expert.py` |
| `EXPERT_INPUT_MIN_CHARS` | `2000` | Untergrenze für Expert-Input/Output bei sehr kleinem `context_window` | `config.py:200`, `graph/expert.py` |
| `CC_HISTORY_COMPRESS_THRESHOLD` | `8000` | Token-Schwelle, ab der `_compress_history_responses()` ältere Tool-Antworten kürzt | `config.py:266` |
| `CC_HISTORY_COMPRESS_KEEP_TURNS` | `8` | Anzahl der jüngsten Turns, die `_compress_history_responses()` unverkürzt lässt | `config.py:267` |
| `GRAPH_COMPRESS_LLM` | `""` (leer = aus) | Modell für `_compress_graph_context_llm()` (Graph-Kontext-Kompression) | `config.py:366`, `graph/research.py` |
| `GRAPH_COMPRESS_LLM_TIMEOUT` | `3.0` | Timeout (s) für `GRAPH_COMPRESS_LLM` | `config.py:367` |
| `CC_HISTORY_COMPRESS_LLM` | `= GRAPH_COMPRESS_LLM` (Fallback) | Modell für Summarization-on-Drop (siehe [Summarization-on-Drop](#14-summarization-on-drop)) | `config.py:374`, `services/pipeline/anthropic.py` |
| `CC_HISTORY_COMPRESS_LLM_TIMEOUT` | `= GRAPH_COMPRESS_LLM_TIMEOUT` (Fallback) | Timeout (s) für `CC_HISTORY_COMPRESS_LLM` | `config.py:375`, `services/pipeline/anthropic.py` |
| `CONTEXT_INDEX_EMBED_URL` | `http://moe-embed:11434` | Ollama-Endpoint für Tier-3-Embeddings (moe-embed Sidecar) | `services/context_index.py` |
| `CONTEXT_INDEX_EMBED_MODEL` | `nomic-embed-text` | Embedding-Modell für Tier-3-Indexierung | `services/context_index.py` |
| `CONTEXT_MAX_CHUNKS` | `200` | Max. Chunks pro Session-Index (OOM-Schutz) | `services/context_index.py` |
| `CONTEXT_INDEX_BATCH_SIZE` | `16` | Batch-Größe für ChromaDB-Upsert (OOM-Schutz) | `services/context_index.py` |

---

## 2. Profile-DB-Felder (CC Profile JSON / Admin UI)

| Feld | Default | Typ | Profile-Werte | Verwendet in |
|---|---|---|---|---|
| `tool_model` | — | String | `gemma4:31b`, `Qwen3-Coder-Next-GGUF:Q4_0` | Alle Profile |
| `tool_max_tokens` | `8192` | Int | `8192` (meiste), `4096` (innovator-fast) | `admin_ui/app.py`, state.py, profiles.html |
| `context_window` | `0` (auto) | Int | `32768` (alle Profile, explizit gesetzt) | `admin_ui/app.py`, state.py, profiles.html, planner.py |
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
| `_planner_ctx_budget()` | Funktion | `planner.py:88` | Leitet Zeichen-Budgets aus Context-Window ab (60/40-Split Prompt-Sektionen, **nicht** über `resolve_io_budget` — siehe [context_budget.py — Funktionen](#12-context_budgetpy-funktionen)) |
| `ctx_tokens` | `state_num_ctx` → `PLANNER_NUM_CTX` → statisch | `planner.py:100` | Resolve-Kette für Planner-Context |

> `_judge_ctx_budget()` (`graph/synthesis.py:88`) folgt demselben Muster wie
> `_planner_ctx_budget()` — ein fester Prozent-Split zwischen Merger-Prompt und
> dynamischem Inhalt — und wurde aus demselben Grund **nicht** auf
> `resolve_io_budget()` umgestellt.

---

## 11. `context_window` Auto-Resolution-Kette

Wenn `context_window` (Profil-Feld) `0` ist, löst `get_model_ctx_async()`
(`context_budget.py:296`) den tatsächlichen Wert in vier Stufen auf:

1. **Override** — `context_window` (Profil) bzw. `JUDGE_NUM_CTX` /
   `PLANNER_NUM_CTX` (env), falls `> 0` → wird sofort zurückgegeben.
2. **Redis-Cache** — Key `moe:ctx:{sha256(base_url)[:12]}:{model}`. TTL `120s`
   für Ollama-Knoten (Allokation kann sich zwischen Requests ändern), `3600s`
   für OpenAI-kompatible/Remote-Knoten (statisch).
3. **Live-Abfrage** — `fetch_ollama_num_ctx()` (`/api/show`) für lokale
   Ollama-Knoten, sonst `fetch_openai_context_window()`
   (`/v1/models/{id}` bzw. LiteLLM-Modellinfo).
4. **Statische Heuristik** — `get_model_context_window()`
   (`context_budget.py:134`) extrahiert die Parametergröße aus dem Modellnamen
   (z. B. `qwen3:35b` → `35.0`) und mapt sie über `_PARAM_CTX_HEURISTIC` auf
   ein konservatives Context-Window. Modelle ≥ 25 B werden auf `32768`
   gedeckelt (`_DEFAULT_CTX_HEURISTIC = 32768` für > 70 B oder unbekannt).

**Aktueller Stand:** Alle CC-Profile (`configs/cc_profiles/*.json`) setzen
`context_window` explizit auf `32768` — Stufe 1 (Override) greift in der Praxis
immer, die Stufen 2–4 sind der Fallback für Templates ohne explizite Angabe
(z. B. Expert-Templates mit `context_window: 0`).

---

## 12. `context_budget.py` — Funktionen

### Konstanten

| Konstante | Wert | Bedeutung |
|---|---|---|
| `CHARS_PER_TOKEN` | `4` | Konservative Zeichen-pro-Token-Schätzung (Prosa, Englisch/Deutsch gemischt) |
| `_CC_TOOL_CHARS_PER_TOKEN` | `3` (hardcoded in `services/pipeline/anthropic.py:111`) | Schätzung für Code-lastigen CC-Traffic — **kein** env-Override |
| `MERGER_FIXED_TOKENS` | `3500` | Fixer Overhead für Merger-Prompt (Systemanweisung + Modus-Präfix + 4× Expert-Antworten + Diverses) |
| `MERGER_HEADROOM_TOKENS` | `2000` | Reservierter Output-Headroom für den Merger |
| `MIN_GRAPHRAG_CHARS` | `800` | Untergrenze für GraphRAG-Kontext, auch bei sehr kleinen Modellen |
| `DEFAULT_GRAPHRAG_CHARS` | `6000` | Fallback, wenn das Modell-Context-Window unbekannt ist (entspricht `MAX_GRAPH_CONTEXT_CHARS`-Default) |
| `MIN_OUTPUT_BUDGET_TOKENS` | `1024` | Output-Untergrenze in `resolve_io_budget()` — darunter ist eine Antwort nicht mehr nützlich |

### Funktionen

| Funktion | Signatur | Bedeutung |
|---|---|---|
| `get_model_context_window(model)` | `(str) -> int` | Synchrone Heuristik (Stufe 4 der Resolution-Kette), nutzt `_PARAM_CTX_HEURISTIC` |
| `get_model_ctx_async(model, base_url, token, redis_client, override)` | `(...) -> int` | Volle 4-Stufen-Resolution-Kette (siehe Abschnitt 11) |
| `get_model_max_output_async(model, base_url, token, redis_client)` | `(...) -> int` | Analoge Resolution-Kette für `max_output_tokens` des Modells |
| `graphrag_budget_chars(model, query_chars, override_chars)` | `(...) -> int` | Max. Zeichen für GraphRAG-Injektion; `override_chars`: `>0` = explizites Limit, `0`/`-1` = auto aus Context-Window |
| `web_research_budget(model, query_chars, graphrag_chars_used)` | `(...) -> (max_blocks, max_block_chars)` | Adaptive Block-Anzahl/-Größe für Web-Research-Kompression, skaliert mit Context-Window |
| `resolve_io_budget(ctx_tokens, desired_max_tokens, *, static_overhead_tokens=0, chars_per_token=CHARS_PER_TOKEN, safety_buffer_tokens=0, min_output_tokens=0, min_input_ratio=0.5)` | `(...) -> dict` | Generischer Input/Output-Split (siehe unten) |
| `estimate_overflow(estimated_input_tokens, desired_max_tokens, ctx_tokens, safety_buffer_tokens=0)` | `(...) -> bool` | Reiner Pre-Flight-Check: `input + output + buffer > ctx`? |

### `resolve_io_budget()` — Algorithmus

Generalisierung des bereits deployten CC-Tool-Budget-Splits, in drei Stufen:

1. **Cap (Output begrenzen)**: `min_input_budget = min(desired_max_tokens, ctx_tokens * min_input_ratio)`;
   `max_allowed_out = ctx_tokens - min_input_budget - safety_buffer_tokens`.
   Ist `desired_max_tokens > max_allowed_out`, wird `max_output_tokens` darauf
   gekappt.
2. **Input-Budget ableiten**: `avail_input_tokens = ctx_tokens - max_output_tokens
   - safety_buffer_tokens - static_overhead_tokens` (geclamped auf `≥ 0`,
   `overflow=True` falls negativ vor dem Clamp).
3. **Output-Floor (nur falls `min_output_tokens > 0`)**: ist `max_output_tokens
   < min_output_tokens`, wird auf den Floor angehoben und `avail_input_tokens`
   neu berechnet (kann erneut `overflow=True` setzen).

Rückgabe: `{"max_output_tokens", "avail_input_tokens", "avail_input_chars",
"overflow", "static_overhead_tokens"}`.

`ctx_tokens <= 0` → `desired_max_tokens` unverändert, `avail_input_tokens=0`,
`overflow=False` (kein Context-Window bekannt → keine Kappung).

**Verwendet in:**

| Call-Site | `min_output_tokens` | `min_input_ratio` | Zweck |
|---|---|---|---|
| `services/pipeline/anthropic.py` (1. Narrowing, ~Z. 750) | `0` | `0.5` | Output auf `max_tokens` cappen, mind. 50 % Input-Budget reservieren |
| `services/pipeline/anthropic.py` (2. Narrowing, ~Z. 944) | `MIN_OUTPUT_BUDGET_TOKENS` | `0.5` | Finales Input-Budget für `_trim_oai_to_budget_async()`, inkl. Output-Floor |
| `graph/expert.py` (`_expert_max_output`) | `EXPERT_INPUT_MIN_CHARS // EXPERT_CHARS_PER_TOKEN` | `1 - 1/EXPERT_OUTPUT_DIVISOR` | Expert-Output-Cap relativ zum Expert-Context-Window |
| `graph/synthesis.py` (`_max_input_chars`) | `MERGER_HEADROOM_TOKENS` | `0.5` | Merger-Input-Budget nach Abzug von `MERGER_FIXED_TOKENS` |

---

## 13. Tier-3 Context Index Status

`services/context_index.py` implementiert die "1M+"-Kontext-Erweiterung
(Chunking + ChromaDB-Indexierung großer `system_prompt`-Inhalte pro Session,
mit semantischer Retrieval pro Expert-Aufruf). Übersicht der Tiers:
[docs/system/memory.md](memory.md).

**Status:** `CC_CONTEXT_INDEX_ENABLED=false` (Default). War deaktiviert, weil
`_get_embedding_function()` ursprünglich ChromaDBs `DefaultEmbeddingFunction()`
nutzte — ein In-Process-ONNX-Modell (~90 MB + Laufzeit-Arena), das im
4 GiB-limitierten `langgraph-app`-Container OOM-Kills auslöste.

**Fix (umgesetzt):** `_get_embedding_function()` nutzt jetzt `HttpxOllamaEF`
(`memory_retrieval.py`) — reines `httpx`/`numpy`, ruft `nomic-embed-text` über
den `moe-embed`-Sidecar per HTTP auf. Keine Embedding-Modell-Last mehr im
Orchestrator-Container. Zusätzlich:

- `MAX_CHUNKS_PER_INDEX` (`CONTEXT_MAX_CHUNKS`, Default `200`) deckelt die
  Anzahl Chunks pro Session-Index.
- `INDEX_BATCH_SIZE` (`CONTEXT_INDEX_BATCH_SIZE`, Default `16`) batcht die
  ChromaDB-`upsert()`-Aufrufe.
- `langgraph-app` Memory-Limit `4G → 6G` (`docker-compose.yml`) als zusätzliche
  Sicherheitsmarge.

**Reaktivierung:** `CC_CONTEXT_INDEX_ENABLED=true` in `.env` setzen, nach
Verifikation, dass `docker stats langgraph-orchestrator chromadb-vector
moe-embed` stabil bleibt (siehe Verifikations-Schritt 5 im Implementierungsplan).

**Relevante Schwellen:** `CONTEXT_INDEX_THRESHOLD` (Default `20000` Zeichen,
ab wann indexiert wird statt verbatim durchgereicht), `CONTEXT_CHUNK_SIZE`
(`1500`), `CONTEXT_CHUNK_OVERLAP` (`200`), `CONTEXT_RETRIEVAL_TOP_K` (`8`),
`CONTEXT_INDEX_TTL_SECS` (`14400`).

---

## 14. Summarization-on-Drop

Wenn `_trim_oai_to_budget_async()` (`services/pipeline/anthropic.py`) wegen
`avail_input_tokens` (aus `resolve_io_budget()`, 2. Narrowing) Nachrichtengruppen
aus der History entfernen muss, werden die entfernten Gruppen — statt sie
stillschweigend zu verwerfen — optional über `_summarize_dropped_groups_llm()`
zusammengefasst (Modell nach `_compress_graph_context_llm()`,
`graph/research.py:361-394`).

**Ablauf:**

1. `_trim_oai_to_budget_impl()` liefert `(kept_msgs, dropped_flag, dropped_groups)`
   — identischer Trim-Algorithmus wie zuvor, sammelt zusätzlich die entfernten
   Gruppen.
2. Falls `dropped_flag` und `CC_HISTORY_COMPRESS_LLM` gesetzt:
   `_summarize_dropped_groups_llm(dropped_groups)` fasst `[role] content[:500]`-Zeilen
   (auf 8000 Zeichen gekappt) über `ChatOpenAI(model=CC_HISTORY_COMPRESS_LLM, ...)`
   zusammen, mit `asyncio.wait_for`-Timeout (`CC_HISTORY_COMPRESS_LLM_TIMEOUT`).
3. Bei Erfolg: `_append_dropped_history_summary()` schreibt die Zusammenfassung
   nach `cc:work:{session_id}["dropped_history_summary"]` (Liste, max. 5 Einträge,
   je max. 1500 Zeichen).
4. `_inject_cc_work_context()` rendert diese Liste als
   `[CONTEXT TRIMMED — summary of removed conversation history]`-Block im
   System-Prompt des nächsten Requests.

**Fallback:** Ist `CC_HISTORY_COMPRESS_LLM` leer (Default, da `GRAPH_COMPRESS_LLM`
ebenfalls leer ist) oder schlägt die Zusammenfassung fehl/timeout, bleibt das
Verhalten exakt wie zuvor — reines Drop ohne Zusammenfassung.

**Abgrenzung zu `_compress_history_responses()`:** Diese (unveränderte) Funktion
ist eine frühere, komplementäre Stufe — sie kürzt einzelne Tool-Antworten
(Head/Tail-Truncation) ab `CC_HISTORY_COMPRESS_THRESHOLD` Tokens, behält aber die
letzten `CC_HISTORY_COMPRESS_KEEP_TURNS` Turns unverkürzt, und schreibt ihre
Kürzungen ebenfalls nach `cc:work` (über `_update_cc_work_summary`).
Summarization-on-Drop greift erst danach, wenn trotz dieser Kürzung noch
Nachrichten aus dem `avail_input_tokens`-Budget fallen.

---

## 15. Pre-flight Overflow Monitoring

`estimate_overflow(estimated_input_tokens, desired_max_tokens, ctx_tokens,
safety_buffer_tokens)` prüft *vor* dem Dispatch, ob
`input + output + buffer > ctx_tokens` gelten würde. Bei `True`:

- Ein `logger.warning(...)` mit den konkreten Token-Zahlen wird geschrieben.
- `PROM_BUDGET_EXCEEDED` (`metrics.py`, `Counter` mit Labels `user_id`,
  `limit_type`) wird inkrementiert.

| `limit_type` | Call-Site | Bedeutung |
|---|---|---|
| `cc_tool_preflight` | `services/pipeline/anthropic.py` (nach `_budget2`) | CC-Tool-Request würde Context-Window sprengen — triggert zusätzlich `services.context_index.ensure_indexed()`, falls `CC_CONTEXT_INDEX_ENABLED=true` |
| `expert_preflight` | `graph/expert.py` (`expert_worker`) | Expert-Context-Window zu klein für `_max_output_cap` |
| `merger_preflight` | `graph/synthesis.py` (Merger-Input-Budget) | Merger-Context-Window zu klein für `MERGER_FIXED_TOKENS + MERGER_HEADROOM_TOKENS` |

Dashboards/Alerts auf `moe_budget_exceeded_total` sollten nach `limit_type`
aufschlüsseln, um die drei Pfade getrennt zu beobachten.

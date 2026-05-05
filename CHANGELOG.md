# Changelog

Alle wesentlichen Änderungen am Sovereign MoE Orchestrator werden hier dokumentiert.
Format basiert auf [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.3.0] - 2026-05-05

### Added
- **Claude Desktop & Cowork Gateway compatibility** (Anthropic Third-Party Inference spec):
  - `display_name` field in all `/v1/models` entries — Claude Desktop displays human-readable names (e.g. "Claude Sonnet 4.6 → MoE (Gateway)") in the model picker
  - `POST /v1/messages/count_tokens` endpoint — satisfies the Anthropic Gateway spec requirement; returns a character-based token estimate (chars / 3.5) used by Claude Code for context-budget warnings
  - `X-Claude-Code-Session-Id` header now recognised in `_extract_session_id` (highest priority) for accurate session tracking from Claude Desktop
  - `_model_display_name()` helper with curated pretty-names for all current Claude model IDs
  - `scripts/setup-claude-desktop.sh` — interactive setup wizard that auto-configures `claude_desktop_config.json` on macOS, Linux, and WSL; checks connectivity, creates backup, merges gateway block

### Changed
- `/v1/models` response: all model objects now include the `display_name` field (templates, MoE modes, CC aliases, native node models, user connections)

---

## [2.2.0] - 2026-04-25

### Added
- **8 new MCP tools** (51 total): `wikidata_sparql`, `pubmed_search`, `crossref_lookup`, `openalex_search`, `duckduckgo_search`, `web_browser` (Splash JS rendering), `wayback_fetch`, `openalex_search`
- **`github_search_issues` fuzzy label resolution**: auto-resolves `Regression` → `05/06 - Regression` by fetching repo labels; OR-semantics across all matching variants
- **`openalex_search` year_max parameter** for precise publication year filtering
- **`wayback_fetch` dual-strategy**: primary availability API + direct URL fallback for reliable archive.org access
- **Pre-run smoke test** (`benchmarks/pre_run_check.py`): 8-point health check before benchmark runs — container health, MCP tools, prompt length, Redis, AST syntax, E2E question
- **HEALTHCHECK for mcp-precision**: Docker healthcheck via `calculate('2+2')` in compose.yml
- **Domain-filtered tool descriptions**: `_TOOL_GROUP_CORE` and `_TOOL_GROUP_RESEARCH` control which tools the planner sees per query type
- **Level-adaptive benchmark timeouts**: L1=300s, L2=480s, L3=900s via `GAIA_TIMEOUT_L{1,2,3}` env vars
- **Security hardening**: SSRF guard, SQL identifier validation, container `cap_drop: ALL` + `no-new-privileges`

### Changed
- **GAIA Benchmark**: Best score 14/30 = 46.7% (exceeds GPT-4o Mini 44.8% reference)
- **Expert-leak detection** (`_EXPERT_LEAK_RE`): broader patterns — `attempt tool call`, `attempt to search/browse`, capability disclaimers in merger fallback
- **Planner prompt** compressed from 15K → 12K chars to prevent context overflow; consolidated deterministic source rules
- **Benchmark normalization**: slugline stripping (`INT. X – DAY` → `X`), `grave accent` → `backtick`, leak phrases in NO_ANSWER retry

### Fixed
- `wayback_fetch`: HTTP 200 `archived_snapshots: {}` no longer silently fails — falls back to direct `web.archive.org/web/2024/{url}` with `follow_redirects=True`
- `github_search_issues`: label name prefixes like `06 - Regression` resolved automatically
- Expert-leak `Attempt web search.` / `Attempt tool call.` now triggers runner retry
- Merger fallback excludes expert-leak answers from fallback pool

## [2.1.0] - 2026-03-29

### Added
- **Two-Tier Expert System**
  - `_infer_tier(model_name)`: extrahiert Modellgröße aus Name (`:14b`, `:32b`) → T1 ≤20B, T2 >20B
  - T1-Experten laufen zuerst (schnell); T2 nur wenn kein T1-Ergebnis `KONFIDENZ: hoch` liefert
  - Spart VRAM bei einfachen Anfragen, eskaliert zu Premiumberechnung bei Bedarf
- **Chat-History-Injection**
  - `_truncate_history()`: letzte 4 Gesprächsrunden, max. 3000 Zeichen
  - Alle Expert-LLMs erhalten den Gesprächskontext vor ihrer Aufgabe
  - `chat_history`-Feld im `AgentState` und API-Layer (extrahiert aus `request.messages`)
- **Citation Tracking**
  - `_web_search_with_citations()`: nutzt `search.results()` statt `search.run()`
  - Strukturierte Quellenangaben (Titel + URL) werden nummeriert an Web-Recherche-Ergebnis angehängt
  - Gilt für `research_node` und `research_fallback_node`
- **`thinking_node`** — neue Node zwischen `research_fallback` und `merger`
  - Aktiviert bei komplexen Plänen (>1 Task) oder wenn Experten `KONFIDENZ: niedrig` melden
  - Magistral:24b führt expliziten 4-Schritt Chain-of-Thought durch:
    Problemzerlegung → Quellenauswertung → Wissenslücken → Schlussfolgerung
  - Ausgabe als `reasoning_trace` fließt priorisiert in den Merger-Prompt
  - Erscheint im `<think>`-Panel von Open WebUI
- **Expert-Deduplication**
  - `_dedup_by_category()`: behält pro Kategorie nur das Ergebnis mit höchster Konfidenz
  - Verhindert Echo-Chamber wenn mehrere Experten derselben Kategorie widersprüchliche Antworten liefern
- **`critic_node`** — neue Node nach `merger`, vor `END`
  - Aktiv ausschließlich für `medical_consult` und `legal_advisor` (safety-critical)
  - Judge-LLM prüft Merger-Antwort auf faktische Fehler und gefährliche Aussagen
  - Bei `BESTÄTIGT`: Antwort unverändert; bei Fehler: korrigierte Version wird zurückgegeben
- **Neue Helper-Funktionen:** `_infer_tier`, `_dedup_by_category`, `_truncate_history`, `_web_search_with_citations`
- **`AgentState`** um `chat_history: List[Dict]` und `reasoning_trace: str` erweitert

### Changed
- **Graph-Topologie:** `research_fallback → thinking → merger → critic → END`
  (vorher: `research_fallback → merger → END`)
- **`expert_worker`:** Two-Tier-Logik ersetzt einfaches paralleles Fan-out; Chat-History in Experten-Messages
- **`merger_node`:** ruft `_dedup_by_category()` vor Prompt-Aufbau; `reasoning_trace` als priorisierter Abschnitt
- **`research_node` / `research_fallback_node`:** Citation-Tracking via `search.results()`
- **Judge-LLM:** `magistral:24b` (vorher: `gpt-oss:20b`) — Reasoning-fokussiert, 24B

### Expert-Modell-Upgrades

| Kategorie | Alt | Neu | Begründung |
|---|---|---|---|
| `general` [2] | `qwen3.5:27b` | `qwen3.5:35b` | +8B, tieferes Reasoning |
| `math` [2] | `mathstral:7b` | `qwq:32b` | T2-Eskalation mit echtem Math-Reasoning |
| `code_reviewer` [2] | `codestral:22b` | `qwen3-coder:30b` | Neuer, größer, stärkere Security-Analyse |
| `medical_consult` [2] | `gemma3:12b` | `gemma3:27b` | T2-Eskalation ergänzt (safety-critical) |
| `legal_advisor` [2] | `mistral-small:24b` | `command-r:35b` | Citation-aware RAG, §§-Zitierung |
| `translation` | solo | + `qwen3.5:35b` | Zweite Meinung, multilinguales Training |
| `reasoning` [1] | `magistral:24b` | `deepseek-r1:32b` | Kein Echo-Chamber mit Judge; echtes CoT |

- `deepseek-r1:32b` (19.9 GB) und `qwq:32b` (19.9 GB) via Ollama API gepullt

---

## [2.0.0] - 2026-03-29

### Added
- **Kafka-Integration** (`confluentinc/cp-kafka:7.7.0`, KRaft-Mode, kein Zookeeper)
  - Topic `moe.ingest`: GraphRAG-Ingest aus Fire-and-Forget zu persistentem Kafka-Event
  - Topic `moe.requests`: Vollständiger Audit-Log aller Anfragen und Cache-Hits
  - Topic `moe.feedback`: Feedback-Events für externe Consumer
  - `AIOKafkaProducer` mit 12-facher Retry-Logik (Backoff 5–60s)
  - `AIOKafkaConsumer` als permanenter asyncio-Background-Task (Group `moe-worker`)
  - Graceful Degradation: System funktioniert vollständig ohne Kafka
- **GraphRAG / Neo4j Knowledge Graph**
  - `GraphRAGManager`: async Neo4j-Client, 2-Hop-Traversal, Kontext-Abfrage
  - Basis-Ontologie: 104 Entitäten, 100 Relationen über 4 Domänen (Medical, Legal, Technical, Math)
  - Hintergrund-Ingest: Tripel-Extraktion aus Merger-Antworten via Judge-LLM
  - Konflikt-Detektion via `_CONTRADICTORY_PAIRS` (TREATS/CAUSES/CONTRAINDICATES)
  - `graph_rag_node`: paralleler LangGraph-Node, 2-Hop-Traversal je Anfrage
  - Domain-Filter: `AND e.type IN $allowed_types` in Cypher (verhindert Cross-Domain-Kontamination)
  - API-Endpunkte: `GET /graph/stats`, `GET /graph/search`
- **MCP Precision Tools Server** (`mcp_server/server.py`, Port 8003)
  - 16 deterministische Tools: calculate, solve_equation, date_diff, date_add, day_of_week,
    unit_convert, statistics_calc, hash_text, base64_codec, regex_extract, subnet_calc,
    text_analyze, prime_factorize, gcd_lcm, json_query, roman_numeral
  - Safe-AST-Evaluator für `calculate` (kein `eval()`)
  - REST-Shim (`POST /invoke`) für internen LangGraph-Zugriff
  - FastMCP SSE-Endpoint (`/mcp/sse`) für externe MCP-Clients
- **Selbstlern-Infrastruktur**
  - `POST /v1/feedback`: Rating 1–5, aktualisiert Expert-Scores, flaggt Cache-Einträge, verifiziert Neo4j-Tripel
  - Expert-Performance-Tracking in Redis (`moe:perf:{model}:{category}`, Laplace-Glättung)
  - Response-Metadaten in Redis (`moe:response:{id}`, TTL 7 Tage)
  - Cache-Lookup: 3 Kandidaten, überspringt `flagged=True`-Einträge
  - Expert-Sortierung nach Performance-Score (beste zuerst); Score < 0.3 → übersprungen
- **LangGraph Pipeline erweitert**
  - Neu: `mcp_node`, `graph_rag_node` (beide parallel im Fan-out)
  - Cache-Short-Circuit: Cosine-Distance < 0.15 → direkte Merger-Rückgabe
  - Expert-Output-Cap: `MAX_EXPERT_OUTPUT_CHARS=2400` (≈ 600 Tokens)
  - Stagger-Delay entfernt; VRAM-Semaphore reicht
  - `math_node`: aktiv nur wenn kein `precision_tools`-Task im Plan
  - `AgentState` um `response_id` und `expert_models_used` erweitert
- **Konfidenz-Scoring** (`KERNAUSSAGE / KONFIDENZ / DETAILS`-Format)
  - Experten strukturieren Output mit `KONFIDENZ: hoch | mittel | niedrig`
  - Merger berücksichtigt Konfidenz bei Quellpriorisierung
- **Research Fallback Node** — automatische Web-Recherche bei `KONFIDENZ: niedrig`
  - Für `medical_consult` und `legal_advisor` bei niedriger Konfidenz erzwungen
- **Output-Modi** — drei separate Modell-IDs für Open WebUI:
  - `moe-orchestrator`: vollständige Antworten (Standard)
  - `moe-orchestrator-code`: nur Quellcode, kein Fließtext
  - `moe-orchestrator-concise`: max. 120 Wörter
- **Token-Tracking** — akkumulierte `prompt_tokens` + `completion_tokens` über alle LLM-Calls
  - `_extract_usage()` aus `usage_metadata` (Ollama) oder `response_metadata`
  - Vollständige `usage`-Felder in Streaming- und Non-Streaming-Responses
- **`<think>`-Panel** für Open WebUI
  - `_progress_queue` via `contextvars.ContextVar` in alle Nodes vererbt
  - Fortschrittsberichte aus jedem Node erscheinen im „Denke nach"-Panel
  - SSE Keep-alive (`": keep-alive"`) verhindert Proxy-Timeouts
- **Open WebUI Internal-Request-Detection**
  - `_is_openwebui_internal()`: erkennt Follow-up/Title/Autocomplete-Prompts
  - `_handle_internal_direct()`: Fast-Path direkt zum Judge ohne MoE-Pipeline
  - Verhindert dass Open WebUI-interne Requests den Spinner nicht stoppen
- **Expert System-Prompts**: Rollenidentität pro Kategorie
- **`AsyncRedisSaver`**: LangGraph-Checkpoints persistent in Redis (`terra_cache`)
- **Streaming-Fix**: OpenAI-kompatibler Abschluss-Chunk mit `finish_reason: "stop"`
- **Experten-Modelle aktualisiert**: meditron/medllama2 (Llama-2-Basis) durch phi4:14b + gemma3:12b ersetzt; magistral:24b und devstral:24b für legal/code hinzugefügt; Kategorien `translation` und `reasoning` neu
- **GraphRAG Domain-Filter**: verhindert Cross-Domain-Kontamination (z.B. medizinische Entitäten bei technischen Anfragen)

### Changed
- `planner_node`: Prompt verschärft (strikteres Format), `_sanitize_plan()` validiert alle Task-Einträge
- `stream_response`: vollständig OpenAI-kompatibles Chunk-Format
- `chat_completions` (non-stream): vollständige Antwort mit allen OpenAI-Pflichtfeldern
- GraphRAG-Ingest: Von `asyncio.create_task` zu Kafka-Publish — persistenter, entkoppelt

### Fixed
- **Planner lieferte Strings statt Task-Dicts** → `_sanitize_plan()` fängt alle ungültigen Einträge ab
- **Neo4j `DEPRECATED_BY` + `UNRELATED_TO` Warnings** → aus `_CONTRADICTORY_PAIRS` entfernt
- **docker-compose doppelter `environment`-Block** → zusammengeführt

---

## [1.2.0] - 2026-02-12

### Added
- SymPy-Mathematik-Modul (`math_node.py`): Gleichungslösung, Vereinfachung, Ableitung, Integration

### Changed
- Expert Worker: verbesserter Stagger-Algorithmus `(t_idx * 3) + (e_idx * 1.5)` Sekunden
- GPU-Count dynamisch via Umgebungsvariable konfigurierbar
- Verbessertes Error-Handling mit spezifischer CUDA-OOM-Erkennung

### Fixed
- Checkpointer-Initialisierungsfehler (`_GeneratorContextManager`)
- Memory-Leak im Semaphore-Management des Expert Workers
- Mathematische Ausdrucks-Erkennung verbessert

---

## [1.1.0] - 2026-02-11

### Added
- LangGraph Multi-Model LLM-Orchestrierung (initiale Pipeline)
- Multi-Node GPU-Cluster-Support via Ollama
- Redis-Checkpoint-Persistenz für Graph-State
- ChromaDB Vektorspeicher für Knowledge-Caching
- SearXNG Web-Recherche-Integration
- Docker Compose Deployment
- OpenAI-kompatibler API-Endpunkt (`/v1/chat/completions`)
- Streaming-Response (SSE)

---

## [1.0.0] - 2026-02-10

### Added
- Projekt-Initialisierung mit LangGraph-Grundstruktur
- Kern-Nodes: cache_lookup, planner, expert_workers, merger
- Docker-Konfiguration
- Basis-Fehlerbehandlung und Logging

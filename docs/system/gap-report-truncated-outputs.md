# GAP Report: Truncated Outputs im Claude Code (CC) Tool-Pfad

> Ziel: Die unbekannte Variable identifizieren, die CC-Antworten auf 1–3 Zeichen kürzt.
> Alle Werte und Zustände aus dem aktuellen Commit (2026-06-09).

---

## 1. Umgebungsvariablen (.env / .env.example / config.py)

| Variable | Wert | Quelle | Rolle im CC-Pfad | GAP | Empfehlung |
|---|---|---|---|---|---|
| `MAX_EXPERT_TOKENS` | `4096` | config.py:170 | **NUR für Ollama-Experten** (expert.py:343). CC-Pfad liest diese Variable NICHT. | Kein Problem | Dokumentation aktualisieren, um zu verdeutlichen, dass diese Variable CC nicht betrifft. |
| `MAX_EXPERT_TOKENS_CODE` | `16384` | config.py:172 | **NUR für Ollama-Code-Experten**. CC-Pfad ignoriert. | Kein Problem | Siehe oben. |
| `MAX_JUDGE_TOKENS` | `32768` | config.py:174 | Nur für Judge-Expert-LLM, nicht CC. | Kein Problem | — |
| `MAX_PLANNER_TOKENS` | `8192` | config.py:176 | Nur Planner. CC verwendet `reasoning_max_tokens` für Reasoning-LLM (anthropic.py:1603). | Kein Problem | — |
| `TOOL_MAX_TOKENS` | `8192` | config.py:290 | Default wenn kein Profil-Wert. CC-Profil überschreibt normalerweise. | **Mittel** — siehe Profil-Check | Profil-Wert `tool_max_tokens=8192` muss das Limit setzen. Falls Profil nicht geladen wird, ist 8192 das Limit. |
| `CC_SAFETY_BUFFER_TOKENS` | `500` | config.py:205 | Line 618, 796, 809: Wird von context_window abgezogen. Bei kleinem Fenster kritisch. | **Niedrig** — nur 500 Tokens. Bei 32768 Fenster harmlos, bei 8192 nur ~1.5% | Auf 1000 erhöhen, um Margin zu geben. |
| `CC_CONTEXT_INDEX_ENABLED` | `true` | config.py:198 | Aktiviert Chunking von system_prompt (anthropic.py:1770). Kann system_prompt auf 2048 chars kürzen. | **Mittel** — wenn system_prompt wichtig ist | Prüfen, ob system_prompt-Kürzung das Problem verursacht. |
| `CC_HISTORY_COMPRESS_THRESHOLD` | `2000` | config.py:200 | Komprimiert History >2000 chars. | **Hoch** — sehr aggressiv! History wird vor der Token-Budget-Prüfung bereits komprimiert. | Auf 5000–8000 erhöhen. |
| `CC_HISTORY_COMPRESS_KEEP_TURNS` | `4` | config.py:201 | Letzte 4 Turns bleiben erhalten. | **Hoch** — nur 4 Turns × Tool-Calls = schnell voll | Auf 6–8 erhöhen. |

---

## 2. Profile-DB-Felder (CC Profile JSON / Admin UI)

| Feld | Default | Profil-Wert (cc-ref-native) | Rolle im CC-Pfad | GAP | Empfehlung |
|---|---|---|---|---|---|
| `tool_max_tokens` | `8192` | `8192` | anthropic.py:610 — `min(req_max_tokens, session.tool_max_tokens)` | **KRITISCH** — Das Limit, das den Output direkt caps | Prüfen: Ist es wirklich 8192? Oder wird es auf etwas Kleineres reduziert? |
| `context_window` | `0` (auto) | `32768` | anthropic.py:575 — `_profile_ctx`. Dient als Basis für `_max_allowed_out` und `_safe_max_out`. | **KRITISCH** — Wenn `0` (auto) und API-Aufruf fehlschlägt → `_tool_ctx=0` | Siehe GAP #ctx_zero. |
| `reasoning_max_tokens` | `16384` | `4096` | anthropic.py:1603 — Reasoning-Modell (für tool_use-Requests ohne Ollama-Endpoint). | Niedrig (4096), aber Reasoning-Modell antwortet selten mit >4k. | Auf 8192 erhöhen für komplexe Tool-Calls. |
| `tool_choice` | `"auto"` | `"required"` | anthropic.py:446, 587 | Kein Einfluss auf Output-Länge. | — | — |
| `moe_mode` | — | `native` | Bestimmt Routing-Pfad (tool_handler vs. moe_handler). | Kein Einfluss auf Output-Länge. | — | — |
| `stream_think` | `false` | `false` | anthropic.py:929–930 — Ollama `think:false` Flag. | Kein direkter Einfluss, aber denken-Modus kann `num_predict` fressen. | Prüfen ob `true` bei anderen Profilen problematisch. |

---

## 3. LangGraph AgentState (pipeline/state.py)

| Feld | Default | Rolle | GAP | Empfehlung |
|---|---|---|---|---|
| `tool_max_tokens` | `0` | Wird aus Profil geladen (anthropic.py:610). `0` = kein Profil-Limit, nur Request-Wert. | **Mittel** — Falls Profil nicht geladen wird, bleibt 0 und CC-Sende 32000. | Profil-Lade-Fehler loggen? |
| `context_window` | `0` | Wird aus Profil geladen. `0` = auto-detect via API. | **KRITISCH** — Siehe GAP #ctx_zero. | — |
| `reasoning_max_tokens` | `0` | Wird aus Profil geladen. `0` = config.py REASONING_MAX_TOKENS. | — | — |
| `tool_choice` | `"auto"` | Profil-Feld. | — | — |
| `input_tokens` | `0` | Kumuliert. Nur Logging/Tracking. | — | — |

---

## 4. CCSession dataclass (cc_session.py)

| Feld | Default | Quelle | GAP |
|---|---|---|---|
| `tool_max_tokens` | `0` | cc_session.py:208 — `int(profile.get("tool_max_tokens") or 0)` | Wenn Profil nicht gefunden → 0 → kein Limit über `min()`. |
| `context_window` | `0` | cc_session.py:209 | Wenn Profil nicht gefunden → 0 → `get_model_ctx_async` muss auto-detecten. |
| `tool_choice` | `"auto"` | cc_session.py:210 | — |
| `reasoning_max_tokens` | `0` | cc_session.py:212 | — |

---

## 5. Ollama API-Parameter (native path)

| Option | Wert | Quelle | GAP | Empfehlung |
|---|---|---|---|---|
| `num_ctx` | `_profile_ctx` (meist 32768) | anthropic.py:921 | **KRITISCH** — `num_predict` wird auf `max_tokens` gesetzt (gleicher Wert). | **GAP #num_predict_num_ctx_merge** — Ollama `num_predict` und `num_ctx` sind vermischt. Wenn der Model die Context-Grenze erreicht, wird es `done_reason=length` zurückgeben — und das kann bei vollem Input den Output auf 0–3 Tokens kürzen. |
| `num_predict` | `max_tokens` | anthropic.py:921 | **KRITISCH** — Siehe oben. | `num_predict` sollte ein eigenes Limit sein, nicht `num_ctx`. |

---

## 6. _trim_oai_to_budget — Der wahrscheinlichste Übeltäter

| Detail | Wert/Code | Bedeutung | GAP |
|---|---|---|---|
| `budget_chars` | `int(available_input_tokens * _CHARS_PER_TOKEN)` | `_CHARS_PER_TOKEN=4` (state.py, prose default) | **KRITISCH** — `_CHARS_PER_TOKEN=4` wird für CC-Nachrichten verwendet, die code/JSON/Tool-Calls enthalten! Diese sind ~3 chars/token (dicht). Die Schätzung ist **25% zu pessimistisch**. |
| `_avail_input` | `_tool_ctx - max_tokens - _CC_SAFETY_BUFFER` | anthropic.py:797 | Bei `_tool_ctx=32768, max_tokens=8192, SAFETY=500`: 24076 tokens available. Das sieht OK aus. |
| ABER: Wenn `_tool_ctx=0` (API-Fehler) | `min(TOOL_MAX_TOKENS, _tool_ctx // 2) = 0` | anthropic.py:617 | **GAP #ctx_zero** — Siehe unten. |
| Gruppen-basiertes Droppen | anthropic.py:128–146 | Dropt User+Tool-Message-Paare als Ganze. | **Mittel** — Das letzte Pair bleibt immer. Bei vielen Tool-Calls kann der letzte Pair noch mehrere KB enthalten. |

---

## 7. Critical Gaps — Zusammenfassung der 3 Hauptverdächtigen

### GAP #1: `_tool_ctx = 0` (Kontext-Fenster nicht auflösbar)

**Was passiert:**
- `context_window=0` im Profil → `get_model_ctx_async()` muss via API auflösen
- API-Aufruf (Ollama /api/show oder OpenAI /v1/models) fehlschlägt (Timeout, Netzwerk, Model-Not-Loaded)
- `get_model_ctx_async()` gibt `0` zurück (context_budget.py:239)
- Folge: `min(TOOL_MAX_TOKENS, 0) = 0`, `_max_allowed_out = 0` (line 617–618)
- `max_tokens` wird auf `0` gecapped → `_safe_max_out = max(256, 0 - 0 - 500)` = **256**
- Ollama bekommt `num_predict: 256` — sehr wenig für eine sinnvolle Antwort!

**Code-Pfade:**
- anthropic.py:555–561 (Context-Fetch)
- anthropic.py:617–618 (Budget-Berechnung bei `_tool_ctx=0`)
- anthropic.py:808–815 (Safe Max Berechnung)

**Empfehlung:**
1. Fallback-Wert bei `_tool_ctx=0`: statt 256 → mindestens `TOOL_MAX_TOKENS` (8192)
2. API-Timeout für Context-Lookup erhöhen (aktuell 5s?)
3. Bei `_tool_ctx=0`: explizit warnen im Log

---

### GAP #2: `num_ctx=0` → Ollama-Default (intern 2048/4096)

**Was passiert:**
- anthropic.py:921: `"options": {"num_ctx": _ollama_num_ctx, "num_predict": max_tokens}`
- `_ollama_num_ctx` wird von Profil `context_window` abgeleitet (meist 32768)
- `max_tokens` wird von Profil `tool_max_tokens` beschränkt (meist 8192)
- Also: `num_ctx=32768, num_predict=8192` — das ist korrekt!
- **ABER:** Wenn `context_window=0` und `_ollama_num_ctx` nicht gesetzt:
  - line 576: `_ollama_num_ctx = _profile_ctx` — aber wenn `_profile_ctx=0`, dann `_ollama_num_ctx=0`
  - line 577: `if JUDGE_NUM_CTX > 0` — wenn JUDGE_NUM_CTX=0, bleibt `_ollama_num_ctx=0`!
  - line 921: `num_ctx: 0, num_predict: 8192` — Ollama ignoriert `num_ctx: 0` und verwendet internen Default (oft 2048 oder 4096)
  - Wenn der interne Default kleiner als der Input ist → **done_reason=length** → Output auf 1–3 Zeichen

**Empfehlung:**
1. Hard-Fallback: `_ollama_num_ctx = max(_ollama_num_ctx, 32768)` falls 0
2. Log-Warnung wenn `_ollama_num_ctx=0`

---

### GAP #3: `CC_HISTORY_COMPRESS_THRESHOLD=2000` (History-Kompression)

**Was passiert:**
- anthropic.py:785–789: `_compress_history_responses()` mit threshold=2000, keep_turns=4
- Wenn frühere Nachrichten >2000 chars haben, werden sie komprimiert (Summary statt Full)
- Komprimierte Nachrichten enthalten nur "Schlüsselinformation", kein Full-Content
- Bei vielen Tool-Calls und langen Ausgaben wird die History stark reduziert
- Das Modell erhält keine vollständigen Tool-Results → falsche/knappe Antworten

**Empfehlung:**
1. `CC_HISTORY_COMPRESS_THRESHOLD` auf 5000–8000 erhöhen
2. `CC_HISTORY_COMPRESS_KEEP_TURNS` auf 6–8 erhöhen

---

## 8. Additional Variables Trace

| Variable | Wert | Quelle | Trace | GAP |
|---|---|---|---|---|
| `MAX_EXPERT_OUTPUT_CHARS` | `2400` | config.py:202 | expert.py:361–365 — Ollama-Expert-Pfad. CC-Pfad ignoriert. | Kein Problem | — |
| `EXPERT_CHARS_PER_TOKEN` | `3` | config.py:201 | expert.py:357–358 — Ollama-Expert-Pfad. | Kein Problem | — |
| `CC_TOOL_CHARS_PER_TOKEN` | `3` | anthropic.py:105 | anthropic.py:808 — CC-Input-Schätzung. Korrekt für CC-Style. | — | — |
| `_CHARS_PER_TOKEN` | `4` | state.py (imported) | anthropic.py:118 — `_trim_oai_to_budget`. 25% zu pessimistisch für CC. | **Mittel** | Auf 3 setzen für CC-Konsistenz. |

---

## 9. Non-CC Agent-Pfad (moe_handler)

| Variable | Wert | Quelle | Rolle | GAP |
|---|---|---|---|---|
| `expert_models_used` | `[]` | anthropic.py:1775 | State-Feld, wird gefüllt. | — | — |
| `prompt_tokens` / `completion_tokens` | `0` | anthropic.py:1775 | State-Feld, wird vom Graph aktualisiert. | — | — |
| `num_ctx` | `0` | anthropic.py:1791 | Übergeben an Graph. `0` = auto via expert.py. | — | — |
| `system_prompt` | Volltext | anthropic.py:1777 | CC_CONTEXT_INDEX_ENABLED kann es auf 2048 chars kürzen (line 1770). | **Mittel** — System-Prompt-Kürzung kann wichtige Kontext-Info entfernen. | |

---

## 10. Recommendations — Prioritized

| Priorität | GAP | Aktion | Risiko |
|---|---|---|---|
| **P0** | #1 `_tool_ctx=0` | Hard-Fallback auf 32768 bei 0; Log-Warnung; Timeout erhöhen | **KRITISCH** — Ursache für 1–3 Zeichen Truncation |
| **P0** | #2 `num_ctx=0` → Default | Hard-Fallback `_ollama_num_ctx = max(_ollama_num_ctx, 32768)` | **KRITISCH** — Gleiche Ursache, anderer Pfad |
| **P1** | #3 `CC_HISTORY_COMPRESS_THRESHOLD` | Von 2000 auf 5000–8000 | **HOCH** — Verhindert übermäßige History-Kompression |
| **P1** | `CC_HISTORY_COMPRESS_KEEP_TURNS` | Von 4 auf 6–8 | **HOCH** — Mehr Kontext erhalten |
| **P2** | `_CHARS_PER_TOKEN` für `_trim_oai_to_budget` | Von 4 auf 3 (CC-spezifisch) | **MITTEL** — 25% mehr History-Kapazität |
| **P2** | `reasoning_max_tokens` | Von 4096 auf 8192 | **MITTEL** — Vollständigere Tool-Calls |
| **P3** | `CC_SAFETY_BUFFER_TOKENS` | Von 500 auf 1000 | **NIEDRIG** — Mehr Margin |
| **P3** | System-Prompt-Kürzung | CC_CONTEXT_INDEX_ENABLED prüfen oder threshold erhöhen | **NIEDRIG** |

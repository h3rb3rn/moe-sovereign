# Session-Dokumentation moe-infra — 2026-07-05

Vollständige Dokumentation aller Findings, Änderungen, Auswirkungen und
Begründungen dieser Sitzung. Umfasst vier zusammenhängende Arbeitsstränge:

1. Feature: Experten-Templates als CC-Tool-Modell
2. Codebase-Analyse und Bugfixes (BUGFIX_PLAN_2026-07-05.md)
3. Kritischer Stream-Termination-Bug (tool_choice=required)
4. Architektur-Optimierung (UMSETZUNGSPLAN_ARCHITEKTUR_2026-07-05.md)

Referenzdokumente im Repo-Root:
- `BUGFIX_PLAN_2026-07-05.md` — detaillierter Umsetzungsplan Strang 2
- `UMSETZUNGSPLAN_ARCHITEKTUR_2026-07-05.md` — detaillierter Umsetzungsplan Strang 4
- `legacy_root_modules/README.md` — Hinweis zu archivierten Alt-Dateien

---

## 1. Feature: Experten-Templates als CC-Tool-Modell

### Finding
Im CC-Profil-Editor (Admin-UI und User-Portal) konnten nur native LLMs als
Tool-Modell für Claude-Code-Requests gewählt werden. Ein Experten-Template
mit einem dedizierten `tool_agent`-Experten konnte nicht als „virtuelles
Frontier-Modell" genutzt werden, obwohl die Infrastruktur (Templates,
Experten-Rollen) das nahelegt.

### Änderung
- Neues Wert-Format `tool_model = "template:<template_id>"` in CC-Profilen.
- `services/pipeline/cc_session.py`: Phase 3 löst `template:`-Werte auf
  einen `tool_agent`-Experten des referenzierten Templates auf (Modell, URL,
  Token, System-Prompt).
- **Phase 5.5 (Nachtrag):** Wenn `tool_model` im Profil komplett leer ist UND
  ein `expert_template_id` gesetzt ist, wird der `tool_agent` des zugewiesenen
  Templates automatisch übernommen — kein manueller `template:`-Eintrag mehr
  nötig.
- `/user/api/permitted-models` (beide `app.py`-Varianten) gibt zusätzlich
  `template:<id>`-Einträge für Templates mit `tool_agent`-Experten zurück.
- Admin-UI (`profiles.html`) und User-Portal (`user_portal.html`, beide
  Kopien) zeigen zwei Dropdown-Gruppen („Expert Templates (MoE)" /
  „Native LLMs") und einen Hinweistext, wann die Auto-Ableitung greift.
- Neues Template `moe-auto-n04-256k-cc` (ID `user:660629d8bd0c468698ab15b92df2866f`,
  User horndev) mit CC-Tool-Header-optimiertem System-Prompt für den
  `tool_agent`-Experten (statt des ursprünglichen MCP-Benchmark-Prompts).

### Auswirkung
Ein CC-Profil kann jetzt ein komplettes Experten-Ensemble als Tool-Backend
nutzen, statt an ein einzelnes statisches Modell gebunden zu sein. Templates
mit starkem `tool_agent` (z.B. `qwen3.6:35b`) lassen sich für mehrere Profile
wiederverwenden, ohne Modell/Endpoint/Token redundant in jedem Profil zu
pflegen.

### Begründung
Reduziert Konfigurationsduplikation und macht die Stärke des Template-Systems
(zentrale Modellzuordnung, Prompt-Tuning) auch für den CC-Tool-Pfad nutzbar,
statt nur für die MoE-Orchestrated-Pipeline.

---

## 2. Codebase-Analyse: Logikfehler, Übergabelücken, Altlasten

Vollständiger Plan mit exakten Diffs: `BUGFIX_PLAN_2026-07-05.md`
(10 Aufgaben, alle umgesetzt).

### Finding 1 — Token-Bug bei User-Connections (`services/routing.py`)
`_resolve_user_experts()` löste bei Template-Experten auf privaten
Connections zwar die URL aus `user_connections_json` auf, verwendete für den
Token aber weiterhin `TOKEN_MAP` (kennt private Connections nicht → Fallback
`"ollama"`). **Auswirkung:** Jeder Template-Experte auf einem privaten
API-Key-geschützten Endpoint schlug mit 401 fehl. **Fix:** Token kommt jetzt
konsistent aus `user_conns[ep]["api_key"]`, wenn die URL von dort stammt —
in beiden Formaten (aktuell + Legacy) und mit einmaligem statt
pro-Modell-wiederholtem JSON-Parse.

### Finding 2 — System-Prompt-Leck zwischen Handlern
Der `tool_agent`-System-Prompt eines Template-Tool-Modells wurde in
`session.system_prefix` eingebettet — einem Feld, das auch vom
Reasoning-Handler und der MoE-Pipeline gelesen wird. **Auswirkung:**
Function-Calling-Anweisungen („respond only with the tool call(s)…")
verschmutzten Nicht-Tool-Turns. **Fix:** Neues, getrenntes Feld
`session.tool_system_prefix`, das ausschließlich `_anthropic_tool_handler`
konsumiert.

### Finding 3 — Blockierender Sync-DB-Call im Async-Event-Loop
`services/templates.py::_read_expert_templates()` öffnete bei
abgelaufenem 30s-Cache eine **synchrone** `psycopg.connect()` direkt im
Request-Pfad — blockiert den gesamten Event-Loop bis zu 3 Sekunden.
**Fix:** Stale-while-revalidate — der Cache wird sofort ausgeliefert, die
Aktualisierung läuft in einem Daemon-Thread (`_refresh_expert_templates_cache`).
Nur der Kaltstart (leerer Cache) lädt noch synchron.

### Finding 4 — Stille Fehlerverschlucker
Mehrere `except Exception: return None/empty/1.0` ohne Logging in
`routing.py` und `admin_ui/database.py`. **Auswirkung:** Ein fehlerhaftes
Template-JSON ließ die Experten-Auflösung lautlos auf globale Defaults
zurückfallen — kein Log-Eintrag, keine Diagnose möglich. **Fix:**
`logger.exception(...)` vor jedem betroffenen Fallback.

### Finding 5 — Redis-Ausfall-Degradation unsichtbar
Bei Valkey-Ausfall lieferte `_db_fallback_key_lookup()` einen
Minimal-Kontext (`permissions_json: "{}"`) — User verlor stillschweigend CC-
Profile, Templates, Budget-Checks. **Fix:** `logger.warning(...)` macht die
Degradation sichtbar.

### Finding 6 — Deaktivierte eigene Templates im Dropdown
`/user/api/permitted-models` filterte eigene (nicht Admin-verwaltete)
Templates nicht nach `is_active`. **Fix:** Filter ergänzt.

### Finding 7 — Zu breites Fehler-Matching
`_is_endpoint_error()` matchte bare Substrings `"blob"` /
`"no such file or directory"` — ein zufällig passendes Tool-Ergebnis in
einer Exception hätte fälschlich die Endpoint-Fallback-Kette ausgelöst.
**Fix:** Zusätzliche Kontext-Bedingung (`model`/`gguf`/`ollama` im selben Text).

### Finding 8 — Tote, divergierte Root-Duplikate
`app.py`, `auth.py`, `chat.py`, `database.py`, `user_portal.html` im
Repo-Root waren veraltete Kopien der aktiven Module unter `admin_ui/` bzw.
`services/`, wurden von **keinem** laufenden Dienst importiert, aber weiter
gepflegt (Risiko für künftige Fehlarbeit). Root `database.py::sync_user_to_redis`
fehlten sogar die Felder `local_only_routing`/`native_num_ctx`. **Fix:** Nach
vierfacher Grep-Verifikation (keine aktiven Importe) nach
`legacy_root_modules/` verschoben statt gelöscht (kein Git-Repo, kein Undo).

### Finding 9 — Fehlende Compose-Mounts
`admin_ui/database.py` war nicht in den `moe-admin`-Container gemountet —
Änderungen daran wären ohne Image-Rebuild nicht live gegangen. **Fix:**
Mount ergänzt.

### Nicht umgesetzt (mit Begründung)
- Rollen-Default-Fix (`role="always"` als impliziter Default) — DB-Prüfung
  ergab 0 betroffene Templates, aber als Verhaltensänderung bewusst nicht
  ohne explizite Freigabe umgesetzt.

### Auswirkung dieses Strangs
Robustere Fehlerbehandlung (sichtbare Logs statt stiller Fallbacks),
korrekte Authentifizierung für private Experten-Endpoints, kein
Event-Loop-Blocking mehr durch Template-Reads, sauberere Codebase (keine
gepflegten Karteileichen mehr).

---

## 3. Kritischer Bug: Stream-Termination bei `tool_choice=required`

### Finding
Nutzer-Incident: `claude-dev` (RTX-Profil, `tool_model: gemma4:12b`,
`tool_choice: required`) kündigte eine Aktion an, führte aber keinen
Tool-Call aus — Claude Code beendete den Turn sauber mit `stop_reason:
end_turn`, obwohl der User eine Tool-Ausführung erwartete. Parallel lief ein
5-Minuten-MoE-Request ins Leere (Client hatte bereits disconnected).

**Ursache:** Der Ollama-native Pfad (`/api/chat`) kennt **kein**
`tool_choice`-Parameter — `required` wurde beim Payload-Aufbau
stillschweigend verworfen. `gemma4:12b` (12B, 30 Tool-Schemas, 33k-Token-
Kontext) generierte nur Prosa ohne Tool-Call. Der Stream-Abschluss quittierte
das mit dem falschen Return-Wert `end_turn` statt eines Retry- oder
Fehler-Signals.

### Änderung (`services/pipeline/anthropic.py`)
1. Neues `_require_tools`-Flag: aktiv bei Tools + `tool_choice=required` +
   kein Synthesis-Turn (Turns mit `tool_result` dürfen weiterhin final
   antworten).
2. Im required-Modus werden Text-Deltas gepuffert statt live gestreamt —
   die potenziell regelwidrige Antwort geht nicht sofort an den Client.
3. **Enforcement-Retry:** Bleibt der Tool-Call aus, wird einmalig der
   `tool_agent` des zugewiesenen Experten-Templates aufgerufen (mit
   SSE-Keep-Alive-Pings während der Wartezeit). Liefert dieser Tool-Calls,
   ersetzt sein Ergebnis die Ankündigung; der Stream endet korrekt mit
   `stop_reason: tool_use`.
4. Nebenfund: Ollama-Tool-Calls über mehrere Stream-Chunks wurden
   überschrieben statt akkumuliert — nur der letzte Chunk überlebte. Fix:
   Akkumulation mit Duplikat-Schutz.

### Auswirkung
`tool_choice=required` wird jetzt auch für Modelle durchgesetzt, die es
nativ nicht unterstützen (jedes Ollama-Modell). Fehlversuche kosten die
Generierungszeit des schwachen Modells, bevor der Retry greift — siehe
Empfehlung unten (WP0).

### Begründung
Ohne diesen Fix ist `tool_choice=required` im Ollama-Pfad reine Fiktion —
das Feld wird geschluckt, ohne dass Nutzer oder Log-System das bemerken.
Der Retry-Mechanismus nutzt die bereits vorhandene Template-Infrastruktur
(Punkt 1), statt eine neue Fallback-Kette einzuführen.

---

## 4. Architektur-Bewertung und Optimierungsplan

Vollständiger Plan mit exakten Diffs: `UMSETZUNGSPLAN_ARCHITEKTUR_2026-07-05.md`
(13 Arbeitspakete, 8 Phasen). Ausgangsbewertung: Das System hat tragfähige
Abstraktionen (Experten-Templates, Tier-2-4-Gedächtnis, Souveränitäts-Basis),
aber fehlende Rückkopplungsschleifen — es sammelt fast alle Daten, die es
bräuchte, um sich selbst zu optimieren, wertet sie aber nicht aus.

### Phase 0 — Quick Wins

**WP0 — RTX-Profil auf Template-Auto-Ableitung umgestellt.**
*Finding:* Profil `ucp-7b76127c51704cd6977eeb7b06369ffc` zeigte fest auf
`gemma4:12b` statt auf den stärkeren Template-`tool_agent`. *Änderung:*
`tool_model`/`tool_endpoint` in der DB geleert, Redis invalidiert.
*Auswirkung:* Tool-Turns laufen jetzt direkt über `qwen3.6:35b` statt über
den Enforcement-Retry-Umweg. *Begründung:* Der Retry aus Abschnitt 3 kostet
die volle Fehlversuch-Zeit des schwachen Modells — das lohnt sich nur als
Sicherheitsnetz, nicht als Regelbetrieb.

**WP2 — Transparenz-Header `X-MoE-Backend-Model`/`X-MoE-Node`/`X-MoE-Required-Retry`.**
*Finding:* Kein Client-seitiges Signal, welches Backend-Modell tatsächlich
geantwortet hat oder ob ein Fallback griff — stille Degradation war
unauditierbar. *Änderung:* Neue Helper-Funktion `_moe_response_headers()`,
in 10 von 13 Response-Stellen des Tool- und Reasoning-Handlers integriert
(3 bewusst unverändert: früher Empty-Guard vor Modell-Auflösung im
Reasoning-Handler, 2 Stellen im MoE-Handler ohne singuläres Backend-Modell).
*Auswirkung:* Jede Tool-/Reasoning-Antwort trägt jetzt sichtbar ihr echtes
Backend. *Begründung:* Souveränität erfordert Beweisbarkeit — „lief lokal"
muss überprüfbar sein, nicht nur konfiguriert.

### Phase 1 — Token-Triage

**WP1 — Fast-Path für CC-Utility- und Trivial-Requests (`services/cc_fastpath.py`, neu).**
*Finding:* Jeder Request ohne Tools durchlief die volle MoE-Pipeline;
CC-interne Utility-Calls (Topic-Detection, Titel-Generierung) wurden vom
Complexity-Estimator fälschlich als „complex" eingestuft — ein
5-Zeilen-Request band 5 Minuten Pipeline-Zeit. *Änderung:* Neues Modul
erkennt Utility-Muster (Regex) und trivial kurze Prompts; Dispatch-Hook in
`anthropic.py` leitet diese direkt an den Tool-Handler statt an die
Pipeline; Complexity-Estimator-Guard stuft Utility-Texte immer als
„trivial" ein (auch ohne aktives Flag). *Auswirkung (Flag `CC_FASTPATH=1`,
**jetzt aktiv**):* Utility-Calls und kurze Prompts (≤600 Zeichen, ≤4
Messages, keine Tools) antworten in Sekunden. *Begründung:* Der dominante
Kostentreiber war nicht die Pipeline-Qualität, sondern ihre unnötige
Anwendung auf Requests, die gar keine Synthese brauchen.

### Phase 2 — Feedback-Fundament

**WP3 — Online-Qualitätsmessung (`services/quality_probe.py`, neu).**
*Finding:* Niemand maß, ob die MoE-Pipeline (3-5× Token-Kosten) tatsächlich
besser antwortet als der stärkste Einzelexperte. *Änderung:* Stichprobenartig
(Default 5%) wird nach einer Orchestrated-Antwort derselbe Prompt an den
stärksten Einzelexperten geschickt; ein blinder Judge-Call (randomisierte
A/B-Position gegen Positionsbias) entscheidet; Ergebnis landet in
`pipeline_quality_log` (lazy erstellte Tabelle). Hooks in beiden Pfaden
(Streaming + Non-Streaming) des `_anthropic_moe_handler` integriert.
*Auswirkung (Flag `MOE_QUALITY_PROBE=1`, **jetzt aktiv**, Rate 5%):* Für den
Nutzer unsichtbar (fire-and-forget); sammelt binnen einer Woche genug
Datenpunkte, um die Kernfrage „lohnt sich die Pipeline?" mit Zahlen statt
Vermutung zu beantworten. *Begründung:* Ohne diese Messung ist jedes
weitere Template-Tuning Blindflug.

**WP4 — Judge-Gate (`services/judge_gate.py`, neu).**
*Finding:* Der Merger-Judge bewertete häufig einen einzelnen Experten oder
mehrere nahezu identische Antworten — Token-Kosten ohne Diskriminationskraft
(teilweise Selbst-Judging). *Änderung:* Neue Gate-Funktion
`should_skip_judge()`: überspringt den Judge bei genau einem
Expertenergebnis oder bei Konsens (SequenceMatcher-Ratio ≥ 0.85 zwischen
allen Antworten). Integriert in `graph/synthesis.py::merger_node`,
ausschließlich wenn keine Zusatzkontexte (Web/MCP/Math/Graph/Ensemble)
gemergt werden müssen — bewusst konservativ, um Informationsverlust
auszuschließen. *Auswirkung (Flag `MOE_JUDGE_GATE=1`, **noch nicht aktiv**):*
Bei Konsens-Antworten entfällt der komplette Judge-Call. *Begründung:*
Aktivierung erst nach Auswertung der Quality-Probe-Daten geplant — die
Gate-Schwelle (0.85) sollte mit echten Konsens-Mustern kalibriert werden,
bevor sie scharf geschaltet wird.

### Phase 3 — Routing-Intelligenz

**WP5 — Tool-Turn-Router (`services/tool_turn_router.py`, neu).**
*Finding:* ~95% des CC-Traffics sind Tool-Turns — ein einziges statisches
Tool-Modell beantwortet fast alles, das Experten-Ensemble liegt brach.
*Änderung:* Turns mit `tool_result`-Blöcken (Synthese-Phase, kein neuer
Tool-Call zwingend) können an den `code`- oder `general`-Experten des
Templates umgeroutet werden; frische Instruktions-Turns bleiben beim
`tool_agent` (Zuverlässigkeit hat dort Priorität). *Auswirkung (Flag
`CC_TOOL_EXPERT_ROUTING=1`, **noch nicht aktiv**):* Synthese nach
Tool-Ergebnissen könnte von einem stärkeren Modell übernommen werden.
*Begründung:* Bewusst als Experiment zurückgehalten — Effekt auf
Antwortqualität sollte beobachtet werden, bevor es Standard wird.

**WP10 — GPU-Last-bewusstes Routing (`services/node_load.py`, neu).**
*Finding:* Expertenaufrufe queuen sich auf überlasteten Nodes, obwohl
Alternativ-Nodes frei sind; Auslastungsdaten wurden gepollt, aber nicht für
Routing genutzt. *Änderung:* In-Process-Zähler pro Node; in
`graph/expert.py` werden Kandidaten **gleichen** Scores (echte Ties, keine
Score-Verfälschung) nach Node-Last sortiert. *Teilumsetzung:* Das
Ummanteln der drei LLM-Aufrufpfade (Ollama-nativ/Anthropic-nativ/OpenAI) mit
einem Live-Zähler wurde **nicht** umgesetzt — hätte eine 80+-zeilige
Neueinrückung in einer produktiven Try/Except-Struktur erfordert, zu
riskant für den Nutzen. *Auswirkung (Flag `MOE_LOAD_AWARE_ROUTING=1`, **noch
nicht aktiv**):* Nur bei echten Score-Gleichständen wirksam — geringer, aber
sicherer Effekt. *Begründung:* Sicherheit vor Vollständigkeit; der riskante
Teil (Live-Zähler) kann als eigenständige Folgearbeit nachgezogen werden.

### Phase 4 — Wissens-Hygiene

**WP6 — Retrieval-Attribution + Graph-Decay (`services/retrieval_attribution.py`,
`scripts/graph_decay.py`, beide neu).**
*Finding:* Der Knowledge-Graph wächst ungeprüft; niemand misst, ob
abgerufene Chunks tatsächlich in der Antwort verwendet wurden.
*Änderung:* Attribution-Modul (Token-Overlap-Heuristik) und ein
`DRY_RUN`-sicheres Decay-Skript sind fertig implementiert und unit-getestet.
**Der Integrations-Hook wurde nicht verdrahtet** — Code-Untersuchung
(`graph/tool_nodes.py::graph_rag_node`) zeigte, dass der Retrieval-Kontext
bereits vor der Rückgabe zu einem flachen String ohne Chunk-IDs verschmolzen
wird (mehrere Quellen: Episode-Hints, Neo4j-Query, CAG-Statik). Eine echte
Attribution würde die Retrieval-Funktionen selbst umbauen müssen — außerhalb
des sicheren Rahmens dieser Aufgabe. *Auswirkung:* Aktuell keine — die
Module sind einsatzbereit, sobald die Retrieval-Funktionen um Chunk-IDs
erweitert werden. *Begründung:* Lieber ein sauber dokumentiertes „noch
nicht" als eine unsichere Refaktorierung des Kern-Retrievals unter Zeitdruck.

### Phase 5 — Selbstverbesserung

**WP7 — Distillations-Export (`scripts/export_distillation_dataset.py`, neu).**
*Finding:* Die besten Pipeline-Antworten (teuerster Pfad: Planner + N
Experten + Judge) verpuffen, obwohl sie ideales LoRA-Trainingsmaterial für
die eigenen SLMs wären. *Untersuchung während der Umsetzung:* Die
angenommenen Log-Feldnamen (`input`/`final_response`) stimmten nicht mit der
echten Struktur überein — reale Felder sind `messages` (Liste) und
`response` (String), geschrieben von `services/conversation_log.py`;
Query steckt in der letzten `role=="user"`-Nachricht. Rotationsschema
(`{user_id}.jsonl`, `{user_id}.jsonl-YYYYMMDD`, `.gz`-komprimiert) wurde
ebenfalls korrigiert. *Live-Test:* **411 valide Trainingsbeispiele aus 516
echten Log-Zeilen über 33 Dateien** erfolgreich exportiert (inkl.
`.gz`-Dekompression). *Auswirkung:* Sofort nutzbar für ein erstes
LoRA-Fine-Tuning-Experiment auf einem der lokalen SLMs — reines Ops-Tooling,
kein Live-Systemeingriff. *Begründung:* Token-Overhead der Pipeline wird
so zu einem permanenten Fähigkeitsgewinn statt einmaligem Verbrauch.

**WP8 — Modell-Lifecycle (`scripts/model_lifecycle.py`, neu).**
*Finding:* Neue Modelle im Ollama-Bestand werden nie systematisch gegen
amtierende Experten benchmarkt. *Änderung:* Skript pollt `/api/tags` aller
Ollama-Nodes, benchmarkt unbekannte Modelle gegen 4 Golden-Prompts
(Code/General/Reasoning/Tool), schreibt Ergebnisse in `model_registry`
(lazy erstellt) und gibt bei Score ≥ 0.75 eine Promotion-**Empfehlung**
aus — bewusst **kein** automatischer Template-Umbau. *Live-Test:* Lief
real gegen N04-RTX (42 Modelle im Bestand), **10 erfolgreich benchmarkt**
(u.a. `granite4.1:30b`, `ornith:35b`, `qwen3.6:27b` mit Score 1.0;
Embedding-Modelle `all-minilm`/`bge-m3` korrekt mit Score 0.0, da sie keine
Chat-Antworten liefern können — kein Fehler, erwartetes Verhalten), dann für
den finalen Smoke-Test kontrolliert beendet (GPU-Konkurrenz mit dem
Verifikations-Request). *Auswirkung:* Als wöchentlicher Cron-Job
vorgesehen; Betreiber entscheidet über tatsächliche Modell-Beförderungen.
*Begründung:* Auto-Promotion ohne menschliche Prüfung wäre ein
Sicherheitsrisiko (Qualitätsregression durch ungeprüfte Modelle) — die
Empfehlung spart Discovery-Aufwand, ohne Kontrolle abzugeben.

### Phase 6 — Souveränität

**WP9 — Egress-Guard (`services/sovereignty.py`, neu; `docker/opa/moe_routing.rego`, vorbereitet).**
*Finding:* `local_only`-Routing wurde nur per Konfiguration vermieden, nicht
erzwungen — Souveränität war „konfiguriert", nicht „beweisbar". *Änderung:*
`assert_egress_allowed()` prüft bei jedem Request mit
`local_only_routing=1`, ob die aufgelöste Tool-URL in einem privaten
IP-Bereich liegt (RFC1918 + Loopback + ULA, plus Allowlist via
`MOE_LOCAL_ALLOW_HOSTS`); bei Verstoß wird der Request mit HTTP 403
**vor** jedem Egress abgebrochen. Integriert direkt nach der
CC-Session-Auflösung in `anthropic_messages()`. OPA-Rego-Policy als
vorbereiteter Zweitschritt abgelegt (noch nicht verdrahtet). *Live-Test:*
Unit-verifiziert — lokale IP erlaubt, `api.openai.com` korrekt blockiert,
Requests ohne `local_only`-Flag unbeeinflusst. *Auswirkung:* Sofort
wirksamer Code, aber nur für Keys mit gesetztem `local_only_routing=1`
relevant — für alle anderen Requests keine Verhaltensänderung.
*Begründung:* Eine Zusage „kein Cloud-Pfad" muss technisch erzwungen
werden, sonst ist sie nur eine Konfigurationsabsicht.

### Phase 7 — Robustheit

**WP11 — Schema-Verträge (`services/pipeline/contracts.py`, neu).**
*Finding:* Planner→Experte→Judge kommunizieren über Prosa mit fragilen
Parse-Fallback-Ketten. *Änderung:* Tolerante Parser `parse_plan()` /
`parse_verdict()` mit klar definiertem Rückgabetyp. *Untersuchung während
der Umsetzung:* Die reale Planner-Parse-Stelle (`graph/planner.py`,
Zeile ~655) hat Nebenlogik (`metadata_filters`-Extraktion,
`output_skill`-Vorschläge, Advice-Rule-Enforcement), die kein 1:1-Mapping
zum neuen Contract-Schema erlaubt — Adoption würde die gesamte Nebenlogik
in `contracts.py` nachbauen müssen, Risiko für Produktionsverhalten zu
hoch. **Modul bewusst nicht in den Planner integriert.** *Auswirkung:*
Aktuell keine — Modul ist fertig und unit-getestet (im Container gegen
`fastapi`-Abhängigkeit verifiziert), für künftige neue Call-Sites sofort
nutzbar. *Begründung:* Ein sauberes Vertragsschema für neue Integrationen
ist wertvoll; ein riskanter Nachbau bestehender Spezialfall-Logik unter
Zeitdruck ist es nicht.

### Phase 8 — Infrastruktur

**WP12 — vLLM-Prefix-Caching (Runbook, kein Code).**
*Finding:* Experten auf demselben Node ingestieren identischen 30k+-Token-
Kontext bei jedem Aufruf neu — beobachtet im Incident aus Abschnitt 3
(33k Prompt-Tokens pro Expertenaufruf). *Nicht umgesetzt:* Der Plan
verlangt explizit Betreiber-Freigabe vor Beginn (GPU-VRAM-Budget,
HuggingFace-Weights-Verfügbarkeit für `qwen3.6:35b`). **Diese Freigabe
liegt nicht vor — WP12 bleibt ein dokumentiertes Runbook, keine Umsetzung.**

---

## Aktivierte Flags (Stand nach dieser Sitzung)

In `.env` ergänzt, Container per `docker compose up -d --no-build langgraph-app`
neu erstellt (Hinweis: `docker compose restart` lädt `env_file`-Änderungen
**nicht** neu — nur `up -d` mit Recreate übernimmt sie; im Container per
`docker exec ... env` verifiziert):

| Flag | Wert | Live-verifiziert |
|---|---|---|
| `CC_FASTPATH` | `1` | ✅ Log zeigt `cc_dispatch: fast-path (trivial) — bypassing MoE pipeline` bei kurzem Test-Prompt |
| `MOE_QUALITY_PROBE` | `1` | ✅ Env-Variable im Container bestätigt; Tabelle wird beim ersten Treffer der 5%-Stichprobe lazy erstellt |

Alle übrigen Flags (`MOE_JUDGE_GATE`, `CC_TOOL_EXPERT_ROUTING`,
`MOE_LOAD_AWARE_ROUTING`, `MOE_RETRIEVAL_ATTRIBUTION`, `MOE_STRICT_CONTRACTS`)
sind in der `.env` als auskommentierte Referenz hinterlegt — **nicht aktiv**,
wie empfohlen (Rest je nach Datenlage nach der Quality-Probe-Woche).

## Rollout-Historie dieser Sitzung

| Zeitpunkt | Aktion | Ergebnis |
|---|---|---|
| Nach Feature-Umsetzung (Abschnitt 1) | `docker compose up -d --no-build moe-admin` + `restart langgraph-app` | Beide Container fehlerfrei, Templates live |
| Nach Bugfix-Plan (Abschnitt 2) | `restart langgraph-app`, `up -d --no-build moe-admin` (neuer DB-Mount) | 0 Fehler, Smoke-Test 200 OK |
| Nach Stream-Fix (Abschnitt 3) | `restart langgraph-app` | 0 Fehler, `X-MoE-*`-Header im Test bestätigt |
| Nach Architektur-Phase 0+1 | `restart langgraph-app` | 0 Fehler, Fast-Path- und Header-Test bestanden |
| Nach Architektur-Phase 2+3 | `restart langgraph-app` | 0 Fehler, Non-Streaming-Test 200 OK |
| Nach Architektur-Phase 4-7 | `restart langgraph-app` | 0 Fehler; finaler Smoke-Test zunächst durch GPU-Konkurrenz mit dem eigenen WP8-Testlauf verzögert, nach dessen kontrolliertem Abbruch erfolgreich |
| Flag-Aktivierung (dieses Dokument) | `up -d --no-build langgraph-app` (Recreate, da `restart` `env_file` nicht neu lädt) | 0 Fehler, Fast-Path live bestätigt |

## Offene Punkte / Empfehlungen für die kommende Woche

1. **Nach ~1 Woche:** `pipeline_quality_log` auswerten
   (`SELECT winner, count(*) FROM pipeline_quality_log GROUP BY winner`) —
   entscheidet, ob `MOE_JUDGE_GATE` und weitere Flags aktiviert werden.
2. **WP6-Nachtrag (optional):** Retrieval-Funktionen in `graph/tool_nodes.py`
   um durchgereichte Chunk-IDs erweitern, dann `record_attribution()`-Hook
   ergänzen.
3. **WP10-Nachtrag (optional):** In-Flight-Zähler-Wrapping der drei
   LLM-Aufrufpfade in `graph/expert.py`, falls Node-Last-Probleme trotz
   Tie-Break-Sortierung weiter auftreten.
4. **WP12:** Betreiber-Entscheidung zu vLLM-Testinstanz ausstehend.
5. **WP8 als Cron einrichten:** `23 4 * * 1 … scripts/model_lifecycle.py`
   (wöchentlich, wie im Skript-Header dokumentiert) — noch nicht in
   Host-Crontab eingetragen, da das dem Betreiber-System obliegt.

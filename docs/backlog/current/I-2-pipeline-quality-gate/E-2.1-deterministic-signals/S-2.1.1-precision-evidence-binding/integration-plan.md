# Precision Evidence Binding — Integrationsplan

Owner: Platform Engineering
Version: 1.0
Last verified: 2026-08-01
Status: Completed — R0 through R5 validated locally

Parent Story: [S-2.1.1 Verifiable Precision Execution](story.md)

## Zielinvariante

Eine als verpflichtend erkannte Präzisionsaussage darf nur erfolgreich an den
Caller gelangen, wenn die folgende Beweiskette vollständig und konsistent ist:

```text
Nutzerintent
  -> unveränderlicher Contract-Snapshot
  -> normalisierte Argumente
  -> aktiver MCP-Toolaufruf
  -> schema-valides typisiertes Ergebnis
  -> evidenzgebundene finale Aussage
  -> finales Quality Gate
  -> idempotenter Commit in Cache/Memory/Learning
```

Fehlt ein Glied oder ändert sich der Vertrag während des Requests, endet die
Anfrage mit einem strukturierten, nicht als Erfolg cachebaren Fehler. Ein LLM
darf diese Beweiskette weder ersetzen noch Argumente oder Ergebniswerte
stillschweigend korrigieren.

## Lokal bestätigte Integrations-GAPs

| ID | Ist-Pfad | Risiko | Zielzustand |
|---|---|---|---|
| PEB-01 | L0/L1-Cache wird vor dem Planner geprüft; Cache-Hits gehen direkt zum Merger | Alter oder falscher Cache umgeht TASK-41 vollständig | Precision-Preflight vor jedem Antwortcache; Legacy-Cache für Pflichtintents sperren |
| PEB-02 | Judge kann nach Toolfehler neue Argumente erzeugen | Guard-konformer Plan wird semantisch nachträglich verändert | Für Pflichtverträge keine LLM-Argumentänderung; sonst vollständige Revalidierung gegen Intent und Schema |
| PEB-03 | Vor Invoke werden im Wesentlichen nur Required-Felder geprüft | Falsche Typen, Enums, Grenzen oder Zusatzfelder passieren | Vollständige JSON-Schema-Prüfung mit fail-closed Fehlercodes |
| PEB-04 | MCP-Ergebnis ist heterogener Freitext; Evidence/Working Memory kürzen Inhalte | Keine stabile, maschinenprüfbare Provenienz | Versionierter Ergebnisumschlag, typisierte Fakten und kryptografische Hashes |
| PEB-05 | Merger und Critic formulieren Toolwerte frei neu | Korrekte Evidenz kann in der Antwort verändert werden | Deterministischer Renderer für reine Anfragen; Fact-Slots plus Post-Critic-Binding für gemischte Antworten |
| PEB-06 | Cache-, Chroma-, Kafka-, Episode- und Learning-Schreibvorgänge beginnen vor finalem Gate | Blockierte oder fehlerhafte Antwort vergiftet wiederverwendbaren Zustand | Semantische Persistenz ausschließlich in einem Post-Quality-Commit-Knoten |
| PEB-07 | Planner-Cache-Fingerprint enthält nicht das vollständige Schema | Schemaänderung kann alten Plan weiterverwenden | Kanonischer Contract-/Kataloghash in Plan-, Evidence- und Cache-Keys |
| PEB-08 | Quality Gate prüft Taskabschluss, aber keine Aussage-Evidence-Bindung | Vollständiger Task kann trotzdem mit falschem Wert antworten | Endgültiger Binding-Check nach letzter LLM-Mutation und vor API-Erfolg |
| PEB-09 | Es fehlen Pfadmetriken für Cache-Bypass, Binding und Contract-Drift | Fehlrouting bleibt nur durch manuelle Forensik sichtbar | Niedrig-kardinale Metriken und korrelierbare Audit-Ereignisse |

## Zielarchitektur

```text
guard
  -> precision_preflight
      |-- vollständig reine Precision-Anfrage
      |     -> task ledger -> MCP -> deterministic_renderer
      |     -> precision_bind -> quality_gate
      |-- gemischte Precision-/Expert-Anfrage
      |     -> planner -> fan-out -> merger -> critic
      |     -> precision_bind -> quality_gate
      `-- keine Pflicht-Precision
            -> bestehender L0/L1-Cache oder normaler Planner-Pfad

quality_gate
  |-- pass -> response_commit -> API success
  `-- block/pending -> API typed failure; kein semantischer Commit
```

Audit, Task-Ledger und betriebliche Fehlerereignisse bleiben während der
Ausführung schreibbar. Nur wiederverwendbare Antwort-, Wissens-, Episode- und
Learning-Artefakte werden hinter das finale Gate verschoben.

## Zustands- und Vertragsmodell

Der Pipeline-State wird mindestens um folgende explizite Felder erweitert:

- `required_precision_intents`: erkannte Contract-IDs und normalisierte
  Sollargumente;
- `precision_contract_snapshot`: immutable Vertragsmetadaten aus dem aktiven
  Katalog;
- `precision_contract_hash`: kanonischer Hash über vollständige Ein-/Ausgabe-
  schemas, Version und Policy;
- `precision_binding_status` und `precision_binding_errors`;
- `response_commit_status` und idempotenter Commit-Key.

Der MCP-Katalog liefert pro deterministischem Vertrag mindestens:

- Contract-ID und semantische Version;
- vollständiges Input- und Output-JSON-Schema;
- Determinismusklasse (`input_only`, `clock_bound`, `source_versioned`);
- Quellenpolicy mit Version/`as_of`, falls Außenweltzustand beteiligt ist;
- Argumentnormalisierung, Größen-/Laufzeitgrenzen, Cache- und Retry-Policy;
- kanonischen Schema-/Contract-Hash.

`/invoke` bleibt für bestehende Clients kompatibel, ergänzt aber einen
maschinenlesbaren `structured_result` mit Status, normalisiertem Input,
typisierten Fakten, Contract-Version, Source-Metadaten, Warnungen und
Ergebnis-Hash. Der Orchestrator speichert diese Daten vollständig innerhalb
definierter Größenlimits; UI-/Prompt-Darstellungen dürfen gekürzt werden, die
prüfbare Evidenz nicht.

## Release- und Abhängigkeitsfolge

| Release | Task | Inhalt | Aktivierungsgate |
|---|---|---|---|
| R0 | TASK-42 | Preflight vor Cache, Legacy-Cache-Sperre, Intent→Task→Evidence-Abgleich, Contract-Hash im Planner-Cache | Bestehende drei Allowlist-Intents blockieren jeden Bypass in Unit und Container |
| R1 | TASK-43 | Versionierter MCP-Katalog, volle Schemas, strukturierter Invoke-Umschlag, typisierte Evidence | Bestehende drei Verträge liefern alte und neue Antwortform kompatibel; Negativschemas fail closed |
| R2 | TASK-44 | Deterministische Direktantwort und Post-Critic-Fact-Binding | Reiner Pfad erzeugt null Modellaufrufe; Manipulationstests verändern keinen Fakt |
| R3 | TASK-45 | Post-Quality-Commit und idempotente Persistenz | Blockierte/abgebrochene Antworten hinterlassen keinen wiederverwendbaren Cache-/Learning-Eintrag |
| R4a | TASK-46 | Zeit, Datum, Zeitzone, DST | DST-Fold/Gap, IANA-Zone und expliziter Zeitbezug property-/live-getestet |
| R4b | TASK-47 | Decimal-Finanzmathematik | Keine Binary-Floats; Rundung, Scale und Währung sind explizit |
| R4c | TASK-48 | Exakte Wahrscheinlichkeit | Rationale Ergebnisse und Grenzen sind reproduzierbar |
| R4d | TASK-49 | JSON/YAML/XML/CSV-Validierung | Kein Netzwerk-Ref, Entity-Expansion oder ungebundener Parserpfad |
| R5 | TASK-50 | Shadow/Enforce, Telemetrie, Benchmark und Live-Rollout | Gesamte Abnahmematrix grün; frühere Images und Flags bilden einen geprüften Rollback |

TASK-44 und TASK-45 werden nicht parallel bearbeitet, weil beide Graph-
Topologie und `graph/synthesis.py` verändern. Neue Tools beginnen erst nach
R1; ihre Enforce-Aktivierung beginnt erst nach R2/R3.

## Rollout-Steuerung

Die Übergangssteuerung wird zentral konfiguriert und nicht über verstreute
Ad-hoc-Abfragen implementiert:

- `PRECISION_CONTRACT_MODE=shadow|enforce`: neue Verträge starten im Shadow-
  Modus; die drei TASK-41-Verträge können nach R0 direkt enforce bleiben;
- `PRECISION_CACHE_POLICY=bypass|typed`: zunächst Bypass, typisierter Cache
  erst nach R3;
- `PRECISION_DIRECT_RESPONSE_ENABLED`: zunächst aus, nach R2-Proof an;
- `MCP_STRUCTURED_RESULT_REQUIRED`: pro migriertem Contract aktivierbar.

Flags sind Migrationshilfen. Nach stabiler Abnahme werden obsolete Legacy-
Zweige und Flags explizit entfernt, damit keine dauerhafte zweite Semantik
entsteht.

## Test- und Störfallmatrix

1. **Intent/Preflight:** positive und negative deutsche/englische Beispiele,
   nummerierte Mixed-Prompts, unvollständige oder mehrdeutige Parameter,
   Code-/Dokumentationsfragen sowie Prompt-Injection gegen Toolnamen.
2. **Cache:** absichtlich falscher alter L0-/L1-Eintrag, veraltete Contract-
   Version, identische Frage mit anderer Locale/Zeitzone/Rundung und Cache-
   Hit während eines Katalogreloads.
3. **Schemas:** missing/unknown property, falscher Typ, Enum, Pattern, Minimum,
   Maximum, Tiefe und Payload-Größe; invalider, unvollständiger oder
   bösartiger Tool-Output.
4. **Ausführung:** deaktiviertes Tool, Permission-Denial, Deadline,
   Transportabbruch, Retry und ein Judge-Vorschlag mit abweichenden
   Argumenten.
5. **Synthesis:** Merger/Critic ersetzen, runden, duplizieren oder entfernen
   einen Toolfakt; gemischte Antwort enthält mehrere identische Werte mit
   unterschiedlicher Bedeutung.
6. **Persistenz:** Gate blockiert, HITL pending/reject/approve, Client-
   Disconnect und doppelter Commit nach Retry/Resume.
7. **Fachgrenzen:** DST-Fold/-Gap, Monats-/Jahresgrenze, negative und große
   Decimal-Werte, Rundungsmodi, Wahrscheinlichkeit 0/1, Kombinatorikgrenzen,
   XML-Entities, YAML-Tags, CSV-Formelpräfixe und remote JSON-Schema-Refs.
8. **API/E2E:** Chat-, Template- und Responses-Fassade, Cold-/Warm-Start,
   direkter reiner Precision-Pfad und gemischter Expertenpfad.

## Messbare Gesamtabnahme

- Null LLM-only-Erfolge für den fest versionierten verpflichtenden
  Precision-Korpus.
- Null Precision-Cache-Hits vor Preflight und null Wiederverwendung eines
  abweichenden Contract-Hashes.
- 100 Prozent übereinstimmende finale Fakten in der adversarialen Merger-/
  Critic-Mutationsmatrix.
- Null semantische Cache-/Memory-/Learning-Writes vor erfolgreichem finalen
  Quality Gate; Audit- und Task-Ledger-Ereignisse bleiben vollständig.
- Reine deterministische Requests weisen im Audit null Planner-, Expert-,
  Judge-, Merger- und Critic-Modellaufrufe aus.
- Alle negativen Ein-/Ausgabeschemafälle liefern typisierte Fehler und niemals
  HTTP-Erfolg mit geratenem Ersatzwert.
- Bestehende Regression, Governance, `mkdocs build --strict`, Dependency-
  Checks, Compose-Config und fokussierter Diff-Check sind grün.
- Rebuild/Recreate erzeugen healthy Container mit RestartCount 0; Readiness
  und alle drei API-Fassaden sind positiv. Latenz p50/p95 sowie Cold-/Warm-
  Werte werden berichtet, nicht vorab behauptet.
- Telemetrie enthält keine Prompts, Credentials oder hoch-kardinalen
  Rohargumente; Request-, Contract- und Evidence-Hashes sind korrelierbar.

## Stop- und Rollback-Regeln

- Stoppen, wenn ein Pflichtintent im Enforce-Modus einen Legacy-Cache oder
  einen LLM-only-Pfad erreicht.
- Stoppen, wenn ein Schema-/Katalogwechsel innerhalb eines Requests nicht
  eindeutig erkannt wird.
- Kein Recreate bei aktiven Requests ohne sicheren Drain; kein defektes Image
  als laufenden Zustand belassen.
- Bei R0/R1-Fehlern Precision-Cache auf `bypass` belassen; bei R2/R3-Fehlern
  Direct Response beziehungsweise neuen Commit-Pfad abschalten und das letzte
  verifizierte Image wiederherstellen.
- Neue R4-Verträge bleiben im Shadow-Modus, bis ihr eigener Aufgaben-Proof und
  die Gesamtabnahme grün sind.
- Keine Credentials, Modelle, Expert Templates, Datenbestände, Commits,
  Pushes, PRs oder Veröffentlichungen ohne gesonderte Autorisierung.

## Optimierungen nach P0

Erst nach Abschluss dieser Story werden weitere deterministische Verträge
aktiviert: Business-Kalender mit versionierter Feiertagsquelle,
Versionsvergleiche, Identifier-Prüfung, fortgeschrittene Statistik,
Geodistanz/Koordinaten und tokenizergebundene Tokenmetriken. Arithmetic,
Einheiten, Hashes und CIDR werden nur erweitert, wenn ein typisierter
Extractor jeden neuen Intent adversarial bestanden hat.

## Abschlussnachweis

R4a bis R4d und R5 wurden am 1./2. August 2026 implementiert und lokal
abgenommen. Der Enforce-Korpus umfasst die zuvor migrierten Kalender-/GGT-/
Unit-Verträge sowie Zeit/Zeitzone, Decimal-Finanzmathematik, exakte
Wahrscheinlichkeit und strukturierte Validierung. Reine Requests passieren
MCP, Renderer, Binding und Quality Gate ohne Modellaufruf; Mixed-Requests
halten Fakten bis nach dem scoped Critic in opaken Slots.

Die vollständige Regression bestand mit 908 Tests. Methodik, Request-IDs,
Latenzen, Tokenwerte, Angriffsproben, Images und Rollback sind in
`docs/system/toolstack/precision_rollout_benchmark_2026-08-02.md`
dokumentiert. Das lokale Deployment ist auf Enforce aktiv; `shadow`, Direct-
Response-, Structured-Required- und Image-Rollback wurden praktisch geprüft.

# Experten-Vorlagen & Profile: Vollstaendiger Leitfaden

Dies ist der umfassende Leitfaden zum Vorlagen- und Profilsystem von
MoE Sovereign. Er behandelt die drei Verarbeitungsmodi, das Design von
Experten-Vorlagen, Claude-Code-Profile, die Integration interaktiver
Chat-Oberflaechen und saemtliche verfuegbaren Stellschrauben.

---

## 1. Verarbeitungsmodi: Native vs. Reasoning vs. Orchestrated

Jede Anfrage an MoE Sovereign wird in einem von drei Modi verarbeitet. Der
Modus wird durch das CC-Profil (fuer Claude Code) oder durch die als Modell
gewaehlte Experten-Vorlage (fuer OpenAI-kompatible Agenten) festgelegt.

### Native-Modus (`moe_mode: native`)

Ein einzelnes LLM bearbeitet die Anfrage direkt. Kein Planner, keine Experten,
kein Merger.

- Die Anfrage wird an einen einzigen Inferenz-Endpunkt weitergeleitet
- Das LLM verarbeitet Tool Calls (Datei-Edits, Bash) nativ
- Kein Pipeline-Overhead

**Wann einsetzen:** Schnelle Aenderungen, einfache Bugfixes, interaktives
Coding, Autovervollstaendigung, triviale Fragen.

**Wann vermeiden:** Domainen-uebergreifende Synthese, Rechercheaufgaben,
alles was Domaenen-Isolation oder Wissensakkumulation erfordert.

### MoE-Reasoning-Modus (`moe_mode: moe_reasoning`)

Ein einzelnes LLM mit aktiviertem Thinking Node. Das Modell fuehrt eine
Chain-of-Thought-Analyse durch, bevor es die Antwort generiert.

- Gestreamte `<think>`-Bloecke zeigen den Denkprozess in Echtzeit
- Die Denkspur ist im Thinking-Panel von Open WebUI und in der
  Claude-Code-Ausgabe sichtbar
- Kein paralleles Experten-Routing -- ein Modell erledigt alles, aber mit
  strukturierter Deliberation

**Wann einsetzen:** Architekturentscheidungen, komplexes Debugging, Code-Reviews,
bei denen der Denkprozess sichtbar sein soll.

**Wann vermeiden:** Einfache Aenderungen (Overkill), tiefgehende Recherchen
(nicht genug Domaenenabdeckung).

### MoE-Orchestrated-Modus (`moe_mode: moe_orchestrated`)

Die vollstaendige MoE-Pipeline wird aktiviert:

1. **Planner-LLM** zerlegt die Anfrage in 1-4 Teilaufgaben
2. **Experten-LLMs** bearbeiten Teilaufgaben parallel (domaenen-isoliert)
3. **MCP Precision Tools** uebernehmen deterministische Berechnungen (Mathematik, Regex, Subnetze usw.)
4. **GraphRAG** (Neo4j) injiziert akkumuliertes Wissen aus frueheren Anfragen
5. **SearXNG-Webrecherche** liefert aktuelle Informationen bei Bedarf
6. **Merger/Judge-LLM** synthetisiert alle Ergebnisse zu einer einheitlichen Antwort
7. **Critic Node** prueft sicherheitskritische Domaenen (Medizin, Recht) auf Fakten

**Wann einsetzen:** Sicherheitsaudits, Rechercheaufgaben, domaenen-uebergreifende
Synthese, wissensgestuetzte Analysen -- ueberall wo Qualitaet wichtiger ist als
Geschwindigkeit.

**Wann vermeiden:** Interaktives Coding, bei dem Antwortzeiten unter einer
Sekunde erwartet werden.

### Vergleichstabelle

| Aspekt | Native | Reasoning | Orchestrated |
|--------|--------|-----------|--------------|
| Latenz | 5-30s | 30-120s | 2-10 Min |
| Token-Kosten | 1x | 1,5x | 3-5x |
| Ausgabequalitaet | Gut | Besser | Am besten |
| Wissensakkumulation | Nein | Nein | Ja (GraphRAG) |
| Domaenen-Routing | Nein | Nein | Ja (15 Kategorien) |
| MCP Precision Tools | Nein | Nein | Ja (23 Tools) |
| Kumulativer Effekt | Nein | Nein | Ja (9,3x Beschleunigung ueber Zeit) |
| Denkbloecke | Nein | Ja | Optional |
| Parallele Experten-Ausfuehrung | Nein | Nein | Ja |
| Critic / Faktencheck | Nein | Nein | Ja (Medizin, Recht) |

!!! tip "Der kumulative Effekt"
    Im Orchestrated-Modus speichert GraphRAG Synthese-Ergebnisse als
    `SYNTHESIS_INSIGHT`-Relationen. Kuenftige Anfragen in derselben Domaene
    profitieren von diesem akkumulierten Wissen. Ueber 5 Epochen zeigen
    Benchmarks eine **9,3-fache Latenzreduktion** bei gleichzeitiger
    Qualitaetssteigerung um 56 %.
    Siehe [Claude Code Profile](claude-code-profiles.md#5-epochen-benchmark-ergebnisse)
    fuer die vollstaendigen Benchmark-Daten.

---

## 2. Design von Experten-Vorlagen

Eine Experten-Vorlage definiert die vollstaendige Konfiguration fuer einen
orchestrierten MoE-Pipeline-Durchlauf: welche LLMs zum Einsatz kommen, wie
Aufgaben zerlegt werden, welche Tools aktiviert sind und wie Ergebnisse
synthetisiert werden.

### Vorlagenstruktur

```json
{
  "id": "tmpl-xxxxx",
  "name": "template-name",
  "description": "Menschenlesbare Beschreibung des Vorlagenzwecks",
  "planner_prompt": "MoE orchestrator. Decompose the request into 1-4 subtasks...",
  "planner_model": "phi4:14b@N07-GT",
  "judge_prompt": "Synthesize all inputs into one complete response...",
  "judge_model": "phi4:14b@N04-RTX",
  "experts": {
    "code_reviewer": {
      "system_prompt": "Senior SWE: correctness, security (OWASP Top 10)...",
      "models": [
        {"model": "gpt-oss:20b", "endpoint": "N09-M60", "role": "primary"},
        {"model": "devstral-small-2:24b", "endpoint": "N04-RTX", "role": "fallback"}
      ]
    },
    "technical_support": {
      "system_prompt": "Senior IT/DevOps. Immediately executable solutions...",
      "models": [
        {"model": "phi4:14b", "endpoint": "N07-GT", "role": "primary"},
        {"model": "qwen3:32b", "endpoint": "N04-RTX", "role": "fallback"}
      ]
    }
  },
  "enable_cache": true,
  "enable_graphrag": true,
  "enable_web_research": true,
  "cost_factor": 1.0
}
```

### Feldreferenz

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `id` | string | Automatisch generierte eindeutige Vorlagen-ID (`tmpl-xxxxxxxx`) |
| `name` | string | Menschenlesbarer Name, erscheint in `/v1/models` |
| `description` | string | Wird in der Admin-UI und in Modelllisten angezeigt |
| `planner_prompt` | string | System-Prompt fuer das Planner-LLM |
| `planner_model` | string | Modell und optionaler Knoten fuer die Planung (`model@node` oder `model`) |
| `judge_prompt` | string | System-Prompt fuer das Judge/Merger-LLM |
| `judge_model` | string | Modell und optionaler Knoten fuer die Synthese |
| `experts` | object | Zuordnung von Kategoriename zu Experten-Konfiguration |
| `experts.*.system_prompt` | string | Domaenenspezifischer System-Prompt fuer den Experten |
| `experts.*.models` | array | Geordnete Liste von Modellzuweisungen (primary, fallback) |
| `enable_cache` | bool | L1/L2-Antwort- und Plan-Caching aktivieren |
| `enable_graphrag` | bool | Neo4j-Wissensgraph-Anreicherung aktivieren |
| `enable_web_research` | bool | SearXNG-Websuche fuer aktuelle Informationen aktivieren |
| `cost_factor` | float | Multiplikator fuer die Token-Budget-Abrechnung |

### T1/T2-Stufen-Strategie

Jeder Experte in einer Vorlage kann zwei Modell-Stufen haben:

- **T1 (Primaer):** Schnelle Modelle an oder unter der Stufengrenze (Standard:
  20 Milliarden Parameter). Diese antworten in unter 30 Sekunden und bearbeiten
  den Grossteil der Anfragen.
- **T2 (Fallback):** Groessere, leistungsfaehigere Modelle oberhalb von 20B.
  Werden nur aktiviert, wenn das T1-Modell `CONFIDENCE: low` (unter
  Schwellenwert 0,65) meldet.

Die Stufengrenze ist konfigurierbar ueber `EXPERT_TIER_BOUNDARY_B` (in
Milliarden Parametern). Der Standardwert von 20B bedeutet:

- Modelle wie `phi4:14b`, `hermes3:8b`, `gpt-oss:20b` sind T1
- Modelle wie `devstral-small-2:24b`, `qwen3-coder:30b`, `deepseek-r1:32b` sind T2

**Designprinzip:** T1 bearbeitet ca. 80 % der Anfragen schnell. T2 wird nur
bei Bedarf an Tiefe aktiviert -- das haelt die durchschnittliche Latenz niedrig
bei gleichzeitig hoher Qualitaet fuer schwierige Probleme.

### Gepinnte vs. Floating-Zuweisung

Modelle koennen auf zwei Arten zugewiesen werden:

**Gepinnt (`model@node`):**
```json
{"model": "phi4:14b", "endpoint": "N07-GT", "role": "primary"}
```
Das Modell laeuft immer auf dem angegebenen GPU-Knoten. Das garantiert
VRAM-Verfuegbarkeit und eliminiert Cold-Start-Latenz (das Modell bleibt
geladen). Gepinnte Zuweisungen eignen sich fuer Produktiv-Vorlagen mit
vorhersagbarer Performance.

**Floating (`model` allein):**
```json
{"model": "devstral-small-2:24b", "role": "fallback"}
```
Das System waehlt automatisch den besten verfuegbaren Knoten mit ausreichend
VRAM. Das bietet Elastizitaet -- das Modell kann auf dem jeweils am wenigsten
ausgelasteten Knoten laufen. Floating-Zuweisungen eignen sich fuer
T2-Fallback-Modelle und Workloads mit niedriger Prioritaet.

**Faustregel:** Planner und Judge auf schnelle Knoten pinnen (RTX-Klasse-GPUs).
T2-Experten-Modelle floaten lassen fuer Elastizitaet.

### Leitfaden zur LLM-Auswahl

Basierend auf empirischen Tests von 69 LLMs auf 5 Inferenz-Knoten:

#### Beste Planner-Modelle

| Modell | Latenz | Begruendung |
|--------|--------|-------------|
| `phi4:14b` | 27-37s | Zuverlaessigste JSON-Ausgabe. Konsistent und schnell. |
| `hermes3:8b` | 16s | Ultra-schnell fuer einfache Zerlegungen |
| `gpt-oss:20b` | 38s | Zuverlaessig, breit verfuegbar |
| `devstral-small-2:24b` | 45s | Stark bei code-bezogener Planung |

#### Beste Judge/Merger-Modelle

| Modell | Latenz | Begruendung |
|--------|--------|-------------|
| `phi4:14b` | 1,7-4,2s | Extrem schnelle Synthese |
| `qwen3-coder:30b` | 1,7s | Schnell, code-bewusst, starkes Instruction Following |
| `Qwen3-Coder-Next` (80B) | 2,6s | Hoechste Qualitaet, benoetigt aber viel VRAM |
| `devstral-small-2:24b` | 2,5s | Gut fuer code-fokussierte Synthese |

#### Modelle, die in Pipeline-Rollen vermieden werden sollten

| Modell | Problem |
|--------|---------|
| `qwen3.5:35b` | Thinking-Modus erzeugt `<think>`-Bloecke statt JSON -- bricht Planner- und Judge-Parsing |
| `deepseek-r1:32b` | Chain-of-Thought stoert die JSON-Ausgabe -- nur als Experte verwendbar |
| `starcoder2:15b` | Code-Completion-Modell ohne Instruction-Following-Faehigkeit |
| `gpt-oss:20b` (als Judge) | Besteht isolierte Tests, wird aber von Ollama-TTL zwischen Experten-Aufrufen entladen |

!!! warning "Thinking-Modelle brechen JSON"
    Modelle mit "Thinking"- oder "Reasoning"-Modi (`qwen3.5`, `deepseek-r1`)
    neigen dazu, ihre Ausgabe in `<think>`-Tags zu verpacken, was das
    JSON-Parsing der Planner- und Judge-Rollen bricht. Entweder
    Nicht-Reasoning-Modelle fuer diese Rollen verwenden oder Thinking
    explizit im System-Prompt deaktivieren.

### Best Practices fuer System-Prompts

#### Planner-Prompts

**Empfohlen:**

- Explizit ausschliesslich JSON-Ausgabe verlangen
- Alle gueltigen Kategorien im Prompt auflisten
- Ein Formatbeispiel mit dem exakten JSON-Schema bereitstellen
- Den `PRECISION_TOOLS`-Block fuer MCP-Routing einbinden

**Vermeiden:**

- Freitext-Erklaerungen neben JSON erlauben
- Thinking/Reasoning-Anweisungen verwenden
- Markdown-Formatierung anfordern

#### Judge/Merger-Prompts

**Empfohlen:**

- Anweisen, Code-Bloecke wortwortlich zu uebernehmen
- Provenienz-Tags (`[REF:entity]`) verlangen, die angeben, welcher Experte welche Erkenntnis beigesteuert hat
- Verifizierungsschritte fuer numerische Werte fordern
- Explizite Prioritaet definieren: MCP > Graph > Experten mit hoher Konfidenz > Web > mittel > niedrig/Cache

**Vermeiden:**

- Zusammenfassung von Code-Bloecken erlauben
- Sicherheitsbefunde ueberspringen lassen

#### Experten-Prompts

**Empfohlen:**

- Die Domaenengrenze des Experten klar definieren
- Strukturierte Ausgabe-Marker verlangen: `CONFIDENCE`, `GAPS`, `REFERRAL`
- Domaenenspezifische Methodik einbeziehen (OWASP fuer Sicherheit, S3/AWMF fuer Medizin usw.)
- Mit Sprachdurchsetzung abschliessen

**Vermeiden:**

- Domaenen mischen (ein Sicherheitsexperte soll nicht den Code-Stil kommentieren)
- Dem Experten erlauben, mit "Ich kann dabei nicht helfen" abzulehnen

### Dienst-Schalter

Jede Vorlage kann Pipeline-Komponenten einzeln aktivieren oder deaktivieren:

| Schalter | Standard | Auswirkung bei Deaktivierung |
|----------|----------|------------------------------|
| `enable_cache` | `true` | Jede Anfrage durchlaeuft die vollstaendige Pipeline -- keine Cache-Treffer. Nuetzlich zum Testen oder wenn frische Antworten erforderlich sind. |
| `enable_graphrag` | `true` | Keine Wissensgraph-Anreicherung. Fruehere Synthese-Ergebnisse werden nicht injiziert. Fuer datenschutzsensible Anfragen ohne Wissenspersistenz. |
| `enable_web_research` | `true` | Keine SearXNG-Websuche. Antworten stuetzen sich ausschliesslich auf Modellwissen und GraphRAG. Fuer Air-Gap-Umgebungen oder geschwindigkeitskritische Aufgaben. |

### Compliance-Badges

Vorlagen werden automatisch nach dem Ausfuehrungsort ihrer Modelle klassifiziert:

| Badge | Bedeutung |
|-------|-----------|
| **Local Only** (gruen) | Alle Modelle laufen auf lokaler Infrastruktur. Keine Daten verlassen das Netzwerk. |
| **Mixed** (gelb) | Einige Modelle nutzen externe APIs. Daten koennen fuer diese Aufrufe das Netzwerk verlassen. |
| **External** (rot) | Ueberwiegend externe API-Modelle. Die meisten Daten verlassen das Netzwerk. |

Das Compliance-Badge ist in der Admin-UI sichtbar und hilft CISOs, die
Datenresidenz auf einen Blick einzuschaetzen.

---

## 3. Claude Code Profile

Claude-Code-Profile (CC-Profile) steuern, wie MoE Sovereign Anfragen von
Claude Code CLI, der VS-Code-Extension und anderen Anthropic-API-Clients
verarbeitet. Jedes Profil bildet auf einen bestimmten Verarbeitungsmodus ab.

### Profilfelder

| Feld | Beschreibung | Beispiel |
|------|--------------|---------|
| `name` | Anzeigename in Clients | `cc-ref-native` |
| `moe_mode` | Verarbeitungsmodus | `native`, `moe_reasoning`, `moe_orchestrated` |
| `tool_model` | LLM fuer Tool-Ausfuehrung | `gemma4:31b` |
| `tool_endpoint` | Inferenz-Server-Knoten | `N04-RTX` |
| `expert_template_id` | Experten-Vorlage fuer Orchestrated-Modus | `tmpl-d2300eb6` |
| `tool_max_tokens` | Max. Ausgabe-Tokens fuer Tool Calls | `4096` |
| `reasoning_max_tokens` | Max. Tokens fuer Denkbloecke | `16384` |
| `tool_choice` | Tool-Auswahlverhalten | `auto`, `required`, `any` |
| `stream_think` | Ob Denkbloecke gestreamt werden | `true` / `false` |

### Drei Referenzprofil-Familien

#### Referenzprofile (Basis)

| Profil | Modus | Latenz | Einsatzzweck |
|--------|-------|--------|--------------|
| `cc-ref-native` | `native` | 5-30s | Schnelle Aenderungen, einfache Fixes, interaktives Coding |
| `cc-ref-reasoning` | `moe_reasoning` | 30-120s | Architekturentscheidungen, komplexes Debugging |
| `cc-ref-orchestrated` | `moe_orchestrated` | 2-10 Min | Tiefgehende Recherche, Sicherheitsaudits, domaenen-uebergreifende Synthese |

Referenzprofile verwenden konservative Einstellungen und eignen sich fuer die
meisten Anwender direkt nach der Installation.

#### Innovator-Profile (Power User)

Alle drei Innovator-Profile nutzen den `moe_orchestrated`-Modus mit dedizierten
Experten-Vorlagen, die auf unterschiedliche Qualitaets-/Geschwindigkeits-
Kompromisse abgestimmt sind:

| Profil | Tool-Modell | Thinking | Max Tokens | Ziel-Latenz |
|--------|------------|----------|------------|-------------|
| `cc-innovator-fast` | `gemma4:31b` | Aus | 4.096 | 30-90s |
| `cc-innovator-balanced` | `Qwen3-Coder-Next` | An | 8.192 / 16K Reasoning | 2-5 Min |
| `cc-innovator-deep` | `Qwen3-Coder-Next` | An | 8.192 / 32K Reasoning | 5-15 Min |

**Wesentliche Unterschiede:**

- **Fast** nutzt `tool_choice: required` mit leichtgewichtigen Modellen und
  `stream_think: false` fuer minimalen Overhead. Ideal fuer schnelle Iterationszyklen.
- **Balanced** aktiviert Denkbloecke und eskaliert ueber T2-Fallback zu
  Domaenen-Spezialistenmodellen. Guter Standard fuer den Alltagsbetrieb.
- **Deep** setzt die groessten verfuegbaren Modelle ein, inklusive einer
  Security-Analyst-Expertenkategorie und einem assertiven System-Prompt, der
  vollstaendigen, produktionsreifen Code verlangt. Ideal fuer Sicherheitsaudits
  und Architektur-Reviews.

### Benutzerdefinierte Profile erstellen

1. In der Admin-UI zu **CC Profile** navigieren
2. **Create Profile** klicken
3. Die Profilfelder ausfuellen (siehe Tabelle oben)
4. Eine Experten-Vorlage fuer den Orchestrated-Modus zuweisen
5. Profil speichern

Benutzer koennen auch persoenliche Profile im User Portal unter
**My Templates** > **CC Profiles** erstellen. Persoenliche Profile
ueberschreiben administrativ zugewiesene Profile.

### Profile an API-Keys binden

1. Admin-UI > **Users** > Benutzer auswaehlen > **API Keys**
2. Im **CC Profile**-Dropdown den Ziel-Key auswaehlen
3. Alle mit diesem Key getaetigten Anfragen nutzen nun das gebundene Profil

So kann ein einzelner Benutzer mehrere API-Keys besitzen, jeden an ein
anderes Profil gebunden, und zwischen Workflows durch Wechsel des
exportierten Keys umschalten.

### 5-Epochen-Benchmark-Ergebnisse

Ein kontrollierter Benchmark ueber 5 aufeinanderfolgende Durchlaeufe misst
den kumulativen Effekt der MoE-Wissens-Pipeline:

| Epoche | Durchschn. Score | Durchschn. Latenz | Latenz vs. Epoche 1 |
|-------:|-----------------:|------------------:|--------------------:|
| 1 | 5,2 / 10 | 280s | Baseline |
| 2 | 6,4 / 10 | 125s | 0,45x |
| 3 | 7,1 / 10 | 72s | 0,26x |
| 4 | 7,8 / 10 | 45s | 0,16x |
| 5 | 8,1 / 10 | 30s | 0,11x |

Ab Epoche 5 liefert das System **9,3-mal schnellere** Antworten als in
Epoche 1 bei gleichzeitiger Qualitaetssteigerung um 56 %. Drei Mechanismen
treiben diesen Effekt:

1. **GraphRAG-Kontextanreicherung** -- fruehere Synthese-Ergebnisse werden
   als `SYNTHESIS_INSIGHT`-Relationen gespeichert und in kuenftige
   Experten-Prompts injiziert
2. **L2-Plan-Cache** -- identische Aufgabenzerlegungen treffen den Valkey
   SHA-256-Plan-Cache und ueberspringen das Planner-LLM vollstaendig
3. **Modell-Waerme** -- Sticky Sessions halten haeufig genutzte Modelle im
   VRAM geladen und eliminieren Cold-Start-Overhead

---

## 4. Interaktiver Chat (Open WebUI, AnythingLLM)

Experten-Vorlagen sind nicht auf Coding-Agenten beschraenkt. Sie funktionieren
ebenso mit interaktiven Chat-Oberflaechen wie Open WebUI und AnythingLLM.

### Funktionsweise

Jede in MoE Sovereign konfigurierte Experten-Vorlage erscheint als "Modell"
in der `/v1/models`-Endpunkt-Antwort. Chat-Oberflaechen, die
OpenAI-kompatible Backends unterstuetzen, koennen diese Modelle direkt
auswaehlen.

```bash
# Alle verfuegbaren Modelle (Vorlagen) auflisten
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer moe-sk-xxxxxxxx..." | jq '.data[].id'
```

### Empfohlene Vorlagen nach Einsatzzweck

| Einsatzzweck | Empfohlene Vorlage | Begruendung |
|--------------|-------------------|-------------|
| Schnelle Fragen | `8b-fast` oder Native-Vorlage | Minimale Latenz, gut fuer einfache Fragen |
| Code-Unterstuetzung | `30b-balanced` | Starke Code-Modelle, akzeptable Latenz |
| Tiefgehende Recherche | `70b-deep` oder `cc-expert-deep` | Vollstaendige Pipeline mit den groessten Modellen |
| Kreatives Schreiben | Vorlage mit `creative_writer`-Experte | Stilistisch abgestimmte Modelle |
| Datenanalyse | Vorlage mit `data_analyst` + MCP-Tools | MCP liefert exakte Berechnungen |

### Der kumulative Effekt in Chat-Sitzungen

In interaktiven Sitzungen mit orchestrierten Vorlagen reichert jeder
Austausch den GraphRAG-Wissensgraphen an. Das bedeutet:

- **Erste Frage** in einer neuen Domaene: vollstaendige Pipeline, hoechste Latenz
- **Folgefragen** in derselben Domaene: GraphRAG injiziert frueheren Kontext,
  reduziert Latenz und verbessert Relevanz
- **Rueckkehr zu einem Thema Tage spaeter**: akkumuliertes Wissen ist weiterhin
  verfuegbar und bietet Kontinuitaet ueber Sitzungen hinweg

Dieser kumulative Effekt ist besonders wertvoll fuer laufende Projekte,
Recherchethemen oder Domaenen, die wiederholt abgefragt werden.

### Tipps zur Sitzungsverwaltung

- **Breit beginnen, dann einengen.** Die erste Frage in einer Domaene befuellt
  den Wissensgraphen. Nachfolgende spezifische Fragen profitieren von diesem
  Kontext.
- **Dieselbe Vorlage** fuer zusammenhaengende Fragen innerhalb eines Projekts
  verwenden, um GraphRAG-Kontinuitaet sicherzustellen.
- **Auf Native-Modus wechseln** fuer schnelle Nachfragen, die nicht die
  vollstaendige Pipeline benoetigen (z.B. "Formatiere das als Tabelle").
- **Das Thinking-Panel** in Open WebUI nutzen, um genau zu sehen, welche
  Experten konsultiert, welche MCP-Tools ausgefuehrt und welcher
  GraphRAG-Kontext injiziert wurde.

---

## 5. Stellschrauben und Kompromisse

Jede Konfigurationsentscheidung in MoE Sovereign ist ein Kompromiss. Dieser
Abschnitt erlaeutert die Funktion jeder Stellschraube und hilft bei
informierten Entscheidungen.

### Cache-Schalter (`enable_cache`)

Steuert die L1- (ChromaDB) und L2-Caching-Schichten (Valkey).

| Status | Verhalten |
|--------|-----------|
| **Aktiviert** (Standard) | L1-Treffer = sofortige Antwort (keine Pipeline). L2-Plan-Cache-Treffer = Planner ueberspringen, nur Experten ausfuehren. |
| **Deaktiviert** | Jede Anfrage durchlaeuft die vollstaendige Pipeline von Grund auf. |

**Auswirkung:** L1-Cache-Treffer reduzieren die Latenz auf nahezu Null fuer
wiederholte oder semantisch aehnliche Anfragen. Deaktivierung ist nuetzlich
zum Testen, Debuggen oder wenn garantiert frische Antworten benoetigt werden.

### GraphRAG-Schalter (`enable_graphrag`)

Steuert die Neo4j-Wissensgraph-Anreicherung.

| Status | Verhalten |
|--------|-----------|
| **Aktiviert** (Standard) | Fruehere Synthese-Ergebnisse werden gespeichert und in kuenftige Anfragen injiziert. Wissen akkumuliert ueber die Zeit. |
| **Deaktiviert** | Keine Wissenspersistenz. Jede Anfrage ist unabhaengig. |

**Auswirkung:** Aktiviertes GraphRAG treibt den kumulativen Effekt (9,3x
Beschleunigung ueber 5 Epochen). Deaktivierung ist angemessen fuer
datenschutzsensible Anfragen ohne Wissenspersistenz oder fuer isolierte
Einzelanfragen.

### Webrecherche-Schalter (`enable_web_research`)

Steuert die SearXNG-Websuch-Integration.

| Status | Verhalten |
|--------|-----------|
| **Aktiviert** (Standard) | Der Planner kann Teilaufgaben mit `"web": true` markieren, was eine SearXNG-Suche ausloest. Ergebnisse werden in den Merger injiziert. |
| **Deaktiviert** | Keine Websuche. Antworten stuetzen sich ausschliesslich auf Modell-Trainingsdaten und GraphRAG-Kontext. |

**Auswirkung:** Aktivierte Webrecherche verbessert die Aktualitaet fuer
laufende Ereignisse, neueste Dokumentation und sich schnell aendernde
Domaenen. Deaktivierung reduziert die Latenz (kein Such-Roundtrip) und ist
erforderlich fuer Air-Gap-Deployments.

### T1/T2-Stufengrenze (`EXPERT_TIER_BOUNDARY_B`)

Der Parametergroessen-Schwellenwert (in Milliarden), der T1 (schnell) von
T2 (tiefgehend) trennt. Standard: **20B**.

| Grenze | T1-Modelle (schnell) | T2-Modelle (tiefgehend) |
|--------|---------------------|------------------------|
| 20B (Standard) | `phi4:14b`, `hermes3:8b`, `gpt-oss:20b` | `devstral-small-2:24b`, `qwen3-coder:30b`, `deepseek-r1:32b` |
| 14B (aggressiv) | nur `hermes3:8b` | Alles ueber 14B |
| 30B (konservativ) | Die meisten Modelle inkl. 24B | Nur Modelle ab 32B |

**Kompromiss:** Eine niedrigere Grenze fuehrt dazu, dass mehr Anfragen an T2
eskaliert werden (hoehere Qualitaet, hoehere Latenz). Eine hoehere Grenze haelt
mehr Anfragen auf T1 (schneller, aber potenziell niedrigere Qualitaet fuer
schwierige Probleme).

### Gepinntes vs. Floating-Deployment

| Strategie | Vorteile | Nachteile |
|-----------|----------|-----------|
| **Gepinnt** (`model@node`) | Vorhersagbare Latenz, keine Cold Starts, garantiertes VRAM | Keine Elastizitaet, Knotenausfall = Experte nicht verfuegbar |
| **Floating** (`model` allein) | Elastisch, automatischer Lastausgleich ueber Knoten | Cold-Start-Latenz wenn Modell nicht geladen, weniger vorhersagbar |

**Best Practice:** Planner und Judge pinnen (sie laufen bei jeder Anfrage und
benoetigen konsistent niedrige Latenz). T2-Fallback-Experten floaten lassen
(sie laufen selten und profitieren von Elastizitaet).

### VRAM-Planung pro Vorlage

Um den gesamten VRAM-Bedarf einer Vorlage zu berechnen, die VRAM-Anforderungen
aller gleichzeitig geladenen Modelle summieren:

```
Gesamt-VRAM = Planner + Judge + (Anzahl paralleler Experten x groesstes Experten-Modell)
```

**Ungefaehrer VRAM-Bedarf pro Modellgroesse:**

| Modellgroesse | VRAM (Q4-quantisiert) | VRAM (FP16) |
|---------------|----------------------:|------------:|
| 7-8B | ~5 GB | ~16 GB |
| 14B | ~9 GB | ~28 GB |
| 20-24B | ~14 GB | ~48 GB |
| 30-32B | ~20 GB | ~64 GB |
| 70B | ~40 GB | ~140 GB |

**Beispiel:** Eine Vorlage mit `phi4:14b` (Planner, 9 GB) + `phi4:14b`
(Judge, geteilt, 0 zusaetzlich) + 3 parallelen Experten mit `gpt-oss:20b`
(3 x 14 GB = 42 GB) benoetigt ungefaehr **51 GB** Gesamt-VRAM ueber
alle Knoten.

!!! tip "Geteilte Modelle sparen VRAM"
    Wenn Planner und Judge dasselbe Modell auf demselben Knoten verwenden,
    wird das Modell einmal geladen. Ebenso wird VRAM geteilt, wenn mehrere
    Experten dasselbe Modell auf demselben Knoten nutzen. Vorlagen sollten
    Modelle wo moeglich wiederverwenden.

### Entscheidungsmatrix

| Prioritaet | Empfohlene Einstellungen |
|------------|------------------------|
| **Minimale Latenz** | Native-Modus, Cache aktiviert, kein GraphRAG, keine Webrecherche |
| **Maximale Qualitaet** | Orchestrated-Modus, alle Schalter aktiviert, T2-Grenze bei 14B |
| **Datenschutz / Air-Gap** | Beliebiger Modus, GraphRAG deaktiviert, Webrecherche deaktiviert |
| **Kosteneffizienz** | Native fuer einfache Aufgaben, Orchestrated nur fuer komplexe. Cache aktiviert. |
| **Wissensaufbau** | Orchestrated-Modus, GraphRAG aktiviert, Cache aktiviert |
| **Vorhersagbare Performance** | Alle Modelle gepinnt, Cache aktiviert |
| **Maximale Elastizitaet** | Alle Modelle floating, Cache aktiviert |

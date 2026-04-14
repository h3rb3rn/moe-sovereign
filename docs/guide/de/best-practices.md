# Best Practices: LLM-Auswahl & Vorlagen-Design

Dieser Leitfaden basiert auf empirischen Tests von 69 LLMs auf 5 Inferenz-Knoten
und deckt die Eignung fuer Planner-, Judge- und Experten-Rollen ab.

## LLM-Auswahl fuer Pipeline-Rollen

### Planner (Aufgabenzerlegung)

Der Planner muss **strikt valides JSON** ausgeben -- kein Prosa, keine
Markdown-Fences, keine Denkbloecke. Das schliesst ueberraschend viele Modelle aus.

| Stufe | Empfohlene Modelle | Latenz | Hinweise |
|-------|-------------------|--------|----------|
| **Beste** | `phi4:14b` | 27-36s | Schnellster zuverlaessiger Planner. Konsistente JSON-Ausgabe. |
| **Beste** | `hermes3:8b` | 16s | Ultra-schnell, gut fuer einfache Zerlegungen |
| **Gut** | `gpt-oss:20b` | 38s | Zuverlaessig, breit verfuegbar |
| **Gut** | `devstral-small-2:24b` | 45s | Stark bei code-bezogener Planung |
| **Gut** | `nemotron-cascade-2:30b` | ~200s | Ausgezeichnete Qualitaet, aber langsam |
| **Vermeiden** | `qwen3.5:35b` | FAIL | Thinking-Modus erzeugt `<think>`-Bloecke statt JSON |
| **Vermeiden** | `deepseek-r1:32b` | Nur P | Chain-of-Thought stoert die JSON-Ausgabe |
| **Vermeiden** | `starcoder2:15b` | FAIL | Code-Completion-Modell ohne Instruction Following |

**Zentrale Erkenntnis:** Modelle mit "Thinking"- oder "Reasoning"-Modi
(`qwen3.5`, `deepseek-r1`) neigen dazu, ihre Ausgabe in `<think>`-Tags zu
verpacken, was das JSON-Parsing bricht. Im Planner-Prompt den Thinking-Modus
deaktivieren oder Nicht-Reasoning-Modelle verwenden.

### Judge / Merger (Antwortsynthese & Bewertung)

Der Judge muss mehrere Experten-Antworten synthetisieren UND strukturierte
Ausgabe liefern (Scores, Provenienz-Tags). Dafuer ist starkes
Instruction Following erforderlich.

| Stufe | Empfohlene Modelle | Latenz | Hinweise |
|-------|-------------------|--------|----------|
| **Beste** | `phi4:14b` | 1,7-4,2s | Extrem schnelle Judge-Antworten |
| **Beste** | `qwen3-coder:30b` | 1,7s | Schnelle, code-bewusste Synthese |
| **Gut** | `Qwen3-Coder-Next` (80B) | 2,6s | Hoechste Qualitaet, aber gross |
| **Gut** | `devstral-small-2:24b` | 2,5s | Gut fuer code-fokussierte Synthese |
| **Gut** | `glm-4.7-flash` | 15s | Starke allgemeine Synthese |
| **Vermeiden** | `gpt-oss:20b` in der Pipeline | -- | Funktioniert isoliert, wird aber von Ollama-TTL zwischen Experten-Aufrufen entladen |
| **Vermeiden** | `qwen3.5:35b` | FAIL | Selbes Thinking-Modus-Problem wie beim Planner |

**Kritischer Befund:** `gpt-oss:20b` besteht isolierte Judge-Tests (4,7s,
valides JSON), schlaegt aber in der MoE-Pipeline fehl, weil Ollama es zwischen
den Experten-Inferenz-Aufrufen entlaedt. Loesung: Sticky Sessions oder einen
dedizierten Judge-Knoten verwenden.

### Experten-Modelle

Experten sind toleranter -- sie produzieren Freitext-Antworten, kein
strukturiertes JSON. Nahezu jedes Instruction-Following-Modell funktioniert
als Experte.

| Domaene | Empfohlen | Begruendung |
|---------|-----------|-------------|
| Code Review | `devstral-small-2:24b` | SWE-bench 68 %, code-fokussiert |
| Code-Generierung | `qwen3-coder:30b` | 370 Sprachen, starkes Tool Calling |
| Reasoning | `deepseek-r1:32b` | Bestes Chain-of-Thought auf Consumer-GPUs |
| Sicherheitsanalyse | `devstral-small-2:24b` | CWEval-faehig, OWASP-Abdeckung |
| Recherche | `gemma4:31b` | Starkes Allgemeinwissen |
| Mathematik | `phi4:14b` + MCP-Tools | MCP uebernimmt die Berechnung, LLM extrahiert Parameter |
| Recht | `gpt-oss:20b` | Deutsches Rechtswissen, Gesetze-im-Internet-Tools |

## Vorlagen-Komposition

### T1/T2-Stufen-Strategie

- **T1 (Primaer, bis 20B):** Schnelle Ersteinschaetzung. Modelle mit Antwortzeiten
  unter 30 Sekunden. Verwende `phi4:14b`, `hermes3:8b`, `gpt-oss:20b`.
- **T2 (Fallback, ueber 20B):** Tiefgehende Analyse. Wird nur aktiviert, wenn
  T1 `CONFIDENCE: low` meldet. Verwende `devstral-small-2:24b`,
  `qwen3-coder:30b`, `deepseek-r1:32b`.

### Knotenzuweisung

- **Gepinnt (`model@node`):** Fuer Produktiv-Vorlagen. Garantiert VRAM-Verfuegbarkeit.
- **Floating (`model` allein):** Fuer elastische/niedrig priorisierte Workloads.
  Das System findet automatisch den besten verfuegbaren Knoten.

Regel: Planner und Judge auf schnelle Knoten (RTX) pinnen. T2-Experten floaten.

### Dienst-Schalter

Jede Vorlage kann Pipeline-Komponenten deaktivieren:

| Schalter | Standard | Wann deaktivieren |
|----------|----------|-------------------|
| `enable_cache` | true | Testen, Debugging (frische Antworten benoetigt) |
| `enable_graphrag` | true | Datenschutzsensible Anfragen (keine Wissenspersistenz) |
| `enable_web_research` | true | Air-Gap-Umgebungen, geschwindigkeitskritische Aufgaben |

### Compliance-Badge

Vorlagen werden automatisch klassifiziert:

- **Local Only** (gruen): Alle Modelle auf lokaler Infrastruktur
- **Mixed** (gelb): Einige Modelle auf externen APIs
- **External** (rot): Ueberwiegend externe APIs

Der CISO sieht auf einen Blick, ob Daten das Netzwerk verlassen.

## System-Prompt-Engineering

### Planner-Prompts

**Empfohlen:**

- Explizit ausschliesslich JSON-Ausgabe verlangen
- Gueltige Kategorien auflisten
- Formatbeispiele bereitstellen
- `PRECISION_TOOLS`-Block fuer MCP-Routing einbinden

**Vermeiden:**

- Freitext-Erklaerungen erlauben
- Thinking/Reasoning-Anweisungen verwenden
- Markdown-Formatierung anfordern

### Judge-Prompts

**Empfohlen:**

- Anweisen, Code-Bloecke wortwortlich zu uebernehmen
- Provenienz-Tags `[REF:entity]` verlangen
- Verifizierungsschritte fordern
- Angeben lassen, welcher Experte welche Erkenntnis beigesteuert hat

**Vermeiden:**

- Zusammenfassung von Code erlauben
- Sicherheitsbefunde ueberspringen lassen

### Experten-Prompts

**Empfohlen:**

- Die Domaenengrenze des Experten klar definieren
- Strukturierte Ausgabe verlangen (CONFIDENCE, GAPS, REFERRAL)
- Domaenenspezifische Methodik einbeziehen (OWASP fuer Sicherheit usw.)
- Mit Sprachdurchsetzung abschliessen

**Vermeiden:**

- Domaenen mischen (Sicherheitsexperte soll NICHT den Stil kommentieren)
- Dem Experten erlauben abzulehnen ("Ich kann dabei nicht helfen")

## CC-Profil-Best-Practices

| Profiltyp | Tool-Modell | Thinking | Max Tokens | Einsatzzweck |
|-----------|------------|----------|------------|--------------|
| Fast | `gemma4:31b` | aus | 4.096 | Schnelle Aenderungen, einfache Fragen |
| Balanced | `Qwen3-Coder-Next` | an | 8.192 / 16K Reasoning | Tagesgeschaeft Entwicklung |
| Deep | `Qwen3-Coder-Next` | an | 8.192 / 32K Reasoning | Architektur, Sicherheitsaudits |

**Wichtig:** Die Einstellung `tool_choice: required` zwingt das Modell, stets
Tools zu verwenden, wenn diese verfuegbar sind. Das ist entscheidend fuer die
Claude-Code-Integration -- ohne diese Einstellung generiert das Modell
moeglicherweise Prosa statt Datei-Edits auszufuehren.

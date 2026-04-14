# Agenten-Integrationsprofile

Verbinde deinen bevorzugten Coding-Agenten in weniger als fuenf Minuten mit
MoE Sovereign. Diese Seite beschreibt die Konfiguration fuer alle unterstuetzten
Agenten, erlaeutert die verwendeten API-Endpunkte und zeigt die
Feature-Kompatibilitaet im Ueberblick.

---

## Funktionsweise

MoE Sovereign stellt zwei API-Endpunkte bereit:

| Endpunkt | Protokoll | Genutzt von |
|----------|-----------|-------------|
| `/v1/messages` | Anthropic Messages API | Claude Code |
| `/v1/chat/completions` | OpenAI Chat Completions API | OpenCode, Claw Code, Codex CLI, Aider, Continue.dev, Cursor, Open WebUI |

Beide Endpunkte leiten Anfragen durch dieselbe MoE-Pipeline. Welcher Endpunkt
zum Einsatz kommt, haengt ausschliesslich davon ab, welches Protokoll der
jeweilige Agent erwartet -- die Verarbeitung im Hintergrund ist identisch.

---

## Claude Code

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) ist Anthropics
offizieller CLI-Agent. Da er das Anthropic Messages API nativ spricht, lassen
sich saemtliche MoE-Sovereign-Features ohne Adapter-Schicht nutzen.

### Schnellstart

```bash
# ~/.bashrc oder ~/.zshrc
export ANTHROPIC_BASE_URL=https://your-moe-instance.example.com
export ANTHROPIC_API_KEY=moe-sk-xxxxxxxx...

# Claude Code starten -- alle Anfragen laufen ab sofort ueber MoE Sovereign
claude
```

Nach dem Bearbeiten der Shell-Konfiguration `source ~/.bashrc` (bzw.
`source ~/.zshrc`) ausfuehren, bevor `claude` gestartet wird.

### VS Code Extension

Wer die Claude Code VS Code Extension statt der CLI nutzt:

```json
{
  "claude-code.apiEndpoint": "https://your-moe-instance.example.com",
  "claude-code.apiKey": "moe-sk-xxxxxxxx..."
}
```

### Unterstuetzte Features

| Feature | Status | Hinweise |
|---------|--------|----------|
| `/v1/messages`-Endpunkt | Vollstaendig | Natives Anthropic-Protokoll |
| Streaming (SSE) | Vollstaendig | Echtzeit-Token-Ausgabe |
| Tool Use / Function Calling | Vollstaendig | Datei-Edits, Bash, MCP-Tools |
| Mehrteilige Konversationen | Vollstaendig | Kompletter Gespraechsverlauf bleibt erhalten |
| Denkbloecke (`<think>`) | Vollstaendig | Erfordert `moe_reasoning` oder `moe_orchestrated` Modus |
| Bildeingaben | Unterstuetzt | Werden an vision-faehige Modelle weitergeleitet |
| System-Prompts | Unterstuetzt | CLAUDE.md, Projektanweisungen werden durchgereicht |

### Profil-Umschaltung ueber API-Key

Jeder API-Key kann in der Admin-UI an ein bestimmtes **CC-Profil** gebunden
werden. Damit wird festgelegt, welcher MoE-Modus (native, reasoning,
orchestrated) fuer alle Anfragen mit diesem Key gilt.

**Einrichtung:**

1. Admin-UI > **Users** > Benutzer auswaehlen > **API Keys**
2. Im **CC Profile**-Dropdown das gewuenschte Profil setzen
3. Alle Anfragen mit diesem Key nutzen nun automatisch das gebundene Profil

So lassen sich mehrere API-Keys fuer unterschiedliche Workflows anlegen:

```bash
# Key gebunden an cc-ref-native -- schnelles interaktives Coding
export ANTHROPIC_API_KEY=moe-sk-native-key...

# Key gebunden an cc-ref-orchestrated -- tiefgehende Recherche
export ANTHROPIC_API_KEY=moe-sk-deep-key...
```

Zwischen Profilen wechselt man einfach durch Aendern des exportierten Keys.

### Einschraenkungen

- Claude Code spricht ausschliesslich das Anthropic-Protokoll (`/v1/messages`)
  und kann den OpenAI-kompatiblen Endpunkt nicht direkt verwenden.
- Die Modellauswahl innerhalb von Claude Code (`/model`-Befehl) waehlt aus
  den von `/v1/models` gemeldeten Modellen. Das tatsaechliche LLM-Routing
  wird durch die Experten-Vorlage des gebundenen CC-Profils bestimmt, nicht
  allein durch den Modellnamen.

---

## OpenCode

[OpenCode](https://github.com/opencode-ai/opencode) ist ein Go-basierter,
quelloffener Terminal-Coding-Agent mit Unterstuetzung fuer ueber 75 LLM-Anbieter.
Er nutzt den OpenAI-kompatiblen `/v1/chat/completions`-Endpunkt.

### Schnellstart

Erstelle oder bearbeite `~/.config/opencode/config.toml`:

```toml
[providers.moe]
name = "MoE Sovereign"
base_url = "https://your-moe-instance.example.com/v1"
api_key = "moe-sk-xxxxxxxx..."
models = ["moe-reference-30b-balanced"]
```

Dann OpenCode starten:

```bash
opencode
```

### Hinweise

- OpenCode erkennt verfuegbare Modelle ueber `/v1/models`. Alle in MoE Sovereign
  konfigurierten Experten-Vorlagen erscheinen als waehlbare Modelle.
- Tool-Use-Unterstuetzung haengt von der OpenCode-Version ab. Die aktuelle
  Projektdokumentation enthaelt Details zu den neuesten Features.
- Streaming wird vollstaendig unterstuetzt.

---

## Claw Code

[Claw Code](https://github.com/claw-project/claw-code) ist ein Python-basierter,
quelloffener Coding-Agent, inspiriert von Claude Code. Er arbeitet ueber den
OpenAI-kompatiblen Endpunkt.

### Schnellstart

```bash
export OPENAI_BASE_URL=https://your-moe-instance.example.com/v1
export OPENAI_API_KEY=moe-sk-xxxxxxxx...
export OPENAI_MODEL=moe-reference-30b-balanced

claw-code
```

### Hinweise

- Claw Code unterstuetzt Tool Use (Datei-Edits, Bash-Ausfuehrung) ueber das
  OpenAI Function Calling-Protokoll.
- Streaming wird vollstaendig unterstuetzt.
- Da Claw Code den OpenAI-Endpunkt nutzt, steht die CC-Profil-Auswahl per
  API-Key-Binding nicht zur Verfuegung. Der Modellname in `OPENAI_MODEL`
  bestimmt, welche Experten-Vorlage verwendet wird.

---

## Codex CLI (OpenAI)

[Codex CLI](https://github.com/openai/codex) ist OpenAIs offizieller
Terminal-Agent. Er unterstuetzt benutzerdefinierte Base-URLs fuer
OpenAI-kompatible Backends.

### Schnellstart

```bash
export OPENAI_BASE_URL=https://your-moe-instance.example.com/v1
export OPENAI_API_KEY=moe-sk-xxxxxxxx...

codex --model moe-reference-30b-balanced
```

### Hinweise

- Codex CLI erwartet ein vollstaendig OpenAI-kompatibles Backend. Der
  `/v1/chat/completions`-Endpunkt von MoE Sovereign erfuellt diese Anforderung.
- Tool Use und Streaming werden unterstuetzt.
- Das `--model`-Flag waehlt die Experten-Vorlage anhand der Modell-ID aus
  `/v1/models`.

---

## Aider

[Aider](https://github.com/paul-gauthier/aider) ist das aelteste
Terminal-KI-Pair-Programming-Tool (39K+ GitHub-Sterne). Es unterstuetzt
OpenAI-kompatible Backends ueber Kommandozeilenparameter oder Umgebungsvariablen.

### Schnellstart

```bash
aider --openai-api-base https://your-moe-instance.example.com/v1 \
      --openai-api-key moe-sk-xxxxxxxx... \
      --model openai/moe-reference-30b-balanced
```

### Umgebungsvariablen (Alternative)

```bash
export OPENAI_API_BASE=https://your-moe-instance.example.com/v1
export OPENAI_API_KEY=moe-sk-xxxxxxxx...

aider --model openai/moe-reference-30b-balanced
```

### Hinweise

- Das `openai/`-Praefix im Modellnamen weist Aider an, den OpenAI-Provider
  zu verwenden. Dieses Praefix ist zwingend erforderlich.
- Aider unterstuetzt Tool Use fuer Datei-Bearbeitung und Git-Operationen.
- Streaming wird vollstaendig unterstuetzt.
- Fuer optimale Ergebnisse mit der MoE-Pipeline empfiehlt sich eine
  `30b-balanced`-Vorlage oder groesser -- Aiders Edit-Format profitiert von
  praezisem Instruction Following.

---

## Continue.dev / Cursor

Diese IDE-integrierten Agenten werden auf der separaten Seite
[Continue / Cursor Integration](../continue-dev.md) behandelt.

Beide nutzen den OpenAI-kompatiblen `/v1/chat/completions`-Endpunkt und
unterstuetzen Modellauswahl, Streaming und Tool Use.

---

## Verfuegbare Modelle abfragen

Alle Agenten koennen die verfuegbaren Modelle (Experten-Vorlagen) bei
MoE Sovereign abfragen:

```bash
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer moe-sk-xxxxxxxx..." | jq '.data[].id'
```

Jede in der Admin-UI konfigurierte Experten-Vorlage erscheint als Modell in
der `/v1/models`-Antwort. Die angezeigte Modell-ID entspricht dem gewuenschten
Qualitaets-/Geschwindigkeits-Kompromiss.

---

## Kompatibilitaetsmatrix

| Feature | Claude Code | OpenCode | Claw Code | Codex CLI | Aider |
|---------|:-----------:|:--------:|:---------:|:---------:|:-----:|
| `/v1/messages` (Anthropic) | Ja | Nein | Nein | Nein | Nein |
| `/v1/chat/completions` (OpenAI) | Nein | Ja | Ja | Ja | Ja |
| Tool Use | Ja | Eingeschraenkt | Ja | Ja | Ja |
| Streaming | Ja | Ja | Ja | Ja | Ja |
| MoE-Pipeline | Ja | Ja | Ja | Ja | Ja |
| CC-Profil-Auswahl | Ja (per Key) | Nein | Nein | Nein | Nein |
| Experten-Vorlagen-Routing | Ja | Ja | Ja | Ja | Ja |
| Denkbloecke | Ja | Nein | Nein | Nein | Nein |
| Bildeingaben | Ja | Nein | Nein | Nein | Nein |

!!! note "Der Endpunkt bestimmt die Features"
    Agenten ueber den Anthropic-Endpunkt `/v1/messages` (Claude Code) erhalten
    Zugriff auf Denkbloecke und das vollstaendige CC-Profil-System. Agenten
    ueber den OpenAI-Endpunkt `/v1/chat/completions` waehlen ihre
    Experten-Vorlage stattdessen ueber den Modellnamen.

---

## Fehlerbehebung

### "Invalid API Key"

```bash
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

Falls dies einen Fehler liefert, den Key im User Portal unter **API Keys**
pruefen.

### Verbindung abgelehnt

```bash
curl https://your-moe-instance.example.com/health
```

Falls dies fehlschlaegt, ist die MoE-Sovereign-Instanz nicht erreichbar.
DNS, Firewall-Regeln und den Service-Status pruefen.

### Modell nicht gefunden

```bash
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer moe-sk-xxxxxxxx..." | jq '.data[].id'
```

Eine der zurueckgegebenen Modell-IDs in der Agenten-Konfiguration verwenden.

### Langsame Antworten

- Auf ein `native`-CC-Profil oder eine schnelle Experten-Vorlage (z.B. `8b-fast`)
  fuer interaktives Coding wechseln.
- Den `moe_orchestrated`-Modus nur fuer tiefgehende Recherchen oder komplexe
  Analysen verwenden, bei denen 2-10 Minuten Latenz akzeptabel sind.
- Siehe [Experten-Vorlagen & Profile](templating-guide.md) fuer Hinweise
  zur Wahl des richtigen Qualitaets-/Geschwindigkeits-Kompromisses.

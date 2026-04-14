# FAQ — MoE Sovereign Plattform

## Inhaltsverzeichnis

1. [Claude Code & .bashrc-Konfiguration](#1-claude-code--bashrc-konfiguration)
2. [API-Keys — Erstellen & Verwenden](#2-api-keys--erstellen--verwenden)
3. [Authentifizierung](#3-authentifizierung)
4. [Anfrage-Modi (Models)](#4-anfrage-modi-models)
5. [Token-Budget & Kosten](#5-token-budget--kosten)
6. [Expert-Templates](#6-expert-templates)
7. [Feedback geben](#7-feedback-geben)
8. [Admin-UI — Wichtige Funktionen](#8-admin-ui--wichtige-funktionen)
9. [MoE Portal & Web-Oberfläche](#9-moe-portal--web-oberfläche)
10. [Best Practices](#10-best-practices)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Claude Code & .bashrc-Konfiguration

### Wie muss meine `.bashrc` aussehen um die CC-Profile anzuzeigen?

Claude Code liest zwei Umgebungsvariablen um sich mit der MoE API zu verbinden:

```bash
# ~/.bashrc oder ~/.zshrc

# MoE API als Anthropic-Backend verwenden
export ANTHROPIC_BASE_URL=http://localhost:8002          # lokal
# export ANTHROPIC_BASE_URL=https://api.moe-sovereign.org  # extern

# Dein persönlicher API-Key (aus Admin-UI → Users → API Keys)
export ANTHROPIC_API_KEY=moe-sk-<YOUR_KEY_HERE>
```

Danach `source ~/.bashrc` ausführen oder Terminal neu starten.

### Wie werden CC-Profile (Claude Code Profiles) angezeigt und gewechselt?

Profile werden im **Admin-UI** unter **Users → CC-Profiles** verwaltet.
Jedes Profil definiert:

| Feld | Bedeutung | Beispielwert |
|------|-----------|--------------|
| `tool_model` | Lokales Modell für Tool-Ausführung | `gemma4:31b` |
| `tool_endpoint` | GPU-Knoten | `N04-RTX` |
| `moe_mode` | Orchestrierungsmodus | `native` / `moe_orchestrated` / `moe_reasoning` |
| `tool_max_tokens` | Max. Output-Tokens pro Tool-Call | `8192` |
| `reasoning_max_tokens` | Max. Tokens für Reasoning | `16384` |
| `system_prompt_prefix` | Zusätzliche Systeminstruktionen | _(optional)_ |
| `stream_think` | Thinking-Tokens im Stream ausgeben | `true` / `false` |

**Profil wechseln**: Admin-UI → Users → CC-Profiles → gewünschtes Profil auf „Aktiv" setzen.

### Was ist der Unterschied zwischen den `moe_mode`-Werten?

| Modus | Beschreibung | Latenz |
|-------|-------------|--------|
| `native` | Claude Code → MoE API → direkt an GPU (kein MoE-Fanout) | niedrig |
| `moe_orchestrated` | Anfrage wird vollständig durch MoE-Pipeline geroutet | mittel–hoch |
| `moe_reasoning` | MoE-Pipeline mit aktiviertem Thinking-Node | hoch |

**Empfehlung**: `native` für tägliche Coding-Aufgaben, `moe_orchestrated` für komplexe Analysen.

### Welche Claude-Modell-IDs sind für Claude Code verfügbar?

```bash
# Konfiguriert via CLAUDE_CODE_MODELS in .env:
claude-opus-4-6
claude-sonnet-4-6
claude-haiku-4-5-20251001
claude-opus-4-5
claude-sonnet-4-5
claude-haiku-4-5
```

---

## 2. API-Keys — Erstellen & Verwenden

### Wie erstelle ich einen API-Key?

**Admin-UI** → Users → gewünschten User auswählen → **API Keys** → **„Neuen Key erstellen"**

Der Raw-Key (`moe-sk-...`) wird **nur einmal** angezeigt — sofort kopieren!

### Welches Format haben API-Keys?

```
moe-sk-<48 zufällige Hex-Zeichen>
```

Beispiel: `moe-sk-<48_hex_chars_replace_me_with_real_key>`

### Wie verwende ich den API-Key in curl?

```bash
# Variante 1: Authorization-Header (empfohlen)
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "Authorization: Bearer moe-sk-XXXXXXXX..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moe-orchestrator",
    "messages": [{"role": "user", "content": "Hallo!"}]
  }'

# Variante 2: x-api-key-Header
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "x-api-key: moe-sk-XXXXXXXX..." \
  -H "Content-Type: application/json" \
  -d '{"model": "moe-orchestrator", "messages": [{"role": "user", "content": "Test"}]}'
```

### Wie widerrufe ich einen Key?

Admin-UI → Users → API Keys → **„Key löschen"** (sofort wirksam, auch wenn gecacht).

---

## 3. Authentifizierung

### Welche Authentifizierungsmethoden gibt es?

| Methode | Wann verwenden | Header |
|---------|---------------|--------|
| **API-Key** | Standard, direkte API-Nutzung | `Authorization: Bearer moe-sk-...` |
| **OIDC/JWT** | SSO via Authentik (Browser-Flow) | `Authorization: Bearer <JWT>` |

### Wie funktioniert der OIDC-Login (Browser)?

1. Browser öffnet **https://admin.moe-sovereign.org** (oder localhost:8088)
2. Klick auf **„Login mit SSO"** → Redirect zu Authentik
3. Authentik-Login → Redirect zurück mit JWT
4. JWT wird als Bearer-Token für folgende API-Calls verwendet

### Was ist der Unterschied zwischen OIDC und API-Key?

- **OIDC**: Kurzlebig (1h), für Browser/interaktive Nutzung
- **API-Key**: Langlebig, für Skripte/Automatisierung/Claude Code

---

## 4. Anfrage-Modi (Models)

### Welche Modell-IDs kann ich verwenden?

| Model-ID | Modus | Verwendungszweck |
|----------|-------|-----------------|
| `moe-orchestrator` | `default` | Vollständige Antworten mit Kontext — Standardwahl |
| `moe-orchestrator-code` | `code` | Nur Quellcode, kein Prosa-Text |
| `moe-orchestrator-concise` | `concise` | Kurz & präzise (max. ~120 Wörter) |
| `moe-orchestrator-agent` | `agent` | Coding-Agent (OpenCode, Continue.dev) |
| `moe-orchestrator-agent-orchestrated` | `agent_orchestrated` | Claude Code — voller MoE-Fanout |
| `moe-orchestrator-research` | `research` | Tiefenrecherche mit mehreren Web-Suchen |
| `moe-orchestrator-report` | `report` | Professioneller Markdown-Bericht |
| `moe-orchestrator-plan` | `plan` | Strukturierte Planung komplexer Aufgaben |

### Wann welchen Modus verwenden?

```
Allgemeine Fragen       → moe-orchestrator
Codeprobleme            → moe-orchestrator-code
Schnelle Antworten      → moe-orchestrator-concise
Tiefenrecherche         → moe-orchestrator-research
Komplexe Planung        → moe-orchestrator-plan
Claude Code (täglich)   → moe-orchestrator-agent
Claude Code (komplex)   → moe-orchestrator-agent-orchestrated
```

### Wie aktiviere ich Streaming?

```bash
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "Authorization: Bearer moe-sk-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moe-orchestrator",
    "stream": true,
    "messages": [{"role": "user", "content": "Erkläre Docker"}]
  }'
```

---

## 5. Token-Budget & Kosten

### Wie werden Token-Budgets gesetzt?

Admin-UI → Users → **Budget** → drei Limits konfigurierbar:

| Limit | Beschreibung | Beispiel |
|-------|-------------|---------|
| `daily_limit` | Max. Tokens/Tag (reset 00:00 UTC) | `100 000` |
| `monthly_limit` | Max. Tokens/Monat | `3 000 000` |
| `total_limit` | Lifetime-Limit | `50 000 000` |

`NULL` = unbegrenzt (Standard für Admin-Accounts).

### Wie sehe ich meinen aktuellen Verbrauch?

```bash
# Via Admin-API
curl -H "Authorization: Bearer moe-sk-..." \
  http://localhost:8088/api/users/{user_id}/usage
```

Oder: Admin-UI → Users → **Usage** Tab.

### Wie teuer ist ein Request?

`TOKEN_PRICE_EUR = 0.00002 €` pro Token (nur für Anzeige/Reporting, keine echte Abrechnung).

### Was passiert wenn das Budget überschritten wird?

Die API antwortet mit:

```json
{"error": {"type": "budget_exceeded", "message": "Daily token limit exceeded"}}
```

HTTP-Status: `429 Too Many Requests`

---

## 6. Expert-Templates

### Was ist ein Expert-Template?

Ein vordefiniertes Konfigurationspaket das für einen User:

- Spezifische Modelle pro Experten-Kategorie vorschreibt
- Custom System-Prompts für einzelne Experten setzt
- Judge- und Planner-Modelle überschreibt
- Einen eigenen `cost_factor` (Token-Gewichtung) trägt

### Wie erstelle ich ein Expert-Template?

Admin-UI → **Expert Templates** → **„Neues Template"**

Minimale JSON-Struktur:

```json
{
  "name": "Mein Template",
  "description": "Optimiert für Python-Entwicklung",
  "experts": {
    "code_reviewer": {
      "system_prompt": "Senior Python-Entwickler mit Fokus auf PEP 8 und Type Hints.",
      "models": [
        {"model": "devstral:24b", "endpoint": "N04-RTX", "required": true}
      ]
    }
  }
}
```

### Wie weise ich einem User ein Template zu?

Admin-UI → Users → gewünschten User → **Permissions** → Template aus Dropdown auswählen → Speichern.

Alternativ per API:

```bash
curl -X POST http://localhost:8088/api/users/{user_id}/permissions \
  -H "Authorization: Bearer moe-sk-..." \
  -H "Content-Type: application/json" \
  -d '{"resource_type": "expert_template", "resource_id": "tmpl-xxxx"}'
```

### Werden Template-Änderungen sofort aktiv?

Ja — Templates werden **alle 60 Sekunden** aus `.env` neu geladen, ohne Container-Neustart.
Sofortige Aktivierung: `docker compose restart langgraph-orchestrator`

---

## 7. Feedback geben

### Wie gebe ich Feedback zu einer Antwort?

```bash
curl -X POST http://localhost:8002/v1/feedback \
  -H "Authorization: Bearer moe-sk-..." \
  -H "Content-Type: application/json" \
  -d '{
    "response_id": "chatcmpl-abcd1234",
    "rating": 5,
    "correction": "Optionale Korrektur der Antwort..."
  }'
```

### Was bedeuten die Bewertungen?

| Rating | Bedeutung | Effekt |
|--------|-----------|--------|
| 1–2 | Negativ | Expert-Modell verliert Score; Few-Shot-Fehler werden gespeichert |
| 3 | Neutral | Kein Effekt auf Scoring |
| 4–5 | Positiv | Expert-Modell gewinnt Score; Planner-Muster werden gespeichert |

### Wo finde ich die `response_id`?

In der API-Antwort im Feld `id`:

```json
{"id": "chatcmpl-a1b2c3d4", "object": "chat.completion", ...}
```

---

## 8. Admin-UI — Wichtige Funktionen

### Wie erreiche ich das Admin-UI?

- **Lokal**: http://localhost:8088
- **Extern**: https://admin.moe-sovereign.org (via Authentik SSO)

### Welche Hauptfunktionen gibt es?

| Bereich | Funktion |
|---------|---------|
| **Users** | Benutzer anlegen, API-Keys, Budgets, CC-Profile, Templates zuweisen |
| **Expert Templates** | Templates erstellen, bearbeiten, löschen |
| **Models** | Experten-Modelle konfigurieren, VRAM-Status einsehen |
| **Live Logs** | Echtzeit-Pipeline-Logs via WebSocket |
| **System Health** | Docker-Container-Status, GPU-Auslastung |
| **Metrics** | Token-Verbrauch, Feedback-Statistiken, Cache-Hit-Rate |

### Wie erstelle ich einen neuen User?

Admin-UI → Users → **„Neuen User anlegen"** → Username, E-Mail, Passwort eingeben → Speichern.
Danach: API-Key erstellen + Budget setzen + ggf. Template zuweisen.

---

## 9. MoE Portal & Web-Oberfläche

### Welche Web-Oberflächen gibt es?

| Portal | URL | Zugang |
|--------|-----|--------|
| **MoE Web** (öffentlich) | https://moe-sovereign.org | Kein Login |
| **Admin-UI** | https://admin.moe-sovereign.org | Authentik SSO |
| **API-Endpunkt** | https://api.moe-sovereign.org | API-Key / OIDC |
| **Dokumentation** | http://localhost:8010 | Intern |
| **Grafana** | http://localhost:3001 | Monitoring |
| **Neo4j Browser** | http://localhost:7474 | Graph-Inspektion |

### Wie verbinde ich Open WebUI mit MoE?

In Open WebUI → Einstellungen → **Connections** → OpenAI API:

```
Base URL: http://localhost:8002/v1
API Key:  moe-sk-xxxxxxxx...
```

Dann erscheinen alle `moe-orchestrator-*`-Modelle in der Modell-Auswahl.

### Wie verbinde ich Continue.dev / Cursor mit MoE?

`~/.continue/config.json`:

```json
{
  "models": [{
    "title": "MoE Agent",
    "provider": "openai",
    "model": "moe-orchestrator-agent",
    "apiBase": "http://localhost:8002/v1",
    "apiKey": "moe-sk-xxxxxxxx..."
  }]
}
```

---

## 10. Best Practices

### Welchen Modus soll ich standardmäßig verwenden?

- **Interaktive Chats**: `moe-orchestrator` (default)
- **Code-Reviews**: `moe-orchestrator-code`
- **Lange Analysen**: `moe-orchestrator-research`
- **Claude Code täglich**: `.bashrc` mit `moe-orchestrator-agent-orchestrated`

### Wie optimiere ich die Latenz?

1. **Triviale Fragen** werden automatisch als `trivial` klassifiziert → Tier-1-Modell, kein Research
2. Nutze `moe-orchestrator-concise` wenn kurze Antworten ausreichen
3. Research/Thinking-Node wird für `trivial`/`moderate` Anfragen automatisch übersprungen

### Wie nutze ich den Self-Correction-Loop?

Gib nach jeder falschen Antwort negatives Feedback (Rating 1–2) — das System lernt automatisch:

- Fehlerhafte Modelle verlieren Score → werden seltener ausgewählt
- Numerische Fehler werden als Few-Shot-Beispiele gespeichert → Planner vermeidet sie künftig

### Wie sichere ich sensible Daten?

- API-Keys **niemals** in Skripte einchecken → `.env` oder Vault nutzen
- `.bashrc`-Einträge nur für lokale Entwicklung, nicht auf Produktionsservern
- Budget-Limits für alle nicht-Admin-User setzen

### Wann sollte ich Expert-Templates verwenden?

- **Spezialisierte Teams**: z.B. nur `code_reviewer` + `technical_support` für DevOps
- **Datenschutz-kritische Nutzung**: `medical_consult` auf dediziertem GPU ohne Logging
- **Performance-Tuning**: Tier-1-only-Template für schnelle, einfache Antworten

---

## 11. Troubleshooting

### Die API antwortet mit 401 Unauthorized

- API-Key korrekt? Format: `moe-sk-` + 48 Hex-Zeichen
- Key aktiv? Admin-UI → Users → API Keys → Status prüfen
- `AUTHENTIK_URL` gesetzt aber Authentik nicht erreichbar? → `.env` prüfen

### Die API antwortet mit 429 Too Many Requests

- Token-Budget überschritten → Admin-UI → Users → Budget erhöhen oder Reset abwarten
- Zu viele parallele Requests → kurz warten, MoE hat GPU-Semaphoren

### Antwort ist leer oder sehr kurz

- VRAM erschöpft? → `docker logs langgraph-orchestrator | grep VRAM`
- Judge-LLM Timeout? → `JUDGE_TIMEOUT` in `.env` erhöhen (Standard: 900s)
- Modell noch nicht geladen? → erstes Request nach Kaltstart dauert länger

### CC-Profile erscheinen nicht in Claude Code

1. `ANTHROPIC_BASE_URL` und `ANTHROPIC_API_KEY` in `.bashrc` gesetzt?
2. `source ~/.bashrc` ausgeführt?
3. Claude Code neu starten
4. API erreichbar? `curl -s http://localhost:8002/v1/models -H "Authorization: Bearer moe-sk-..." | jq .`

### Self-Correction schlägt fehl / Few-Shot-Daten fehlen

- Redis läuft? `docker ps | grep terra_cache`
- Key-Prefix prüfen: `docker exec terra_cache redis-cli keys "moe:few_shot:*"`
- Verzeichnis vorhanden? `ls /opt/moe-infra/few_shot_examples/`

---

*Letzte Aktualisierung: 2026-04-04 — Version 1.0*

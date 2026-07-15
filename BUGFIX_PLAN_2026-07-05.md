# BugFix-Plan moe-infra — 2026-07-05

Umsetzungsplan für die Befunde aus der Codebase-Analyse vom 2026-07-05.
Zielgruppe: ausführendes LLM. Jede Aufgabe ist in sich abgeschlossen und
enthält Fehlerbeschreibung, exakte Edits und Verifikation.

---

## Allgemeine Regeln (VOR Beginn lesen)

1. **Arbeitsverzeichnis:** `/opt/deployment/moe-sovereign/moe-infra`
2. **NIEMALS** Dateien unter `.claude/worktrees/` ändern — das sind isolierte Kopien.
3. Die Code-Blöcke „ALT" müssen **exakt** (inkl. Einrückung) im Ziel gefunden werden.
   Wenn ein ALT-Block nicht gefunden wird: STOPPEN und melden, nicht raten.
4. Nach jeder Änderung an einer `.py`-Datei sofort prüfen:
   `python3 -m py_compile <datei>` — bei Fehler die Änderung korrigieren, bevor
   die nächste Aufgabe begonnen wird.
5. Kein Umformatieren von umgebendem Code, keine zusätzlichen Kommentare,
   keine „Verbesserungen" außerhalb der beschriebenen Edits.
6. Die Aufgaben in der angegebenen Reihenfolge abarbeiten (Priorität absteigend).
7. Container-Neustarts erst ganz am Ende (Abschnitt „Rollout").

---

## AUFGABE 1 — Token-Bug: User-Connection-API-Key wird ignoriert

**Priorität:** P1 (funktionaler Bug, führt zu 401-Fehlern)
**Datei:** `services/routing.py`

### Fehlerbeschreibung
In `_resolve_user_experts()` wird für Template-Experten, deren Endpoint eine
private User-Connection ist, zwar die **URL** aus `user_connections_json`
aufgelöst, aber der **Token** weiterhin aus `TOKEN_MAP` genommen. `TOKEN_MAP`
kennt private Connections nicht → Fallback `"ollama"`. Ein Experte auf einem
privaten OpenAI-kompatiblen Endpoint mit API-Key sendet dann `Bearer ollama`
und bekommt 401. Zusätzlich: Der Legacy-Format-Zweig versucht gar keine
User-Connection-Auflösung, und `user_connections_json` wird pro
Modell-Schleifendurchlauf erneut geparst (unnötig).

### Edit 1.1 — user_connections einmalig parsen

ALT:
```python
    try:
        perms     = json.loads(permissions_json or "{}")
        tmpl_ids  = perms.get("expert_template", [])
        templates = _read_expert_templates()
        user_templates: dict = json.loads(user_templates_json or "{}")
```

NEU:
```python
    try:
        perms     = json.loads(permissions_json or "{}")
        tmpl_ids  = perms.get("expert_template", [])
        templates = _read_expert_templates()
        user_templates: dict = json.loads(user_templates_json or "{}")
        user_conns: dict = json.loads(user_connections_json or "{}")
```

**Achtung:** Dieser ALT-Block existiert **zweimal** in der Datei (auch in
`_resolve_template_prompts`). Nur das **erste** Vorkommen ändern (in
`_resolve_user_experts`, ca. Zeile 31–35).

### Edit 1.2 — Token aus User-Connection übernehmen (models-Format)

ALT:
```python
                    ep   = (m.get("endpoint") or "").strip()
                    url  = URL_MAP.get(ep) if ep else None
                    if not url:
                        _uc = json.loads(user_connections_json or "{}")
                        if ep in _uc:
                            url = _uc[ep]["url"]
                    models_list.append({
                        "model":          m.get("model", ""),
                        "endpoint":       ep,
                        "url":            url,
                        "token":          TOKEN_MAP.get(ep, "ollama") if ep else "ollama",
```

NEU:
```python
                    ep    = (m.get("endpoint") or "").strip()
                    url   = URL_MAP.get(ep) if ep else None
                    token = TOKEN_MAP.get(ep, "ollama") if ep else "ollama"
                    if not url and ep in user_conns:
                        # Private user connection: URL AND api_key come from the
                        # connection — TOKEN_MAP does not know private endpoints.
                        url   = user_conns[ep]["url"]
                        token = user_conns[ep].get("api_key") or "ollama"
                    models_list.append({
                        "model":          m.get("model", ""),
                        "endpoint":       ep,
                        "url":            url,
                        "token":          token,
```

### Edit 1.3 — Legacy-Format-Zweig gleichziehen

ALT:
```python
            elif isinstance(cat_cfg, dict):
                # Legacy format: {model, endpoint}
                ep  = (cat_cfg.get("endpoint") or "").strip()
                url = URL_MAP.get(ep) if ep else None
                result[cat] = [{
                    "model":          cat_cfg.get("model", ""),
                    "endpoint":       ep,
                    "url":            url,
                    "token":          TOKEN_MAP.get(ep, "ollama") if ep else "ollama",
```

NEU:
```python
            elif isinstance(cat_cfg, dict):
                # Legacy format: {model, endpoint}
                ep    = (cat_cfg.get("endpoint") or "").strip()
                url   = URL_MAP.get(ep) if ep else None
                token = TOKEN_MAP.get(ep, "ollama") if ep else "ollama"
                if not url and ep in user_conns:
                    url   = user_conns[ep]["url"]
                    token = user_conns[ep].get("api_key") or "ollama"
                result[cat] = [{
                    "model":          cat_cfg.get("model", ""),
                    "endpoint":       ep,
                    "url":            url,
                    "token":          token,
```

### Verifikation
```bash
python3 -m py_compile services/routing.py
grep -c "json.loads(user_connections_json" services/routing.py
# Erwartung: 2 (einmal in _resolve_user_experts oben, einmal in _resolve_template_prompts._resolve_ep_url)
```

---

## AUFGABE 2 — System-Prompt-Leck: tool_agent-Prompt verschmutzt Reasoning-/MoE-Handler

**Priorität:** P1 (Logikfehler, falsches Modellverhalten)
**Dateien:** `services/pipeline/cc_session.py`, `services/pipeline/anthropic.py`

### Fehlerbeschreibung
Wenn das Tool-Modell aus einem Experten-Template stammt, wird dessen
`tool_agent`-System-Prompt („respond only with the tool call(s)…") in
`session.system_prefix` eingebettet. `system_prefix` wird aber von **allen
drei** Handlern konsumiert: `_anthropic_tool_handler` (korrekt),
`_anthropic_reasoning_handler` (falsch — Zeile ~2198) und als
`behavioral_directives` in der MoE-Pipeline (falsch — Zeile ~2426).
Folge: Reasoning-/MoE-Turns bekommen Function-Calling-Anweisungen injiziert.
Fix: separates Feld `tool_system_prefix`, das **nur** der Tool-Handler nutzt.

### Edit 2.1 — Neues Dataclass-Feld (cc_session.py)

ALT:
```python
    # ── Prompting ────────────────────────────────────────────────────────────
    system_prefix: str = ""
    stream_think: bool = False
```

NEU:
```python
    # ── Prompting ────────────────────────────────────────────────────────────
    system_prefix: str = ""
    # System prompt of the template's tool_agent expert. Consumed ONLY by
    # _anthropic_tool_handler — must never leak into reasoning/MoE turns.
    tool_system_prefix: str = ""
    stream_think: bool = False
```

### Edit 2.2 — Phase 6 entflechten (cc_session.py)

ALT:
```python
    _prof_prefix  = (profile.get("system_prompt_prefix") or "").strip() if profile else ""
    # Prepend tool_agent system prompt (if resolved from a template-backed tool model)
    # so the expert knows to handle tool_header content as native function calls.
    if _tool_agent_system_prompt and _prof_prefix:
        system_prefix = f"{_tool_agent_system_prompt}\n\n{_prof_prefix}"
    elif _tool_agent_system_prompt:
        system_prefix = _tool_agent_system_prompt
    else:
        system_prefix = _prof_prefix
```

NEU:
```python
    _prof_prefix  = (profile.get("system_prompt_prefix") or "").strip() if profile else ""
    system_prefix = _prof_prefix
    # tool_agent prompt is kept separate — only the tool handler prepends it.
    tool_system_prefix = _tool_agent_system_prompt
```

### Edit 2.3 — Feld im Konstruktor übergeben (cc_session.py)

ALT:
```python
        system_prefix=system_prefix,
        stream_think=stream_think,
```

NEU:
```python
        system_prefix=system_prefix,
        tool_system_prefix=tool_system_prefix,
        stream_think=stream_think,
```

### Edit 2.4 — Tool-Handler kombiniert beide Prefixe (anthropic.py)

Suche in `services/pipeline/anthropic.py` innerhalb der Funktion
`_anthropic_tool_handler` (ca. Zeile 654):

ALT:
```python
    _eff_sys_prefix = session.system_prefix
    if _eff_sys_prefix and system:
```

NEU:
```python
    _eff_sys_prefix = session.system_prefix
    if session.tool_system_prefix:
        _eff_sys_prefix = (
            f"{session.tool_system_prefix}\n\n{_eff_sys_prefix}"
            if _eff_sys_prefix else session.tool_system_prefix
        )
    if _eff_sys_prefix and system:
```

**Achtung:** Es gibt ähnliche `session.system_prefix`-Blöcke in
`_anthropic_reasoning_handler` (ca. Zeile 2198). Diese NICHT ändern —
genau dort soll der tool_agent-Prompt ja NICHT mehr ankommen.

### Verifikation
```bash
python3 -m py_compile services/pipeline/cc_session.py services/pipeline/anthropic.py
grep -c "tool_system_prefix" services/pipeline/cc_session.py   # Erwartung: >= 4
grep -c "tool_system_prefix" services/pipeline/anthropic.py    # Erwartung: 2
```

---

## AUFGABE 3 — Compose-Mount für admin_ui/database.py ergänzen

**Priorität:** P2 (Voraussetzung für Aufgabe 4 und 8)
**Datei:** `docker-compose.yml`

### Fehlerbeschreibung
Der Container `moe-admin` mountet `admin_ui/app.py` und
`admin_ui/templates/`, aber **nicht** `admin_ui/database.py`. Änderungen an
dieser Datei würden ohne Image-Rebuild nicht live gehen.

### Edit 3.1

ALT:
```yaml
      - ./admin_ui/app.py:/app/app.py:ro
      - ./admin_ui/templates:/app/templates:ro
```

NEU:
```yaml
      - ./admin_ui/app.py:/app/app.py:ro
      - ./admin_ui/database.py:/app/database.py:ro
      - ./admin_ui/templates:/app/templates:ro
```

### Verifikation
```bash
grep -A2 "admin_ui/app.py" docker-compose.yml | grep "database.py"
# Erwartung: eine Zeile mit ./admin_ui/database.py:/app/database.py:ro
```

---

## AUFGABE 4 — Stille except-Blöcke mit Logging versehen

**Priorität:** P2 (Diagnose-Blindstellen)
**Dateien:** `services/routing.py`, `admin_ui/database.py`

### Fehlerbeschreibung
Mehrere `except Exception:`-Blöcke verschlucken Fehler komplett. Ein
fehlerhaftes Template-JSON lässt die Experten-Auflösung lautlos verschwinden
(Fallback auf globale Experten ohne jeden Log-Eintrag).

### Edit 4.1 — routing.py, Ende von `_resolve_user_experts`

ALT:
```python
        return result or None
    except Exception:
        return None
```

NEU:
```python
        return result or None
    except Exception:
        logger.exception("_resolve_user_experts failed — falling back to global experts")
        return None
```

### Edit 4.2 — routing.py, Ende von `_resolve_template_prompts`

ALT (das Vorkommen direkt nach dem großen `return {...}`-Block mit
`"causal_intervention"` als letztem Key):
```python
            "causal_intervention":     tmpl.get("causal_intervention"),
        }
    except Exception:
        return empty
```

NEU:
```python
            "causal_intervention":     tmpl.get("causal_intervention"),
        }
    except Exception:
        logger.exception("_resolve_template_prompts failed — returning empty prompt config")
        return empty
```

### Edit 4.3 — admin_ui/database.py, `_get_user_cost_factor`

Suche die Funktion `_get_user_cost_factor` in `admin_ui/database.py`.
Sie endet mit:

ALT:
```python
        return max_factor
    except Exception:
        return 1.0
```

NEU:
```python
        return max_factor
    except Exception:
        logger.exception("_get_user_cost_factor failed — defaulting to 1.0")
        return 1.0
```

**Hinweis:** Falls in `admin_ui/database.py` kein `logger` auf Modulebene
existiert, prüfen mit `grep -n "^logger" admin_ui/database.py`. Es existiert
einer (wird u.a. in `sync_user_to_redis` benutzt) — nichts zusätzlich importieren.

### Verifikation
```bash
python3 -m py_compile services/routing.py admin_ui/database.py
grep -c "logger.exception" services/routing.py    # Erwartung: 2
```

---

## AUFGABE 5 — Redis-Ausfall-Degradation loggen

**Priorität:** P2 (stille Verhaltensänderung sichtbar machen)
**Datei:** `services/auth.py`

### Fehlerbeschreibung
Bei Redis-Ausfall liefert `_db_fallback_key_lookup()` einen Minimal-Kontext
mit `permissions_json: "{}"` — der User verliert stillschweigend alle
CC-Profile, Templates und Budget-Checks. Das ist als Verfügbarkeits-Fallback
gewollt, wird aber nirgends geloggt.

### Edit 5.1

ALT:
```python
        # Redis unavailable or sync failed — build a minimal auth dict from the
        # DB row so startup-window requests and cache-miss keys don't 401.
        return {
```

NEU:
```python
        # Redis unavailable or sync failed — build a minimal auth dict from the
        # DB row so startup-window requests and cache-miss keys don't 401.
        logger.warning(
            "auth: Valkey unavailable — serving minimal fallback context for user %s "
            "(permissions, CC profiles, templates and budget checks NOT applied this request)",
            user_id,
        )
        return {
```

### Verifikation
```bash
python3 -m py_compile services/auth.py
```

---

## AUFGABE 6 — permitted-models: deaktivierte eigene Templates ausblenden

**Priorität:** P2
**Datei:** `admin_ui/app.py`

### Fehlerbeschreibung
Im Endpoint `/user/api/permitted-models` werden die eigenen Templates des
Users ohne `is_active`-Filter geladen. Deaktivierte Templates erscheinen
weiterhin als `template:<id>` im CC-Tool-Modell-Dropdown.

### Edit 6.1

Suche in `admin_ui/app.py` innerhalb der Funktion
`user_api_permitted_models` (ca. Zeile 6417):

ALT:
```python
    _own_tmpls = await db.list_user_templates(user_id)
    _own_tmpl_ids = {t["id"] for t in _own_tmpls}
```

NEU:
```python
    _own_tmpls = [t for t in await db.list_user_templates(user_id) if t.get("is_active", True)]
    _own_tmpl_ids = {t["id"] for t in _own_tmpls}
```

**Achtung:** `db.list_user_templates(user_id)` kommt mehrfach in der Datei
vor. Nur das Vorkommen ändern, bei dem die Ergebniszeile mit
`_own_tmpls =` beginnt und die Folgezeile `_own_tmpl_ids` ist.

### Verifikation
```bash
python3 -m py_compile admin_ui/app.py
```

---

## AUFGABE 7 — Blockierenden Sync-DB-Call aus dem Event-Loop nehmen

**Priorität:** P2 (Latenz-Anomalie: Event-Loop blockiert bis zu 3 s)
**Datei:** `services/templates.py`

### Fehlerbeschreibung
`_read_expert_templates()` ruft bei abgelaufenem Cache (30 s TTL) synchron
`_load_templates_from_db_sync()` auf — ein blockierender
`psycopg.connect(connect_timeout=3)` im Request-Pfad des Async-Servers.
Fix: Stale-while-revalidate — der Cache wird sofort ausgeliefert, die
Aktualisierung läuft in einem Daemon-Thread. Nur der allererste Aufruf
(Kaltstart, Cache leer) lädt synchron.

### Edit 7.1 — Import ergänzen

ALT:
```python
import json
import os
import time
```

NEU:
```python
import json
import os
import threading
import time
```

### Edit 7.2 — Funktion ersetzen

ALT (kompletter Block inklusive Cache-Initialisierung):
```python
    now = time.monotonic()
    cache = _read_expert_templates._cache
    if now - cache["ts"] < 30 and cache["data"] is not None:
        return cache["data"]

    data = _load_templates_from_db_sync()
    if data is None:
        data = _load_templates_from_env_file()
    if data is None:
        data = json.loads(os.getenv("EXPERT_TEMPLATES", "[]"))

    cache["ts"] = now
    cache["data"] = data
    return data


_read_expert_templates._cache: dict = {"ts": 0.0, "data": None}
```

NEU:
```python
    now = time.monotonic()
    cache = _read_expert_templates._cache
    if cache["data"] is not None:
        # Stale-while-revalidate: serve the cached list immediately; the sync
        # psycopg connect must never block the event loop on the request path.
        if now - cache["ts"] >= 30 and not cache["refreshing"]:
            cache["refreshing"] = True
            threading.Thread(
                target=_refresh_expert_templates_cache, daemon=True
            ).start()
        return cache["data"]
    # Cold start (first call, cache empty): load synchronously exactly once.
    _refresh_expert_templates_cache()
    return cache["data"] or []


def _refresh_expert_templates_cache() -> None:
    """Load templates from DB → .env → env var and update the cache."""
    cache = _read_expert_templates._cache
    try:
        data = _load_templates_from_db_sync()
        if data is None:
            data = _load_templates_from_env_file()
        if data is None:
            data = json.loads(os.getenv("EXPERT_TEMPLATES", "[]"))
        cache["data"] = data
        cache["ts"] = time.monotonic()
    finally:
        cache["refreshing"] = False


_read_expert_templates._cache: dict = {"ts": 0.0, "data": None, "refreshing": False}
```

### Edit 7.3 — Gleiches Muster für `_read_cc_profiles` prüfen

`grep -n "_read_cc_profiles" services/templates.py` ausführen. Wenn die
Funktion dieselbe Struktur hat (Cache-Dict mit `ts`/`data`, synchroner
DB-Load bei TTL-Ablauf), das identische Stale-while-revalidate-Muster
anwenden: eigene `_refresh_cc_profiles_cache()`-Funktion, `refreshing`-Key
im Cache-Dict, Daemon-Thread. Wenn die Struktur abweicht: NICHT ändern,
im Abschlussbericht vermerken.

### Verifikation
```bash
python3 -m py_compile services/templates.py
python3 -c "
import sys; sys.path.insert(0, '.')
# Nur Struktur-Check, kein DB-Zugriff nötig:
import ast
tree = ast.parse(open('services/templates.py').read())
names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
assert '_refresh_expert_templates_cache' in names, names
print('OK')
"
```

---

## AUFGABE 8 — `_is_endpoint_error`: zu breites String-Matching eingrenzen

**Priorität:** P3
**Datei:** `services/routing.py`

### Fehlerbeschreibung
Die Fehlerklassifikation matcht die Substrings `"blob"` und
`"no such file or directory"` ohne Kontext. Ein Fehlertext, der zufällig
„blob" enthält (z. B. durchgereichter Tool-Output in einer Exception),
aktiviert fälschlich die Endpoint-Fallback-Kette.

### Edit 8.1

ALT:
```python
    s = str(exc).lower()
    return any(k in s for k in (
        "401", "unauthorized", "403", "forbidden",
        "429", "rate limit", "quota exceeded",
        "authentication", "x-api-key",
        "402", "insufficient", "payment required",
        # Model-loading failures from Ollama (corrupt/missing blob, GGUF read error)
        "unable to load model",
        "error loading model",
        "failed to load model",
        "no such file or directory",
        "blob",
    ))
```

NEU:
```python
    s = str(exc).lower()
    # Model-loading failures from Ollama (corrupt/missing blob, GGUF read error).
    # Matched narrowly: bare "blob"/"no such file or directory" also occur in
    # unrelated error texts (e.g. tool output echoed into an exception message).
    if ("blob" in s or "no such file or directory" in s) and (
        "model" in s or "blobs/sha256" in s or "gguf" in s or "ollama" in s
    ):
        return True
    return any(k in s for k in (
        "401", "unauthorized", "403", "forbidden",
        "429", "rate limit", "quota exceeded",
        "authentication", "x-api-key",
        "402", "insufficient", "payment required",
        "unable to load model",
        "error loading model",
        "failed to load model",
    ))
```

### Verifikation
```bash
python3 -m py_compile services/routing.py
```

---

## AUFGABE 9 — Tote Legacy-Duplikate ins Archiv verschieben

**Priorität:** P3 (kein Laufzeit-Bug, aber Hauptquelle künftiger Fehlarbeit)
**Dateien:** Repo-Root: `app.py`, `auth.py`, `chat.py`, `database.py`, `user_portal.html`

### Fehlerbeschreibung
Diese fünf Root-Dateien sind veraltete, divergierte Kopien der aktiven
Module (`admin_ui/app.py`, `services/auth.py`, `services/pipeline/chat.py`,
`admin_ui/database.py`, `admin_ui/templates/user_portal.html`). Sie werden
von **keinem** laufenden Dienst importiert oder gestartet:
- Der Orchestrator (`langgraph-app`) startet `main.py`, das nur aus
  `services/*` und `routes/*` importiert.
- `moe-admin` startet die eingebaute Kopie von `admin_ui/app.py`.
- Root `database.py` wird nur von Root `app.py` importiert (das nie läuft).
- Root `database.py::sync_user_to_redis` fehlen die Felder
  `local_only_routing` und `native_num_ctx` — wer diese Datei versehentlich
  pflegt oder deployed, erzeugt Redis-Hashes mit fehlenden Feldern.

**WICHTIG: Das Verzeichnis ist KEIN Git-Repository — es gibt kein Undo.
Deshalb verschieben, nicht löschen.**

### Schritt 9.1 — Verifikation VOR dem Verschieben (Pflicht)

Alle vier Befehle müssen **leer** zurückkommen (keine Treffer außerhalb von
`.claude/worktrees/` und den Root-Dateien selbst):

```bash
cd /opt/deployment/moe-sovereign/moe-infra
grep -rn "^import app$\|^from app import" --include="*.py" . | grep -v ".claude/worktrees\|__pycache__"
grep -rn "^import auth$\|^from auth import" --include="*.py" . | grep -v ".claude/worktrees\|__pycache__\|services/\|admin_ui/"
grep -rn "^import chat$\|^from chat import" --include="*.py" . | grep -v ".claude/worktrees\|__pycache__\|services/"
grep -rn "^import database\|^from database import" --include="*.py" . | grep -v ".claude/worktrees\|__pycache__\|admin_ui/" | grep -v "^./app.py"
```

Zusätzlich prüfen, dass kein Skript/Dockerfile die Dateien referenziert:
```bash
grep -rn "user_portal.html" Dockerfile docker/ scripts/ 2>/dev/null | grep -v admin_ui
```

**Wenn irgendein Befehl Treffer liefert: STOPPEN, Treffer melden, nichts verschieben.**

### Schritt 9.2 — Verschieben

```bash
mkdir -p legacy_root_modules
mv app.py auth.py chat.py database.py user_portal.html legacy_root_modules/
cat > legacy_root_modules/README.md << 'EOF'
# Legacy-Module (archiviert 2026-07-05)

Diese Dateien waren veraltete, divergierte Kopien der aktiven Module und
wurden von keinem Dienst importiert oder gestartet:

| Archivierte Datei  | Aktives Pendant                        |
|--------------------|----------------------------------------|
| app.py             | admin_ui/app.py                        |
| auth.py            | services/auth.py                       |
| chat.py            | services/pipeline/chat.py              |
| database.py        | admin_ui/database.py                   |
| user_portal.html   | admin_ui/templates/user_portal.html    |

Nicht wieder in den Root verschieben. Nach 3 Monaten ohne Rückfrage löschbar.
EOF
```

### Schritt 9.3 — Verifikation NACH dem Verschieben

```bash
# Orchestrator-Entrypoint muss weiterhin importierbar sein (Syntax-Level):
python3 -c "import ast; ast.parse(open('main.py').read()); print('main.py OK')"
ls app.py auth.py chat.py database.py user_portal.html 2>&1 | grep -c "Datei oder Verzeichnis nicht gefunden\|No such file"
# Erwartung: 5
```

---

## AUFGABE 10 (OPTIONAL — nur nach Rückfrage beim Betreiber)

**Rollen-Default in Templates:** `services/routing.py`, Zeile mit
`role = "always" if m.get("required", True) else "primary"`.
Fehlt bei einem Template-Modell sowohl `role` als auch `required`, wird es
als „always" (erzwungen) behandelt. Sicherer Default wäre „primary".
**ABER:** Das ist eine Verhaltensänderung für bestehende Templates.
NICHT umsetzen ohne explizite Freigabe. Vorher prüfen, ob betroffene
Templates existieren:

```bash
python3 - << 'PYEOF'
import psycopg, json, os, re
env = open('.env').read()
pw = re.search(r'MOE_USERDB_PASSWORD=(\S+)', env).group(1)
url = f'postgresql://moe_admin:{pw}@172.20.0.3:5432/moe_userdb'
with psycopg.connect(url) as conn, conn.cursor() as cur:
    for table in ('admin_expert_templates', 'user_expert_templates'):
        cur.execute(f"SELECT id, name, config_json FROM {table}")
        for tid, name, cfg_raw in cur.fetchall():
            cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else cfg_raw
            for cat, cc in (cfg.get('experts') or {}).items():
                for m in (cc.get('models') or [] if isinstance(cc, dict) else []):
                    if 'role' not in m and 'required' not in m:
                        print(f"BETROFFEN: {table} {tid} ({name}) → {cat} → {m.get('model')}")
print("Prüfung abgeschlossen.")
PYEOF
```
Ergebnis dem Betreiber melden; Änderung nur bei „0 betroffen" oder Freigabe.

---

## ROLLOUT (nach Abschluss aller Aufgaben)

```bash
cd /opt/deployment/moe-sovereign/moe-infra

# 1. Finale Syntax-Prüfung aller geänderten Dateien
python3 -m py_compile services/routing.py services/auth.py services/templates.py \
  services/pipeline/cc_session.py services/pipeline/anthropic.py admin_ui/app.py \
  admin_ui/database.py

# 2. Container mit geänderten Mounts/Code neu erstellen bzw. neu starten
docker compose up -d --no-build moe-admin     # übernimmt den neuen database.py-Mount
docker compose restart langgraph-app          # lädt services/* neu

# 3. Fehlerfreiheit prüfen (beide müssen 0 melden)
sleep 10
docker logs moe-admin --since=60s 2>&1 | grep -cE "ERROR|Traceback"
docker logs langgraph-orchestrator --since=60s 2>&1 | grep -cE "ERROR|Traceback"

# 4. Funktionstest permitted-models (Test-API-Key des Betreibers verwenden,
#    NIEMALS den SYSTEM_API_KEY):
#    curl -s -H "x-api-key: <TEST_KEY>" http://localhost:8088/user/api/permitted-models
#    Erwartung: JSON-Liste, enthält mindestens einen "template:"-Eintrag.
```

## Abschlussbericht (vom ausführenden Modell zu erstellen)

Für jede Aufgabe angeben: umgesetzt / übersprungen (mit Grund) / abgewandelt
(mit Begründung). Alle Verifikations-Ausgaben anhängen. Besonderheiten aus
Edit 7.3 (Struktur von `_read_cc_profiles`) und Aufgabe 10 (betroffene
Templates) explizit nennen.

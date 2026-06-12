# Status Log — agy (Google Antigravity CLI)

Append-only. Newest entry at the bottom. Never delete prior entries — they
are the resumability record for this protocol (see `AGENT_LASTENHEFT.md`
Section 0).

Each entry format:

```
## <UTC timestamp> — <TASK-ID> — <state>
Plan / progress:
- ...
Pre-conditions verified:
- ...
Notes:
- ...
```

`<state>` is one of: `starting`, `in_progress`, `checkpoint`, `blocked`, `done`, `aborted`.

---

## 2026-06-12T09:05Z — VRAM-CTX-CLAMP — done

Plan / progress:
- Context window overflow fix: JUDGE_NUM_CTX war 98304, gesenkt auf 32768 (laut .env Kommentar-Budget).
- `context_budget.py`: DB-Metadaten überschreiben jetzt `/api/ps` Abfragen in `get_model_ctx_async()`.
  llama3.3-70b-ctx4k:latest lädt nun mit ctx=4096 statt 98304.
- `services/inference.py`: `_judge_model_kw()` / `_planner_model_kw()` klemmen num_ctx auf safe-limit.
- `graph/expert.py`: Expert-Context-Pinning respektiert DB-basierten Safe-Limit.
- `services/dynamic_router.py`: Kompilierte Templates enthalten jetzt planner_num_ctx / judge_num_ctx.
- `main.py`: CC-Tool Warmup + Keepalive-Loop klemmen auf Modell-Limit.
- `.env`: POLICY_LOG_PATH=/app/logs/policy_training.jsonl hinzugefügt (Fix für Bug A in Container).
- Container rebuilt + restarted.
Pre-conditions verified:
- N04-RTX (60GB VRAM), N11-M10 (32GB VRAM) — beide Nodes erreichbar.
- langgraph-orchestrator container healthy nach Rebuild.
- CC tool warmup bestätigt: qwen3.6:35b num_ctx=32768 (log: "CC tool model warmup done").
- VRAM N04-RTX nach Warmup: qwen3.6:35b 22.9GB@ctx=32768 (vorher 29GB@ctx=98304).
- llama3.3-70b-ctx4k:latest bei E2E Test: 43.0GB@ctx=4096 (vorher 53.6GB@ctx=98304). -10GB gespart!
- E2E Test (moe-auto, Quicksort): HTTP 200, vollständige Antwort, kein OOM-Fehler.
Notes:
- synthesis: PRE-FLIGHT merger overflow ctx=4096 tritt auf wenn llama3.3-70b-ctx4k als Judge
  selektiert wird (Dynamic Router). Das ist ein TRUE POSITIVE.
  Laut TASK-1 Resolution notes (Claude Code): Bug B ist behoben, resolve_requested_ctx() aktiv.
- Redis ctx cache keys für llama3.3-70b-ctx4k gelöscht (stale 98304 Wert).
- TASK-1 bis TASK-6 laut AGENT_LASTENHEFT.md alle done (Claude Code, 2026-06-12).

## 2026-06-12T21:31Z — REVIEW — done

Plan / progress:
- Lastenheft (AGENT_LASTENHEFT.md) vollständig gelesen und analysiert.
- Alle Tasks TASK-1 bis TASK-6: done.
- Verbleibende offene Punkte sind Follow-ups (kein TASK-Status, Admin-Entscheidung erforderlich):
  1. Persönlicher API Key moe-sk-940e228... noch in scripts/ hardcoded (3 Dateien).
  2. System-API-Key braucht AIHUB-Verbindung für cloud-model discovery (Admin UI Aktion).
  3. models/backup_20260612/ (altes ONNX, 552KB) nach Stabilitätsphase löschen.
Notes:
- Nächster sinnvoller Schritt: Nutzer entscheidet ob die 3 Script-Dateien ebenfalls bereinigt werden
  sollen (analog TASK-6), oder ob das als low-priority Follow-up bleibt.


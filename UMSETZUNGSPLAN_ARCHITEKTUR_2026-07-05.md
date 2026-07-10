# Umsetzungsplan Architektur-Optimierung moe-infra — 2026-07-05

Programm-Plan zur Umsetzung aller Punkte aus der Architektur-Bewertung vom
2026-07-05. Zielgruppe: ausführendes LLM. 13 Arbeitspakete (WP0–WP12),
in Phasen sortiert. Jedes WP ist eigenständig abschließbar und verifizierbar.

---

## Globale Regeln (VOR Beginn lesen — gelten für ALLE Arbeitspakete)

1. **Arbeitsverzeichnis:** `/opt/deployment/moe-sovereign/moe-infra`
2. **NIEMALS** Dateien unter `.claude/worktrees/` oder `legacy_root_modules/` ändern.
3. ALT-Codeblöcke müssen **exakt** gefunden werden. Wenn nicht: **STOPPEN und
   melden**, nicht raten. Bei Grep-Ankern gilt: liefert der Anker 0 oder >1
   plausible Treffer → STOPPEN und melden.
4. Nach jeder `.py`-Änderung: `python3 -m py_compile <datei>`. Fehler sofort
   beheben, bevor das nächste WP beginnt.
5. **Feature-Flag-Prinzip:** Jedes neue Verhalten wird über eine
   Umgebungsvariable geschaltet, Default = AUS (bisheriges Verhalten).
   Flags werden in `.env` NICHT automatisch gesetzt — nur im Code mit
   `os.getenv(..., "0")` gelesen und im Abschlussbericht dokumentiert.
6. **Neue Module statt invasiver Edits:** Neue Logik kommt in neue Dateien
   unter `services/`. In Bestandsdateien nur minimale, geankerte Hook-Aufrufe.
7. **Neue DB-Tabellen** werden lazy im jeweiligen Modul per
   `CREATE TABLE IF NOT EXISTS` angelegt (DSN aus `config.MOE_USERDB_URL`).
   Keine Änderungen am SCHEMA-Block von `admin_ui/database.py`.
8. Container-Neustart (`docker compose restart langgraph-app`) nur am Ende
   jeder Phase, nicht nach jedem WP.
9. Für Tests **ausschließlich** den Test-API-Key aus dem Betreiber-Memory
   verwenden, niemals `SYSTEM_API_KEY`.
10. Pro WP einen kurzen Ergebnis-Eintrag ins Abschlussprotokoll schreiben:
    umgesetzt / übersprungen (Grund) / abgewandelt (Begründung) + Verifikations-Output.

## Phasen und Abhängigkeiten

| Phase | WPs | Abhängigkeit |
|---|---|---|
| 0 — Quick Wins | WP0, WP2 | keine |
| 1 — Token-Triage | WP1 | keine |
| 2 — Feedback-Fundament | WP3, WP4 | WP2 (Metadaten) |
| 3 — Routing-Intelligenz | WP5, WP10 | WP2 |
| 4 — Wissens-Hygiene | WP6 | keine |
| 5 — Selbstverbesserung | WP7, WP8 | WP3 (Qualitätsdaten) |
| 6 — Souveränität | WP9 | keine |
| 7 — Robustheit | WP11 | keine |
| 8 — Infrastruktur | WP12 | Betreiber-Freigabe |

---

# PHASE 0 — QUICK WINS

## WP0 — CC-Profil „RTX MoE Template" auf Template-Auto-Ableitung umstellen

**Problem:** Das Profil `ucp-7b76127c51704cd6977eeb7b06369ffc` setzt
`tool_model: gemma4:12b` nativ. Dadurch beantwortet ein schwaches 12B-Modell
alle Tool-Turns; der starke `tool_agent` (qwen3.6:35b) des zugewiesenen
Templates greift nur noch als Enforcement-Retry (kostet ~3 min pro Fehlversuch).
Seit 2026-07-04 leitet ein leeres `tool_model` das Tool-Modell automatisch aus
dem Template-`tool_agent` ab (cc_session.py Phase 5.5).

**Schritte:**
```bash
cd /opt/deployment/moe-sovereign/moe-infra
python3 - << 'PYEOF'
import psycopg, json, re
env = open('.env').read()
pw = re.search(r'MOE_USERDB_PASSWORD=(\S+)', env).group(1)
url = f'postgresql://moe_admin:{pw}@172.20.0.3:5432/moe_userdb'
with psycopg.connect(url, row_factory=psycopg.rows.dict_row) as conn, conn.cursor() as cur:
    cur.execute("SELECT config_json FROM user_cc_profiles WHERE id='ucp-7b76127c51704cd6977eeb7b06369ffc'")
    row = cur.fetchone()
    cfg = json.loads(row['config_json'])
    print("Vorher:", cfg.get('tool_model'), cfg.get('tool_endpoint'))
    cfg['tool_model'] = ""
    cfg['tool_endpoint'] = ""
    cur.execute(
        "UPDATE user_cc_profiles SET config_json=%s WHERE id='ucp-7b76127c51704cd6977eeb7b06369ffc'",
        (json.dumps(cfg, ensure_ascii=False),),
    )
    conn.commit()
    print("Nachher: tool_model leer -> Auto-Ableitung aus Template tool_agent")
PYEOF
# Redis-Cache von horndev invalidieren, damit die Änderung sofort greift:
python3 - << 'PYEOF'
import redis, re
env = open('.env').read()
rpw = re.search(r'REDIS_PASSWORD=(\S+)', env).group(1)
r = redis.Redis(host='172.20.0.2', port=6379, password=rpw, decode_responses=True)
n = 0
for k in r.keys("user:apikey:*"):
    if r.hget(k, 'user_id') == 'b1a128df87944e5f808a9fc9a10e064a':
        r.delete(k); n += 1
print(f"{n} Cache-Einträge invalidiert")
PYEOF
```

**Verifikation:** Erstes Skript druckt „Nachher: tool_model leer …", zweites
mindestens „1 Cache-Einträge invalidiert". Nach dem nächsten claude-dev-Request
muss im Orchestrator-Log `model=qwen3.6:35b` statt `model=gemma4:12b` in der
Zeile `cc_tool: Ollama native /api/chat` stehen.

---

## WP2 — Antwort-Transparenz: X-MoE-Header (welches Modell hat wirklich geantwortet?)

**Problem:** Der Client sieht nicht, welches Backend-Modell geantwortet hat,
ob ein Fallback oder der required-Retry griff. Stille Degradation ist
unauditierbar — für ein Souveränitätssystem inakzeptabel.

**Datei:** `services/pipeline/anthropic.py`

**Schritt 2.1 — Header-Helfer einfügen.** Suche per Grep den Anker
`def _sse_event` (eine Funktion nahe Dateianfang). Füge DIREKT DAVOR ein:

```python
def _moe_response_headers(model: str, node: str, retry_used: bool = False) -> dict:
    """Transparency headers: which backend actually answered this request.

    X-MoE-Backend-Model / X-MoE-Node identify the real model+node (not the
    claude-* alias); X-MoE-Required-Retry marks answers produced by the
    tool_choice=required enforcement retry. Silent degradation must be
    observable for sovereignty auditing.
    """
    return {
        "X-Accel-Buffering":    "no",
        "Cache-Control":        "no-cache",
        "X-MoE-Backend-Model":  str(model or "unknown"),
        "X-MoE-Node":           str(node or "unknown"),
        "X-MoE-Required-Retry": "1" if retry_used else "0",
    }
```

**Schritt 2.2 — Header an den StreamingResponse-Rückgaben verwenden.**
Suche ALLE Vorkommen von
`headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}`
in `services/pipeline/anthropic.py` (erwartet: mindestens 3). Ersetze in
JEDEM Vorkommen, das innerhalb von `_anthropic_tool_handler` liegt, den
headers-Ausdruck durch:
`headers=_moe_response_headers(effective_model, effective_node)`.
Bei Vorkommen in anderen Handlern (reasoning/moe): prüfe, welche
Modell-Variable dort im Scope ist (z. B. `session.tool_model`); wenn keine
eindeutige Variable existiert → dieses Vorkommen unverändert lassen und im
Protokoll vermerken.

**Verifikation:**
```bash
python3 -m py_compile services/pipeline/anthropic.py
grep -c "_moe_response_headers" services/pipeline/anthropic.py   # >= 3 (1 Def + >=2 Nutzungen)
# Nach Phase-0-Neustart:
curl -sI -X POST http://localhost:8002/v1/messages -H "x-api-key: <TEST_KEY>" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":50,"stream":true,"messages":[{"role":"user","content":"OK"}]}' \
  | grep -i "x-moe"
```

---

# PHASE 1 — TOKEN-TRIAGE (größter Einzelhebel)

## WP1 — Fast-Path für triviale und CC-Utility-Requests

**Problem:** Jeder Request ohne Tools durchläuft die volle MoE-Pipeline
(Planner → Experten → Judge). CC-interne Utility-Requests (Topic-Detection,
Titel-Generierung, Quota-Probe) liefen dadurch 5+ Minuten und banden GPUs.
Der Complexity-Estimator stufte einen trivialen Utility-Request als „complex" ein.

### Schritt 1.1 — Utility-/Trivial-Erkennung als neues Modul

Neue Datei `services/cc_fastpath.py` mit EXAKT diesem Inhalt:

```python
"""
services/cc_fastpath.py — Fast-path triage for the /v1/messages endpoint.

Detects requests that must NOT run through the full MoE pipeline
(planner → experts → judge): Claude Code's internal utility calls
(topic detection, title generation, quota probes) and trivially short
prompts. These are answered by a single direct LLM call instead.

Flag: CC_FASTPATH=1 enables the fast path (default: off).
      CC_FASTPATH_MAX_CHARS overrides the trivial-length threshold (default 600).
"""

import os
import re

_UTILITY_PATTERNS = re.compile(
    r"(analyze if this message indicates a new conversation topic"
    r"|write a 5-10 word title"
    r"|generate a concise title"
    r"|please write a .{0,20}title for the"
    r"|^quota$"
    r"|respond only with json"
    r"|isNewTopic)",
    re.IGNORECASE,
)


def _extract_text(body: dict) -> str:
    parts: list = []
    for m in body.get("messages", []):
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
    return "\n".join(parts)


def is_fastpath_request(body: dict) -> str:
    """Return the fast-path reason ('' = no fast path).

    'utility'  — CC-internal side request (topic/title/quota); ALWAYS eligible,
                 these must never occupy the MoE pipeline.
    'trivial'  — no tools and total prompt text below threshold.
    """
    if os.getenv("CC_FASTPATH", "0") != "1":
        return ""
    if body.get("tools"):
        return ""
    text = _extract_text(body)
    if _UTILITY_PATTERNS.search(text):
        return "utility"
    max_chars = int(os.getenv("CC_FASTPATH_MAX_CHARS", "600"))
    if max_chars > 0 and len(text.strip()) <= max_chars and len(body.get("messages", [])) <= 4:
        return "trivial"
    return ""
```

### Schritt 1.2 — Dispatch-Hook in anthropic.py

**Anker (exakt vorhanden):**
```python
    try:
        # Mode 1: Native or tool/tool_result turns → tool handler (precise function calling)
        if session.mode == "native" or tools or has_tool_results:
```

Ersetze durch:
```python
    # Fast-path triage: CC utility calls and trivially short prompts bypass the
    # MoE pipeline entirely — a 5-line topic-detection request must never run
    # planner+experts+judge for minutes (observed: 5 min pipeline for one
    # CC-internal side request). Flag: CC_FASTPATH=1.
    from services.cc_fastpath import is_fastpath_request as _is_fp
    _fp_reason = _is_fp(body)
    if _fp_reason:
        logger.info("cc_dispatch: fast-path (%s) — bypassing MoE pipeline", _fp_reason)

    try:
        # Mode 1: Native or tool/tool_result turns → tool handler (precise function calling)
        if session.mode == "native" or tools or has_tool_results or _fp_reason:
```

**Hinweis:** Der Tool-Handler funktioniert auch ohne Tools als direkter
LLM-Call — genau das ist der Fast-Path.

### Schritt 1.3 — Complexity-Estimator: Utility-Guard

**Datei:** `complexity_estimator.py`. **Anker (exakt vorhanden):**
```python
    words = query.split()
    n = len(words)
```
Füge DIREKT DANACH ein:
```python
    # CC-internal utility requests (topic detection, title generation) are
    # long English instructions and were misclassified as "complex" — they
    # must be trivial so downstream cost tiers stay minimal even when the
    # fast path is disabled.
    _q_low = query[:400].lower()
    if ("analyze if this message indicates a new conversation topic" in _q_low
            or "write a 5-10 word title" in _q_low
            or "generate a concise title" in _q_low):
        return "trivial"
```

**Verifikation:**
```bash
python3 -m py_compile services/cc_fastpath.py complexity_estimator.py services/pipeline/anthropic.py
python3 -c "
import sys; sys.path.insert(0,'.')
from complexity_estimator import estimate_complexity
r = estimate_complexity('Analyze if this message indicates a new conversation topic. Respond only with JSON: {\"isNewTopic\": true}')
assert r == 'trivial', r
print('Estimator-Guard OK')
"
# Funktionstest nach Neustart mit CC_FASTPATH=1 in .env (Betreiber setzt Flag):
# kleiner Request ohne Tools muss in Sekunden antworten und im Log
# 'cc_dispatch: fast-path' zeigen.
```

---

# PHASE 2 — FEEDBACK-FUNDAMENT

## WP3 — Online-Qualitätsmessung: Pipeline vs. bester Einzelexperte

**Problem:** Niemand misst, ob die MoE-Pipeline (3–5× Token-Kosten) besser
antwortet als der beste Einzelexperte. Ohne diese Messung ist Template-Tuning
Blindflug.

**Design:** Stichprobenartig (Default 5 %) wird nach Abschluss einer
`moe_orchestrated`-Antwort im Hintergrund derselbe Prompt an den stärksten
Einzelexperten geschickt; der Judge vergleicht beide anonymisiert; das
Ergebnis landet in der Tabelle `pipeline_quality_log`.
Flag: `MOE_QUALITY_PROBE=1`, Rate: `MOE_QUALITY_SAMPLE_RATE` (Default `0.05`).

### Schritt 3.1 — Neue Datei `services/quality_probe.py` mit EXAKT diesem Inhalt:

```python
"""
services/quality_probe.py — Online A/B: pipeline answer vs. single best expert.

Fire-and-forget probe, sampled (MOE_QUALITY_SAMPLE_RATE, default 5%). The same
user prompt is answered by the single strongest expert; a blind judge call
decides which answer is better. Results accumulate in pipeline_quality_log and
answer the core question: does the pipeline overhead buy quality?

Flag: MOE_QUALITY_PROBE=1 (default off).
"""

import asyncio
import json
import logging
import os
import random
import time
import uuid

import httpx

logger = logging.getLogger("MOE-SOVEREIGN")

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_quality_log (
    id            TEXT PRIMARY KEY,
    created_at    TIMESTAMPTZ DEFAULT now(),
    request_id    TEXT,
    user_id       TEXT,
    template_id   TEXT,
    query_preview TEXT,
    pipeline_answer_chars INT,
    baseline_model TEXT,
    baseline_answer_chars INT,
    winner        TEXT,          -- 'pipeline' | 'baseline' | 'tie' | 'error'
    judge_raw     TEXT,
    baseline_tokens INT,
    judge_tokens  INT
);
"""

_JUDGE_PROMPT = """You are a strict evaluator. Two answers (A and B) to the same user request follow.
Decide which answer is better on correctness, completeness and usefulness.
Respond ONLY with one word: A, B, or TIE.

## User request
{query}

## Answer A
{a}

## Answer B
{b}
"""


async def _db_insert(row: dict) -> None:
    from config import MOE_USERDB_URL
    try:
        import psycopg
        async with await psycopg.AsyncConnection.connect(MOE_USERDB_URL) as conn:
            async with conn.cursor() as cur:
                await cur.execute(_TABLE_SQL)
                cols = ",".join(row.keys())
                ph = ",".join(["%s"] * len(row))
                await cur.execute(
                    f"INSERT INTO pipeline_quality_log ({cols}) VALUES ({ph})",
                    list(row.values()),
                )
            await conn.commit()
    except Exception as e:
        logger.warning("quality_probe: DB insert failed: %s", e)


def _pick_baseline_expert(experts: dict) -> dict | None:
    """Strongest single expert: first entry of 'general', else 'reasoning', else any."""
    for cat in ("general", "reasoning", "code"):
        lst = (experts or {}).get(cat) or []
        for e in lst:
            if e.get("model") and e.get("url"):
                return e
    for lst in (experts or {}).values():
        if isinstance(lst, list):
            for e in lst:
                if e.get("model") and e.get("url"):
                    return e
    return None


async def _ollama_generate(url: str, token: str, model: str, prompt: str,
                           num_ctx: int = 32768, timeout: float = 300.0) -> tuple:
    base = url.rstrip("/").removesuffix("/v1")
    payload = {
        "model": model, "stream": False, "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"num_ctx": num_ctx, "num_predict": 4096},
        "keep_alive": "4h",
    }
    async with httpx.AsyncClient(timeout=timeout) as cl:
        r = await cl.post(f"{base}/api/chat", json=payload,
                          headers={"Authorization": f"Bearer {token}"})
        j = r.json()
    return (j.get("message", {}).get("content", "") or "", int(j.get("eval_count", 0)))


async def run_probe(query: str, pipeline_answer: str, experts: dict,
                    planner_cfg: dict, request_id: str, user_id: str,
                    template_id: str = "") -> None:
    """Fire-and-forget entry point. Call via asyncio.create_task()."""
    try:
        if os.getenv("MOE_QUALITY_PROBE", "0") != "1":
            return
        rate = float(os.getenv("MOE_QUALITY_SAMPLE_RATE", "0.05"))
        if random.random() > rate:
            return
        if not query or not pipeline_answer:
            return
        exp = _pick_baseline_expert(experts)
        if not exp:
            return
        t0 = time.monotonic()
        baseline, base_tok = await _ollama_generate(
            exp["url"], exp.get("token", "ollama"), exp["model"], query,
            num_ctx=int(exp.get("context_window") or 32768),
        )
        if not baseline.strip():
            return
        # Blind judging: randomize A/B assignment to avoid position bias.
        flip = random.random() < 0.5
        a, b = (pipeline_answer, baseline) if not flip else (baseline, pipeline_answer)
        judge_model = (planner_cfg or {}).get("judge_model_override") or exp["model"]
        judge_url   = (planner_cfg or {}).get("judge_url_override") or exp["url"]
        judge_tok   = (planner_cfg or {}).get("judge_token_override") or exp.get("token", "ollama")
        verdict_raw, judge_tokens = await _ollama_generate(
            judge_url, judge_tok, judge_model,
            _JUDGE_PROMPT.format(query=query[:4000], a=a[:6000], b=b[:6000]),
        )
        v = verdict_raw.strip().upper()[:3]
        if v.startswith("A"):
            winner = "pipeline" if not flip else "baseline"
        elif v.startswith("B"):
            winner = "baseline" if not flip else "pipeline"
        elif v.startswith("TIE"):
            winner = "tie"
        else:
            winner = "error"
        await _db_insert({
            "id": uuid.uuid4().hex, "request_id": request_id, "user_id": user_id,
            "template_id": template_id, "query_preview": query[:300],
            "pipeline_answer_chars": len(pipeline_answer),
            "baseline_model": f'{exp["model"]}@{exp.get("endpoint", "?")}',
            "baseline_answer_chars": len(baseline), "winner": winner,
            "judge_raw": verdict_raw[:200], "baseline_tokens": base_tok,
            "judge_tokens": judge_tokens,
        })
        logger.info(
            "quality_probe: winner=%s baseline=%s dur=%.0fs (request %s)",
            winner, exp["model"], time.monotonic() - t0, request_id,
        )
    except Exception as e:
        logger.warning("quality_probe failed (non-fatal): %s", e)
```

### Schritt 3.2 — Hook im MoE-Handler

**Anker-Suche:** `grep -n "moe_stream: pipeline task" services/pipeline/anthropic.py`
Der Treffer liegt in `_anthropic_moe_handler` bzw. dessen Stream-Generator.
Finde in derselben Funktion die Stelle, an der die **finale Pipeline-Antwort
als String vorliegt** (typisch: eine Variable, die für den Episodic-Write-Back
oder das letzte `content_block_delta` verwendet wird; Grep-Hilfen:
`final_response`, `_final_text`, `full_text`). Füge NACH Abschluss des Streams
(nach dem `message_stop`-Yield, vor dem Funktionsende) ein:

```python
            # Online quality probe (sampled): pipeline vs. single best expert.
            try:
                from services.quality_probe import run_probe as _qp_run
                asyncio.create_task(_qp_run(
                    query=<QUERY_VARIABLE>,
                    pipeline_answer=<FINAL_ANSWER_VARIABLE>,
                    experts=session.experts,
                    planner_cfg=session.planner_cfg,
                    request_id=chat_id, user_id=user_id,
                ))
            except Exception:
                pass
```

`<QUERY_VARIABLE>` = die Variable mit dem User-Prompt-Text,
`<FINAL_ANSWER_VARIABLE>` = die Variable mit der finalen Antwort.
**STOP-Regel:** Wenn keine der beiden Variablen eindeutig identifizierbar ist
→ Hook NICHT einbauen, WP als „übersprungen: Anker unklar" protokollieren.

**Verifikation:**
```bash
python3 -m py_compile services/quality_probe.py services/pipeline/anthropic.py
# Nach Phase-Neustart mit MOE_QUALITY_PROBE=1 und MOE_QUALITY_SAMPLE_RATE=1.0 (temporär):
# einen moe-Request senden, dann:
# SELECT winner, baseline_model FROM pipeline_quality_log ORDER BY created_at DESC LIMIT 3;
```

---

## WP4 — Judge-Gate: Judge nur bei Experten-Dissens

**Problem:** Der Judge bewertet häufig Outputs desselben Modells, das auch
Experte ist (Selbst-Judging) — Token-Kosten ohne Diskriminationskraft.
Bei nur einem Expertenergebnis oder nahezu identischen Ergebnissen ist der
Judge-Call verzichtbar.

### Schritt 4.1 — Neue Datei `services/judge_gate.py` mit EXAKT diesem Inhalt:

```python
"""
services/judge_gate.py — Skip the judge when it cannot add value.

The judge call is skipped when (a) only one expert produced a result, or
(b) all expert results are near-identical (SequenceMatcher ratio above
threshold). Self-judging (judge model == only expert model) adds tokens
without discriminative power.

Flag: MOE_JUDGE_GATE=1 (default off).
Threshold: MOE_JUDGE_GATE_SIM (default 0.85).
"""

import os
from difflib import SequenceMatcher


def should_skip_judge(expert_outputs: list) -> tuple:
    """(skip: bool, reason: str, chosen_index: int)

    expert_outputs: list of answer strings (order = expert order).
    chosen_index: which output to use when skipping (longest one).
    """
    if os.getenv("MOE_JUDGE_GATE", "0") != "1":
        return (False, "", -1)
    outs = [o for o in expert_outputs if isinstance(o, str) and o.strip()]
    if not outs:
        return (False, "", -1)
    if len(outs) == 1:
        return (True, "single_expert", expert_outputs.index(outs[0]))
    sim_threshold = float(os.getenv("MOE_JUDGE_GATE_SIM", "0.85"))
    base = outs[0]
    for other in outs[1:]:
        # Compare on the first 4000 chars — enough signal, bounded cost.
        if SequenceMatcher(None, base[:4000], other[:4000]).ratio() < sim_threshold:
            return (False, "", -1)
    longest = max(outs, key=len)
    return (True, "consensus", expert_outputs.index(longest))
```

### Schritt 4.2 — Integration im Merger (`graph/synthesis.py`, Funktion `merger_node`)

**Kontext:** Es existiert bereits ein Fast-Path für EINEN Experten mit
`confidence == "high"` (Zeile ~709). Das Judge-Gate ergänzt zwei Fälle:
einzelner Experte mit mittlerer Konfidenz und mehrere nahezu identische
Expertenantworten. Es darf NUR greifen, wenn keine Zusatzkontexte gemerged
werden müssen (web/mcp/math/graph/ensemble).

**Anker (exakt vorhanden, verifiziert):**
```python
    await _report(f"🔀 Merger prompt ({len(prompt)} chars):\n{prompt}")
    try:
        res = await _invoke_judge_with_retry(state_, prompt, temperature=state_.get("query_temperature"))
```

Ersetze durch:
```python
    # Judge gate: when all expert answers agree (or only one exists) and no
    # auxiliary context needs merging, the judge call adds tokens without
    # discriminative power — take the consensus answer directly.
    # Flag: MOE_JUDGE_GATE=1 (services/judge_gate.py).
    if not ensemble_results and not web and not mcp_res and not math_res and not graph_ctx:
        from services.judge_gate import should_skip_judge as _sj_gate
        _gate_inputs = [re.sub(r'^\[[^\]]+\]:\s*', '', r or '') for r in expert_results]
        _g_skip, _g_reason, _g_idx = _sj_gate(_gate_inputs)
        if _g_skip:
            _g_raw = _gate_inputs[_g_idx]
            _g_det = re.search(r'DETAILS:\n?(.*)', _g_raw, re.DOTALL)
            _g_resp = (_g_det.group(1).strip() if _g_det else _g_raw.strip())
            if _g_resp:
                logger.info("⚡ judge_gate: skipping merger judge (%s)", _g_reason)
                await _report(f"⚡ Judge-Gate: {_g_reason} — Merger-Judge übersprungen")
                return {"final_response": _g_resp, "prompt_tokens": 0, "completion_tokens": 0}

    await _report(f"🔀 Merger prompt ({len(prompt)} chars):\n{prompt}")
    try:
        res = await _invoke_judge_with_retry(state_, prompt, temperature=state_.get("query_temperature"))
```

**Hinweise:** Die Variablen `ensemble_results`, `web`, `mcp_res`, `math_res`,
`graph_ctx`, `expert_results` sind an dieser Stelle im Scope (siehe bestehende
Fast-Path-Bedingung ~Zeile 709–713); `re` ist importiert.
**STOP-Regel:** Wenn der ALT-Block nicht exakt gefunden wird oder eine der
sechs Variablen an der Stelle nicht existiert (mit Grep prüfen!) → NICHT
integrieren, als „übersprungen" protokollieren. Das Modul `judge_gate.py`
trotzdem anlegen (Schritt 4.1 zählt als Teilerfolg).

**Verifikation:**
```bash
python3 -m py_compile services/judge_gate.py graph/expert.py
python3 -c "
import sys, os; sys.path.insert(0,'.')
os.environ['MOE_JUDGE_GATE']='1'
from services.judge_gate import should_skip_judge
assert should_skip_judge(['antwort a'])[0] is True
assert should_skip_judge(['gleicher text '*50, 'gleicher text '*50])[0] is True
assert should_skip_judge(['text über hunde '*50, 'völlig anderes thema quantenphysik '*50])[0] is False
print('judge_gate OK')
"
```

---

# PHASE 3 — ROUTING-INTELLIGENZ

## WP5 — Experten-Routing innerhalb von Tool-Turns (experimentell)

**Problem:** ~95 % des CC-Traffics sind Tool-Turns → ein statisches
`tool_agent`-Modell macht fast alles; das Experten-Ensemble liegt brach.

**Design (V1, bewusst konservativ):** Nur **Synthesis-Turns** werden
umgeroutet: Wenn der Turn `tool_result`-Blöcke enthält (Modell soll
Ergebnisse zusammenfassen, kein neuer Tool-Call zwingend) UND das Template
einen `code`- oder `general`-Experten mit größerem Modell definiert, übernimmt
dieser die Synthese. Frische Instruktions-Turns bleiben beim `tool_agent`.
Flag: `CC_TOOL_EXPERT_ROUTING=1` (Default aus).

### Schritt 5.1 — Neue Datei `services/tool_turn_router.py`:

```python
"""
services/tool_turn_router.py — Route synthesis turns to a stronger expert.

V1 scope: only turns that carry tool_result blocks (synthesis phase) are
re-routed to the template's 'code' or 'general' expert. Fresh instruction
turns stay on the tool_agent — its function-calling reliability is the
priority there.

Flag: CC_TOOL_EXPERT_ROUTING=1 (default off).
"""

import os


def pick_synthesis_expert(messages: list, experts: dict, current_model: str) -> dict | None:
    """Return an expert dict {model,url,token,endpoint,context_window} or None."""
    if os.getenv("CC_TOOL_EXPERT_ROUTING", "0") != "1":
        return None
    has_tool_results = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m.get("content", []))
        for m in messages
    )
    if not has_tool_results:
        return None
    for cat in ("code", "general"):
        for e in (experts or {}).get(cat) or []:
            if e.get("model") and e.get("url") and e.get("model") != current_model:
                return e
    return None
```

### Schritt 5.2 — Integration in `_anthropic_tool_handler`

**Anker (exakt vorhanden in `services/pipeline/anthropic.py`):**
```python
    effective_model = session.tool_model or JUDGE_MODEL
    effective_url   = session.tool_url   or JUDGE_URL
    effective_token = session.tool_token or JUDGE_TOKEN
    effective_node  = session.tool_endpoint or "unknown"
```
Füge DIREKT DANACH ein:
```python
    # Synthesis turns (tool_results present) may be handled by a stronger
    # template expert than the tool_agent — flag-gated, see tool_turn_router.
    from services.tool_turn_router import pick_synthesis_expert as _pse
    _syn_exp = _pse(messages, session.experts, effective_model)
    if _syn_exp:
        logger.info(
            "cc_tool: synthesis turn re-routed %s -> %s@%s",
            effective_model, _syn_exp["model"], _syn_exp.get("endpoint", "?"),
        )
        effective_model = _syn_exp["model"]
        effective_url   = _syn_exp["url"]
        effective_token = _syn_exp.get("token", "ollama")
        effective_node  = _syn_exp.get("endpoint", effective_node)
```

**Verifikation:** `python3 -m py_compile` beider Dateien; Log-Zeile
`synthesis turn re-routed` erscheint nur mit gesetztem Flag und tool_results.

---

## WP10 — GPU-Last-bewusstes Routing (In-Flight-Zähler)

**Problem:** Expertenaufrufe queuen sich auf demselben GPU-Node, obwohl
Alternativ-Nodes frei sind. Die Auslastungsdaten (`/api/ps`) werden gepollt,
fließen aber nicht in Routing-Entscheidungen.

### Schritt 10.1 — Neue Datei `services/node_load.py`:

```python
"""
services/node_load.py — In-process in-flight counters per inference node.

Lightweight load signal for routing decisions: every LLM call increments the
node's counter for its duration. When two expert candidates have the same
tier, the one on the less-loaded node is preferred.

Flag consumers check MOE_LOAD_AWARE_ROUTING=1 themselves.
"""

import threading
from collections import defaultdict
from contextlib import contextmanager

_lock = threading.Lock()
_inflight: dict = defaultdict(int)


@contextmanager
def track(node: str):
    """Context manager: marks one in-flight request on `node`."""
    node = node or "unknown"
    with _lock:
        _inflight[node] += 1
    try:
        yield
    finally:
        with _lock:
            _inflight[node] -= 1


def inflight(node: str) -> int:
    with _lock:
        return _inflight.get(node or "unknown", 0)


def least_loaded(candidates: list, node_key: str = "endpoint") -> list:
    """Stable-sort expert candidate dicts by current node load (ascending)."""
    import os
    if os.getenv("MOE_LOAD_AWARE_ROUTING", "0") != "1":
        return candidates
    return sorted(candidates, key=lambda c: inflight(c.get(node_key, "")))
```

### Schritt 10.2 — Integration

(a) **Zähler:** Finde die zentrale Experten-Aufruf-Funktion:
`grep -n "expert_call model=" services/ graph/ -r` (die Log-Zeile
`📊 expert_call model=…` aus dem Betrieb). Umschließe den dortigen LLM-Call
mit `with node_load.track(<NODE_NAME_VARIABLE>):` (Import oben in der Datei:
`from services import node_load`). **STOP-Regel:** >1 Aufrufstelle oder Node-
Variable unklar → nur Modul anlegen, Integration protokolliert überspringen.

(b) **Sortierung:** Finde die Stelle, an der die Modell-Liste einer
Experten-Kategorie in Primary/Fallback-Reihenfolge durchlaufen wird
(`grep -rn "_tier" services/ graph/ | grep -v routing.py` als Einstieg).
Sortiere Kandidaten GLEICHEN Tiers vor der Iteration mit
`node_load.least_loaded(...)`. Gleiche STOP-Regel.

**Verifikation:** `python3 -m py_compile services/node_load.py` + Unit-Check:
```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from services import node_load
with node_load.track('N04-RTX'):
    assert node_load.inflight('N04-RTX') == 1
assert node_load.inflight('N04-RTX') == 0
print('node_load OK')
"
```

---

# PHASE 4 — WISSENS-HYGIENE

## WP6 — Retrieval-Attribution und Graph-Decay

**Problem:** Der Knowledge-Graph wächst ungeprüft. Niemand misst, ob
abgerufene Chunks in der Antwort tatsächlich verwendet wurden; ohne
Decay/Pruning wird „intelligenter durch Benutzung" zu „verrauschter
durch Benutzung".

### Schritt 6.1 — Neue Datei `services/retrieval_attribution.py`:

```python
"""
services/retrieval_attribution.py — Did retrieved context actually help?

After a final answer is produced, each retrieved chunk is scored by lexical
overlap with the answer (fast token-set ratio). Scores accumulate on the
Neo4j nodes (hit_count, miss_count, last_hit) and drive the decay job
(scripts/graph_decay.py): chunks that are retrieved often but never used
get pruned.

Flag: MOE_RETRIEVAL_ATTRIBUTION=1 (default off).
"""

import logging
import os
import re

logger = logging.getLogger("MOE-SOVEREIGN")

_WORD_RE = re.compile(r"[a-zA-ZäöüÄÖÜß0-9_]{4,}")


def _token_set(text: str) -> set:
    return set(w.lower() for w in _WORD_RE.findall(text or ""))


def chunk_used_in_answer(chunk_text: str, answer: str, threshold: float = 0.35) -> bool:
    """True when >= threshold of the chunk's significant tokens appear in the answer."""
    ct = _token_set(chunk_text)
    if len(ct) < 5:
        return False
    at = _token_set(answer)
    return (len(ct & at) / len(ct)) >= threshold


async def record_attribution(driver, chunks: list, answer: str) -> None:
    """chunks: list of dicts with at least {'id': <neo4j element id or chunk id>, 'text': str}.

    Fire-and-forget: call via asyncio.create_task(). Never raises.
    """
    if os.getenv("MOE_RETRIEVAL_ATTRIBUTION", "0") != "1" or driver is None:
        return
    try:
        used_ids, miss_ids = [], []
        for c in chunks or []:
            cid = c.get("id")
            if not cid:
                continue
            (used_ids if chunk_used_in_answer(c.get("text", ""), answer) else miss_ids).append(cid)
        async with driver.session() as s:
            if used_ids:
                await s.run(
                    "MATCH (n) WHERE n.chunk_id IN $ids "
                    "SET n.hit_count = coalesce(n.hit_count,0)+1, n.last_hit = datetime()",
                    ids=used_ids,
                )
            if miss_ids:
                await s.run(
                    "MATCH (n) WHERE n.chunk_id IN $ids "
                    "SET n.miss_count = coalesce(n.miss_count,0)+1",
                    ids=miss_ids,
                )
        logger.info("retrieval_attribution: %d used / %d unused chunks", len(used_ids), len(miss_ids))
    except Exception as e:
        logger.warning("retrieval_attribution failed (non-fatal): %s", e)
```

### Schritt 6.2 — Neue Datei `scripts/graph_decay.py`:

```python
#!/usr/bin/env python3
"""
scripts/graph_decay.py — Prune knowledge-graph chunks that never help.

Deletes chunk nodes that were retrieved >= MIN_RETRIEVALS times with zero
hits, or not hit at all for DECAY_DAYS days. Run daily via host crontab:
    17 3 * * * cd /opt/deployment/moe-sovereign/moe-infra && python3 scripts/graph_decay.py >> /var/log/moe-graph-decay.log 2>&1

DRY_RUN=1 (default!) only reports; set DRY_RUN=0 to actually delete.
"""

import os
import re
import sys

MIN_RETRIEVALS = int(os.getenv("DECAY_MIN_RETRIEVALS", "5"))
DECAY_DAYS     = int(os.getenv("DECAY_DAYS", "90"))
DRY_RUN        = os.getenv("DRY_RUN", "1") != "0"

env = open(os.path.join(os.path.dirname(__file__), "..", ".env")).read()
uri  = re.search(r"NEO4J_URI=(\S+)", env)
pw   = re.search(r"NEO4J_PASS=(\S+)", env)
if not (uri and pw):
    print("NEO4J_URI/NEO4J_PASS nicht in .env gefunden"); sys.exit(1)

from neo4j import GraphDatabase
drv = GraphDatabase.driver(uri.group(1), auth=("neo4j", pw.group(1)))

FIND = (
    "MATCH (n) WHERE n.chunk_id IS NOT NULL AND ("
    " (coalesce(n.miss_count,0) >= $minr AND coalesce(n.hit_count,0) = 0)"
    " OR (n.last_hit IS NOT NULL AND n.last_hit < datetime() - duration({days:$days}))"
    ") RETURN count(n) AS c"
)
DELETE = FIND.replace("RETURN count(n) AS c", "DETACH DELETE n")

with drv.session() as s:
    c = s.run(FIND, minr=MIN_RETRIEVALS, days=DECAY_DAYS).single()["c"]
    print(f"Decay-Kandidaten: {c} (min_retrievals={MIN_RETRIEVALS}, days={DECAY_DAYS}, dry_run={DRY_RUN})")
    if c and not DRY_RUN:
        s.run(DELETE, minr=MIN_RETRIEVALS, days=DECAY_DAYS)
        print(f"{c} Chunk-Nodes gelöscht.")
drv.close()
```

### Schritt 6.3 — Attribution-Hook

**Anker-Suche:** `grep -rn "graphrag" services/pipeline/chat.py | head` —
finde die Stelle, an der GraphRAG-Chunks in den Kontext injiziert werden und
prüfe, ob dort eine Liste mit Chunk-Objekten (inkl. ID und Text) existiert.
Wenn ja: nach der finalen Antwort
`asyncio.create_task(record_attribution(state.graph_manager.driver, <CHUNKS>, <ANSWER>))`
einfügen. **STOP-Regel:** Wenn Chunk-IDs an der Injektionsstelle nicht
verfügbar sind → Hook überspringen und protokollieren
(„Attribution benötigt Chunk-ID-Durchreichung — Folgearbeit"), Module aus
6.1/6.2 trotzdem anlegen.

**Verifikation:** `py_compile` beider Dateien; `python3 scripts/graph_decay.py`
läuft im DRY_RUN durch und druckt eine Kandidatenzahl.

---

# PHASE 5 — SELBSTVERBESSERUNG

## WP7 — Distillations-Export: Pipeline-Gold als Trainingsdaten

**Problem:** Die teuersten, besten Antworten der Pipeline verpuffen. Sie sind
ideales LoRA-Trainingsmaterial für die eigenen SLMs — Token-Overhead würde zu
permanentem Fähigkeitsgewinn.

### Schritt 7.1 — Neue Datei `scripts/export_distillation_dataset.py`:

```python
#!/usr/bin/env python3
"""
scripts/export_distillation_dataset.py — Export high-quality pipeline answers
as a chat-format JSONL dataset for LoRA fine-tuning of the local SLMs.

Sources: conversation logs (CONVERSATION_LOG_DIR, JSONL per user) filtered to
moe_orchestrated requests. When pipeline_quality_log exists (WP3), only
requests whose probe verdict was 'pipeline' or 'tie' are included.

Usage:
    python3 scripts/export_distillation_dataset.py --out distill_$(date +%Y%m%d).jsonl \
        [--min-answer-chars 400] [--days 30]

Output format (one JSON object per line, axolotl/unsloth-compatible):
    {"conversations": [{"role": "user", "content": ...},
                       {"role": "assistant", "content": ...}]}
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

p = argparse.ArgumentParser()
p.add_argument("--out", required=True)
p.add_argument("--min-answer-chars", type=int, default=400)
p.add_argument("--days", type=int, default=30)
args = p.parse_args()

log_dir = os.getenv("CONVERSATION_LOG_DIR", "/opt/moe-infra/user-audit-logs")
cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

# Optional quality filter from WP3
quality_ok: set = set()
quality_available = False
try:
    import psycopg
    env = open(os.path.join(os.path.dirname(__file__), "..", ".env")).read()
    pw = re.search(r"MOE_USERDB_PASSWORD=(\S+)", env).group(1)
    with psycopg.connect(
        f"postgresql://moe_admin:{pw}@172.20.0.3:5432/moe_userdb", connect_timeout=5
    ) as conn, conn.cursor() as cur:
        cur.execute("SELECT request_id FROM pipeline_quality_log WHERE winner IN ('pipeline','tie')")
        quality_ok = {r[0] for r in cur.fetchall()}
        quality_available = True
except Exception as e:
    print(f"Hinweis: pipeline_quality_log nicht verfügbar ({e}) — exportiere ohne Qualitätsfilter")

n_in = n_out = 0
with open(args.out, "w", encoding="utf-8") as fout:
    for path in glob.glob(os.path.join(log_dir, "**", "*.jsonl"), recursive=True):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                n_in += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("moe_mode") not in ("moe_orchestrated", "cc_moe"):
                    continue
                ts = rec.get("timestamp") or rec.get("created_at") or ""
                try:
                    if ts and datetime.fromisoformat(ts.replace("Z", "+00:00")) < cutoff:
                        continue
                except ValueError:
                    pass
                q = (rec.get("input") or rec.get("query") or rec.get("prompt") or "").strip()
                a = (rec.get("final_response") or rec.get("response") or rec.get("answer") or "").strip()
                if not q or len(a) < args.min_answer_chars:
                    continue
                if quality_available and rec.get("request_id") and rec["request_id"] not in quality_ok:
                    continue
                fout.write(json.dumps({"conversations": [
                    {"role": "user", "content": q},
                    {"role": "assistant", "content": a},
                ]}, ensure_ascii=False) + "\n")
                n_out += 1
print(f"{n_out} Trainingsbeispiele aus {n_in} Log-Zeilen exportiert -> {args.out}")
if n_out == 0:
    print("WARNUNG: 0 Beispiele — Feldnamen der Log-Records prüfen (siehe Skript-Kommentar).")
    sys.exit(2)
```

**Hinweis für das ausführende Modell:** Die Feldnamen der Log-Records
(`input`/`final_response` etc.) sind Annahmen. Vor dem ersten Lauf EINE
Beispiel-Logdatei ansehen (`head -1 <datei> | python3 -m json.tool`) und die
Feldzuordnung im Skript bei Abweichung anpassen; Abweichung protokollieren.

**Verifikation:** Skript läuft und exportiert > 0 Beispiele (oder meldet
sauber Exit 2 mit Warnung).

---

## WP8 — Modell-Lebenszyklus: Registry + Benchmark-Empfehlung

**Problem:** Neue Modelle im Ollama-Bestand werden nicht systematisch gegen
amtierende Experten evaluiert. V1 erzeugt **Empfehlungen** (keine
Auto-Promotion — bewusste Sicherheitsentscheidung).

### Schritt 8.1 — Neue Datei `scripts/model_lifecycle.py`:

```python
#!/usr/bin/env python3
"""
scripts/model_lifecycle.py — Detect new local models and benchmark them.

1. Polls /api/tags of every INFERENCE_SERVER (api_type ollama).
2. New models (not in model_registry) get a mini-benchmark: N golden prompts
   per category, keyword-scored.
3. Result rows land in model_registry; models scoring >= the current template
   expert's score produce a PROMOTION-CANDIDATE log line (no auto-change).

Run weekly via host crontab:
    23 4 * * 1 cd /opt/deployment/moe-sovereign/moe-infra && python3 scripts/model_lifecycle.py >> /var/log/moe-model-lifecycle.log 2>&1
"""

import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = open(os.path.join(BASE, ".env")).read()
pw = re.search(r"MOE_USERDB_PASSWORD=(\S+)", env).group(1)
DSN = f"postgresql://moe_admin:{pw}@172.20.0.3:5432/moe_userdb"

servers_raw = re.search(r'INFERENCE_SERVERS="(.+?)"\n', env, re.S)
SERVERS = json.loads(servers_raw.group(1).replace('\\"', '"')) if servers_raw else []

GOLDEN = [
    ("code",      "Write a Python function `fib(n)` returning the nth Fibonacci number iteratively. Only code.",
                  ["def fib", "return"]),
    ("general",   "Explain in two sentences why the sky is blue.",
                  ["scatter", "wavelength"]),
    ("reasoning", "Anna is older than Ben. Ben is older than Carl. Who is youngest? One word.",
                  ["carl"]),
    ("tool",      'Respond ONLY with JSON calling tool "search" with query "weather berlin": '
                  '{"name": ..., "arguments": {...}}',
                  ['"name"', "search", "weather"]),
]

TABLE = """CREATE TABLE IF NOT EXISTS model_registry (
    model TEXT, node TEXT, first_seen TIMESTAMPTZ DEFAULT now(),
    benchmarked_at TIMESTAMPTZ, score REAL, details TEXT,
    PRIMARY KEY (model, node))"""


def ollama(url, path, payload=None, timeout=180):
    req = urllib.request.Request(url.rstrip("/").removesuffix("/v1") + path,
                                 data=json.dumps(payload).encode() if payload else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def bench(url, model):
    hits, details = 0, []
    for cat, prompt, keywords in GOLDEN:
        try:
            j = ollama(url, "/api/chat", {
                "model": model, "stream": False, "think": False,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"num_predict": 512},
            })
            ans = (j.get("message", {}).get("content", "") or "").lower()
            ok = all(k.lower() in ans for k in keywords)
            hits += ok
            details.append(f"{cat}:{'OK' if ok else 'FAIL'}")
        except Exception as e:
            details.append(f"{cat}:ERR({e})")
    return hits / len(GOLDEN), " ".join(details)


import psycopg
with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute(TABLE)
    cur.execute("SELECT model, node FROM model_registry")
    known = set(cur.fetchall())
    for srv in SERVERS:
        if srv.get("api_type", "ollama") != "ollama" or not srv.get("url"):
            continue
        try:
            tags = ollama(srv["url"], "/api/tags", timeout=10)
        except Exception as e:
            print(f"{srv['name']}: nicht erreichbar ({e})"); continue
        for m in tags.get("models", []):
            key = (m["name"], srv["name"])
            if key in known:
                continue
            print(f"NEU: {m['name']} auf {srv['name']} — Benchmark läuft…")
            score, details = bench(srv["url"], m["name"])
            cur.execute(
                "INSERT INTO model_registry (model,node,benchmarked_at,score,details) "
                "VALUES (%s,%s,now(),%s,%s) ON CONFLICT (model,node) DO NOTHING",
                (m["name"], srv["name"], score, details),
            )
            conn.commit()
            print(f"  Score {score:.2f} — {details}")
            if score >= 0.75:
                print(f"  >>> PROMOTION-KANDIDAT: {m['name']}@{srv['name']} (Score {score:.2f}) — "
                      f"manuell gegen Template-Experten evaluieren.")
print("model_lifecycle abgeschlossen.")
```

**Verifikation:** Skript läuft durch (bereits bekannte Modelle → keine
Benchmarks, nur Registry-Befüllung beim Erstlauf; das kann dauern —
akzeptabel, da Cron-Kontext). Tabelle `model_registry` enthält danach Zeilen.

---

# PHASE 6 — SOUVERÄNITÄT

## WP9 — `local_only` als erzwungene Policy (OPA-ready, lokale Durchsetzung)

**Problem:** Cloud-Egress (z. B. AIHUB) wird nur per Konfiguration vermieden,
nicht erzwungen. Souveränität heißt: *beweisbar* kein Cloud-Pfad.

**Design V1:** Zentrale Guard-Funktion mit lokaler Allowlist (private
IP-Bereiche + explizite Allowlist-Hosts). OPA-Anbindung als vorbereiteter
zweiter Schritt (Policy-Datei wird mitgeliefert). Flag pro Request aus
`user_ctx["local_only_routing"]`.

### Schritt 9.1 — Neue Datei `services/sovereignty.py`:

```python
"""
services/sovereignty.py — Egress guard for local-only routing.

When a request's user context carries local_only_routing=1, every outbound
LLM call must target a private/allowlisted host. Violations raise
EgressDenied BEFORE any payload leaves the system — configuration mistakes
must fail loudly, not leak silently.

Allowlist extension: MOE_LOCAL_ALLOW_HOSTS="host1,host2" (exact hostnames).
"""

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

logger = logging.getLogger("MOE-SOVEREIGN")


class EgressDenied(Exception):
    pass


_PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128",
)]


def _host_is_local(host: str) -> bool:
    allow = {h.strip() for h in os.getenv("MOE_LOCAL_ALLOW_HOSTS", "").split(",") if h.strip()}
    if host in allow:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not any(ip in net for net in _PRIVATE_NETS):
            return False
    return bool(infos)


def assert_egress_allowed(url: str, user_ctx: dict | None) -> None:
    """Raise EgressDenied when local_only_routing is set and url is non-local."""
    if not user_ctx or user_ctx.get("local_only_routing") != "1":
        return
    host = urlparse(url).hostname or ""
    if not _host_is_local(host):
        logger.error("sovereignty: BLOCKED egress to %s (local_only key)", host)
        raise EgressDenied(
            f"local_only routing: endpoint '{host}' is not a local/allowlisted host"
        )
```

### Schritt 9.2 — OPA-Policy-Datei ablegen (Vorbereitung, keine Verdrahtung)

Neue Datei `docker/opa/moe_routing.rego`:
```rego
package moe.routing

# Deny outbound LLM egress for local_only requests to non-private hosts.
# Wired up in a follow-up: services/sovereignty.py will query OPA when
# MOE_OPA_URL is set; until then the in-process guard enforces the same rule.
default allow := false

allow if {
    not input.local_only
}

allow if {
    input.local_only
    input.host_is_private
}
```

### Schritt 9.3 — Guard-Integration im Tool-Handler

**Anker (exakt vorhanden in `services/pipeline/anthropic.py`, Beginn von
`_anthropic_tool_handler`-Nutzung der URL):**
`grep -n '_call_url = f"{_ollama_base}/api/chat"' services/pipeline/anthropic.py`
Direkt NACH dieser Zeile UND nach der Zeile
`_call_url = f"{effective_url}/chat/completions"` (else-Zweig) jeweils prüfen,
ob `user_ctx` im Scope ist. Der Tool-Handler erhält `session`, nicht
`user_ctx` — daher: Guard stattdessen im Endpoint `anthropic_messages`
einbauen. **Anker:**
```python
    _profile_ids = json.loads(user_ctx.get("permissions_json", "") or "{}").get("cc_profile", [])
    session = _resolve_cc_session(user_ctx, _profile_ids)
```
Füge DIREKT DANACH ein:
```python
    # Sovereignty guard: local_only keys must never reach non-local endpoints.
    from services.sovereignty import assert_egress_allowed, EgressDenied
    try:
        if session.tool_url:
            assert_egress_allowed(session.tool_url, user_ctx)
    except EgressDenied as _ed:
        return JSONResponse(status_code=403, content={"error": {
            "message": str(_ed), "type": "permission_error", "code": "local_only_violation",
        }})
```

**Verifikation:**
```bash
python3 -m py_compile services/sovereignty.py services/pipeline/anthropic.py
python3 -c "
import sys; sys.path.insert(0,'.')
from services.sovereignty import assert_egress_allowed, EgressDenied
assert_egress_allowed('http://192.168.155.224:11435', {'local_only_routing':'1'})
try:
    assert_egress_allowed('https://api.openai.com/v1', {'local_only_routing':'1'})
    raise SystemExit('FEHLER: Egress nicht blockiert')
except EgressDenied:
    print('sovereignty OK')
"
```

---

# PHASE 7 — ROBUSTHEIT

## WP11 — Schema-Verträge zwischen Pipeline-Stufen

**Problem:** Planner→Experte→Judge kommunizieren über Prosa; Parse-Fehler
werden durch fragile Fallback-Ketten kaschiert.

### Schritt 11.1 — Neue Datei `services/pipeline/contracts.py`:

```python
"""
services/pipeline/contracts.py — Typed contracts between pipeline stages.

parse_plan()/parse_verdict() are tolerant parsers with a single well-defined
result shape. Adoption is incremental: call sites keep their current fallback
behavior when parsing fails (flag MOE_STRICT_CONTRACTS=1 makes failures loud).
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger("MOE-SOVEREIGN")


@dataclass
class PlanTask:
    category: str = "general"
    instruction: str = ""


@dataclass
class PlannerPlan:
    tasks: list = field(default_factory=list)   # list[PlanTask]
    raw: str = ""
    valid: bool = False


def _first_json(text: str):
    dec = json.JSONDecoder()
    pos = text.find("{")
    while pos >= 0:
        try:
            obj, _ = dec.raw_decode(text, pos)
            return obj
        except (json.JSONDecodeError, ValueError):
            pos = text.find("{", pos + 1)
    pos = text.find("[")
    if pos >= 0:
        try:
            return json.loads(text[pos:])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def parse_plan(raw: str) -> PlannerPlan:
    plan = PlannerPlan(raw=raw or "")
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    obj = _first_json(cleaned)
    tasks = obj.get("tasks") if isinstance(obj, dict) else obj
    if isinstance(tasks, list):
        for t in tasks:
            if isinstance(t, dict) and (t.get("category") or t.get("instruction") or t.get("task")):
                plan.tasks.append(PlanTask(
                    category=str(t.get("category", "general")),
                    instruction=str(t.get("instruction") or t.get("task") or ""),
                ))
        plan.valid = bool(plan.tasks)
    if not plan.valid and os.getenv("MOE_STRICT_CONTRACTS", "0") == "1":
        logger.error("contracts: planner output failed schema parse (chars=%d)", len(raw or ""))
    return plan


def parse_verdict(raw: str) -> dict:
    """Judge verdict: {'winner': int|-1, 'reason': str, 'valid': bool}."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    obj = _first_json(cleaned)
    if isinstance(obj, dict) and "winner" in obj:
        try:
            return {"winner": int(obj["winner"]), "reason": str(obj.get("reason", "")), "valid": True}
        except (TypeError, ValueError):
            pass
    m = re.search(r"\b(?:answer|antwort|winner)\s*[:#]?\s*(\d)", cleaned, re.I)
    if m:
        return {"winner": int(m.group(1)), "reason": "", "valid": True}
    if os.getenv("MOE_STRICT_CONTRACTS", "0") == "1":
        logger.error("contracts: judge verdict failed schema parse (chars=%d)", len(raw or ""))
    return {"winner": -1, "reason": "", "valid": False}
```

### Schritt 11.2 — Adoption (nur wenn eindeutig)

Suche die Planner-Output-Parse-Stelle: `grep -rn "planner" graph/*.py | grep -i "json\|parse" | head`.
Wenn dort eine eigene JSON-Extraktion existiert, die dieselbe Struktur
liefert wie `parse_plan()`: ersetze sie durch einen `parse_plan()`-Aufruf und
mappe `plan.tasks` auf die bestehende Struktur — NUR wenn das Mapping
1:1 offensichtlich ist. **STOP-Regel:** Bei jeder Unklarheit → nur das Modul
anlegen (Schritt 11.1 zählt), Adoption als Folgearbeit protokollieren.

**Verifikation:**
```bash
python3 -m py_compile services/pipeline/contracts.py
python3 -c "
import sys; sys.path.insert(0,'.')
from services.pipeline.contracts import parse_plan, parse_verdict
p = parse_plan('Text davor {\"tasks\":[{\"category\":\"code\",\"instruction\":\"tue X\"}]} danach')
assert p.valid and p.tasks[0].category == 'code'
v = parse_verdict('<think>hmm</think>{\"winner\": 2, \"reason\": \"besser\"}')
assert v['valid'] and v['winner'] == 2
print('contracts OK')
"
```

---

# PHASE 8 — INFRASTRUKTUR (nur mit Betreiber-Freigabe)

## WP12 — vLLM mit Prefix-Caching für den heißesten Node (Runbook)

**Problem:** Experten auf demselben Node ingestieren denselben 30k+-Kontext
bei jedem Aufruf neu (beobachtet: 33k Prompt-Tokens pro Expertenaufruf).
vLLM mit `--enable-prefix-caching` teilt den KV-Cache über Requests.

**Dies ist ein Ops-Runbook, KEIN Code-WP. Vor Beginn Betreiber-Freigabe
einholen (GPU-VRAM-Budget, Modellverfügbarkeit als HF-Weights).**

1. **Vorprüfung:** `qwen3.6:35b` ist ein Ollama-Modellname. Klären, welches
   HuggingFace-Repo dem entspricht (z. B. AWQ/GPTQ-Quantisierung für vLLM)
   und ob der Ziel-Node (N04-RTX, GPU-VRAM prüfen via `nvidia-smi`) das
   Modell in vLLM-tauglicher Quantisierung tragen kann. **Ohne eindeutige
   Antwort: STOPPEN, Betreiber fragen.**
2. **Testinstanz auf Zweitport** (kein Eingriff in Ollama):
   ```bash
   docker run -d --name vllm-n04-test --gpus all -p 8011:8000 \
     -v /opt/models/hf:/root/.cache/huggingface \
     vllm/vllm-openai:latest \
     --model <HF_REPO> --quantization awq \
     --enable-prefix-caching --max-model-len 65536
   ```
3. **Benchmark Vorher/Nachher:** identischer 30k-Token-Prompt, 3 aufeinander-
   folgende Requests mit gleichem Prefix; Time-to-first-token und Gesamtdauer
   messen (Ollama :11435 vs. vLLM :8011). Erwartung: Request 2+3 auf vLLM
   deutlich schneller (Prefix-Cache-Hit).
4. **Bei Erfolg:** neuen INFERENCE_SERVER `N04-VLLM` (api_type `openai`,
   URL `http://<node-ip>:8011/v1`) in der Admin-UI anlegen, im Template
   testweise EINEN Experten (z. B. `long_context`) auf `@N04-VLLM` umstellen,
   eine Woche Metriken vergleichen, dann schrittweise weitere Experten.
5. **Rollback:** Template-Endpoints zurückstellen, Container entfernen.

---

# ROLLOUT & GESAMTVERIFIKATION

Nach jeder Phase:
```bash
cd /opt/deployment/moe-sovereign/moe-infra
# 1. Alle in der Phase geänderten Dateien: py_compile (siehe WP-Verifikationen)
# 2. Neustart:
docker compose restart langgraph-app
sleep 15
docker logs langgraph-orchestrator --since=60s 2>&1 | grep -cE "ERROR|Traceback"   # muss 0 sein
docker inspect langgraph-orchestrator --format '{{.State.Health.Status}}'          # muss healthy sein
# 3. Smoke-Test (Test-API-Key aus Betreiber-Memory, NIE SYSTEM_API_KEY):
curl -s -X POST http://localhost:8002/v1/messages -H "x-api-key: <TEST_KEY>" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":50,"messages":[{"role":"user","content":"Sag OK"}]}' \
  | head -c 300
```

**Flags-Übersicht für den Betreiber** (alle Default AUS; Aktivierung einzeln
in `.env`, danach Neustart):

| Flag | WP | Wirkung |
|---|---|---|
| `CC_FASTPATH=1` | WP1 | Utility-/Trivial-Requests umgehen die Pipeline |
| `MOE_QUALITY_PROBE=1` | WP3 | Stichproben-A/B Pipeline vs. Einzelexperte |
| `MOE_JUDGE_GATE=1` | WP4 | Judge-Skip bei Konsens/Einzelergebnis |
| `CC_TOOL_EXPERT_ROUTING=1` | WP5 | Synthesis-Turns an stärkeren Experten |
| `MOE_LOAD_AWARE_ROUTING=1` | WP10 | Node-Last fließt in Kandidaten-Reihenfolge |
| `MOE_RETRIEVAL_ATTRIBUTION=1` | WP6 | Chunk-Nutzung wird im Graph verbucht |
| `MOE_STRICT_CONTRACTS=1` | WP11 | Schema-Parse-Fehler werden laut geloggt |

**Empfohlene Aktivierungs-Reihenfolge:** CC_FASTPATH → MOE_QUALITY_PROBE
(1 Woche Daten sammeln) → MOE_JUDGE_GATE → Rest nach Datenlage.

# Abschlussprotokoll (vom ausführenden Modell zu erstellen)

Pro WP: umgesetzt / übersprungen (Grund) / abgewandelt (Begründung),
Verifikations-Outputs angehängt. Explizit berichten:
- WP3/WP4/WP6/WP10/WP11: Wurden die Integrations-Anker gefunden oder griff
  eine STOP-Regel? (Module anlegen zählt als Teilerfolg.)
- WP7: tatsächliche Feldnamen der Log-Records.
- WP12: nur nach Betreiber-Freigabe; sonst „wartet auf Freigabe".

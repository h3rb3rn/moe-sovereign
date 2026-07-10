#!/usr/bin/env python3
"""
scripts/export_distillation_dataset.py — Export high-quality pipeline answers
as a chat-format JSONL dataset for LoRA fine-tuning of the local SLMs.

Source: conversation logs written by services/conversation_log.py
(CONVERSATION_LOG_DIR, one file per user: "{user_id}.jsonl" for the current
day plus rotated "{user_id}.jsonl-YYYYMMDD" and "{user_id}.jsonl-YYYYMMDD.gz").
Each line is a JSON object with fields: ts, request_id, session_id, model,
moe_mode, messages (list of {role, content}), response (answer string),
prompt_tokens, completion_tokens, latency_ms, expert_domains, cache_hit,
agentic_rounds. There is NO "input"/"final_response" field — the query is the
last role=="user" entry in `messages`, the answer is the top-level `response`.

When pipeline_quality_log exists (WP3), only requests whose probe verdict was
'pipeline' or 'tie' are included (quality gate).

Usage:
    python3 scripts/export_distillation_dataset.py --out distill_$(date +%Y%m%d).jsonl \
        [--min-answer-chars 400] [--days 30]

Output format (one JSON object per line, axolotl/unsloth-compatible):
    {"conversations": [{"role": "user", "content": ...},
                       {"role": "assistant", "content": ...}]}
"""

import argparse
import gzip
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--out", required=True)
p.add_argument("--min-answer-chars", type=int, default=400)
p.add_argument("--days", type=int, default=30)
args = p.parse_args()

log_dir = Path(os.getenv("CONVERSATION_LOG_DIR", "/opt/moe-infra/user-audit-logs"))
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
        cur.execute(
            "SELECT to_regclass('public.pipeline_quality_log')"
        )
        if cur.fetchone()[0] is not None:
            cur.execute("SELECT request_id FROM pipeline_quality_log WHERE winner IN ('pipeline','tie')")
            quality_ok = {r[0] for r in cur.fetchall()}
            quality_available = True
except Exception as e:
    print(f"Hinweis: pipeline_quality_log nicht verfügbar ({e}) — exportiere ohne Qualitätsfilter")


def _open_lines(path: Path):
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            yield from f
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            yield from f


def _last_user_message(messages: list) -> str:
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content", "")
            return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    return ""


n_in = n_out = 0
files: list[Path] = []
if log_dir.is_dir():
    for f in log_dir.iterdir():
        if f.name.endswith(".jsonl") or re.search(r"\.jsonl-\d{8}(\.gz)?$", f.name):
            files.append(f)
else:
    print(f"CONVERSATION_LOG_DIR nicht gefunden oder kein Verzeichnis: {log_dir}")
    sys.exit(1)

with open(args.out, "w", encoding="utf-8") as fout:
    for path in files:
        try:
            lines = _open_lines(path)
        except Exception as e:
            print(f"Überspringe {path}: {e}")
            continue
        for line in lines:
            n_in += 1
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts") or ""
            try:
                if ts and datetime.fromisoformat(ts) < cutoff:
                    continue
            except ValueError:
                pass
            q = _last_user_message(rec.get("messages") or []).strip()
            a = (rec.get("response") or "").strip()
            if not q or len(a) < args.min_answer_chars:
                continue
            if quality_available and rec.get("request_id") and rec["request_id"] not in quality_ok:
                continue
            fout.write(json.dumps({"conversations": [
                {"role": "user", "content": q},
                {"role": "assistant", "content": a},
            ]}, ensure_ascii=False) + "\n")
            n_out += 1

print(f"{n_out} Trainingsbeispiele aus {n_in} Log-Zeilen ({len(files)} Dateien) exportiert -> {args.out}")
if n_out == 0:
    print("WARNUNG: 0 Beispiele — Feldnamen/Schwellwerte prüfen (siehe Skript-Kommentar).")
    sys.exit(2)

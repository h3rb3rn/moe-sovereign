#!/bin/bash
# One-shot host-side backup for the 2026-07-12 quality-probe evaluation.
# Purpose: capture pipeline_quality_log stats to disk even if the Claude
# session that scheduled the in-session CronCreate job (job 7663332c) no
# longer exists on that date. Does NOT decide on MOE_JUDGE_GATE — that
# judgment call is left for review of this snapshot (see
# SESSION_DOKUMENTATION_2026-07-05.md for the decision heuristic).
# Self-removes its own crontab line after running (one-shot).

set -euo pipefail
cd /opt/deployment/moe-sovereign/moe-infra
OUT="/opt/moe-infra/quality_probe_snapshot_2026-07-12.txt"

PW=$(grep -oP 'MOE_USERDB_PASSWORD=\K\S+' .env)

{
  echo "=== Quality-Probe Snapshot — $(date '+%Y-%m-%d %H:%M %Z') ==="
  echo "Kontext: Auswertung 1 Woche nach Aktivierung von CC_FASTPATH/MOE_QUALITY_PROBE."
  echo "Entscheidungslogik siehe SESSION_DOKUMENTATION_2026-07-05.md."
  echo
  echo "--- Verteilung nach winner ---"
  PGPASSWORD="$PW" psql -h 172.20.0.3 -U moe_admin -d moe_userdb -c \
    "SELECT winner, count(*) FROM pipeline_quality_log GROUP BY winner ORDER BY count(*) DESC;" 2>&1 \
    || python3 - << PYEOF
import psycopg
with psycopg.connect("postgresql://moe_admin:${PW}@172.20.0.3:5432/moe_userdb") as conn, conn.cursor() as cur:
    cur.execute("SELECT winner, count(*) FROM pipeline_quality_log GROUP BY winner ORDER BY count(*) DESC")
    for row in cur.fetchall():
        print(row)
PYEOF
  echo
  echo "--- Gesamtzahl und letzte 7 Tage ---"
  python3 - << PYEOF
import psycopg
with psycopg.connect("postgresql://moe_admin:${PW}@172.20.0.3:5432/moe_userdb") as conn, conn.cursor() as cur:
    cur.execute("SELECT to_regclass('public.pipeline_quality_log')")
    if cur.fetchone()[0] is None:
        print("Tabelle pipeline_quality_log existiert noch nicht — keine Probe-Treffer bisher.")
    else:
        cur.execute("SELECT count(*), count(*) FILTER (WHERE created_at > now() - interval '7 days') FROM pipeline_quality_log")
        total, last7 = cur.fetchone()
        print(f"total={total} letzte_7_tage={last7}")
PYEOF
} > "$OUT" 2>&1

chmod 644 "$OUT"
echo "Snapshot geschrieben nach $OUT" >> "$OUT"

# Self-remove: delete this line from the crontab after running once.
crontab -l 2>/dev/null | grep -v "quality_probe_snapshot_2026-07-12.sh" | crontab -

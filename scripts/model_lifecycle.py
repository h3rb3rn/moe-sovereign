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
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = open(os.path.join(BASE, ".env")).read()
pw = re.search(r"MOE_USERDB_PASSWORD=(\S+)", env).group(1)
DSN = f"postgresql://moe_admin:{pw}@172.20.0.3:5432/moe_userdb"

servers_raw = re.search(r'INFERENCE_SERVERS="(.+?)"\n', env)
_servers_json = servers_raw.group(1).replace('\\"', '"') if servers_raw else "[]"
SERVERS = json.loads(_servers_json)

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

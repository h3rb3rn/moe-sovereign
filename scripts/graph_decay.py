#!/usr/bin/env python3
"""
scripts/graph_decay.py — Prune knowledge-graph chunks that never help.

Deletes chunk nodes that were retrieved >= MIN_RETRIEVALS times with zero
hits, or not hit at all for DECAY_DAYS days. Run daily via host crontab:
    17 3 * * * cd /opt/moe-infra && python3 scripts/graph_decay.py >> /var/log/moe-graph-decay.log 2>&1

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

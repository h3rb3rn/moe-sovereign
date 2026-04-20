# Maintenance & Disk Management

MoE Sovereign runs a set of **autonomous cleanup jobs** that keep disk usage bounded without manual intervention. The **System Cleanup Manager** in the Admin UI exposes these jobs with configurable TTLs, last-run history, and manual trigger buttons.

---

## System Cleanup Manager

Navigate to **Admin → Maintenance** and scroll to the *System Cleanup Manager* card.

Each cleanup job is displayed as a card showing:

| Column | Description |
|---|---|
| **Last run** | Timestamp and relative age (e.g. "vor 3 h") |
| **Duration** | Last run duration and rolling average |
| **Freed** | Bytes reclaimed last run and rolling average |
| **Runs total** | Lifetime run count |
| **Details** | Job-specific metrics (images freed, rows deleted, dump size …) |

A **Jetzt** button triggers the job immediately in the background; the status card refreshes after 5 seconds.

A **Konfig** toggle opens an inline configuration panel. Changes are persisted to `/opt/moe-infra/cleanup-config.json` and take effect on the next run (cron or manual).

The page auto-refreshes every 60 seconds.

---

## Cleanup Jobs

### Docker Prune

**Schedule:** daily (cron)  
**Script:** `/etc/cron.daily/moe-docker-prune`  
**Log:** `/var/log/moe-docker-prune.log`

Removes dangling images, stopped containers, and the entire build cache.

```
docker image prune -f
docker container prune -f
docker builder prune --all -f
```

!!! warning "Build cache growth"
    Frequent rebuilds of `langgraph-app` (2.35 GB) produce 50+ GB of build cache per day. Without this job, `/var/lib/docker/overlay2` can fill a disk within hours. The daily prune keeps the steady-state below ~5 GB.

**Configurable fields:**

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Disable to pause the job |

---

### LangGraph Checkpoint Archive

**Schedule:** daily (cron)  
**Script:** `/etc/cron.daily/moe-checkpoint-archive`  
**Env file:** `/etc/cron.daily/moe-checkpoint-archive.env`  
**Log:** `/var/log/moe-checkpoint-archive.log`  
**Archive directory:** `/opt/moe-infra/checkpoint-archives/`

LangGraph persists every graph step to PostgreSQL (`terra_checkpoints`). Without pruning, the three checkpoint tables grow without bound.

The job runs in two phases:

1. **Archive** — `pg_dump --format=custom -Z9` of the full `langgraph` database. Compression ratio is typically 8–12 %, so 265 MB of live data produces a ~32 MB `.pgdump` file.
2. **Prune** — Deletes checkpoints older than the live-DB window (`keep_rows`). Orphaned `checkpoint_blobs` (linked by `thread_id`) are cleaned up after the `checkpoints` delete.

```
Phase A: DELETE checkpoint_writes WHERE checkpoint_id NOT IN (newest N)
Phase B: DELETE checkpoints WHERE checkpoint_id NOT IN (newest N)
Phase C: DELETE checkpoint_blobs WHERE thread_id NOT IN checkpoints
Phase D: VACUUM ANALYZE all three tables
Phase E: Remove archives older than retain_days
```

!!! note "Audit compliance"
    No conversation data is destroyed. Every daily dump is retained for `retain_days` (default 90). A full restore for audit purposes:

    ```bash
    docker exec terra_checkpoints pg_restore \
      -U langgraph -d langgraph_audit \
      /opt/moe-infra/checkpoint-archives/2026-04-19.pgdump
    ```

**Configurable fields:**

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Disable to pause the job |
| `keep_rows` | `30 000` | Live-DB window — rows to retain in `checkpoints` |
| `retain_days` | `90` | Days of daily `.pgdump` archives to keep |

---

### Admin-Log Rotation

**Schedule:** inline — triggered on every monitoring API write  
**Configuration:** `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` in `.env`  
**Files:** `/opt/moe-infra/admin-logs/llm_instances.jsonl`, `active_requests.jsonl`

The live-monitoring page polls `/api/live/llm-instances` and `/api/live/active-requests` every 5 seconds. Each poll appends a JSON snapshot to a JSONL file. Without rotation this produces ~830 MB per two weeks.

`_append_log()` in `app.py` checks file size before every write. When the limit is reached, it rotates:

```
active_requests.jsonl      → active_requests.jsonl.1
active_requests.jsonl.1    → active_requests.jsonl.2  (if backup_count ≥ 2)
```

A `logrotate` config at `/etc/logrotate.d/moe-admin-logs` provides an additional daily safety net with `compress` and `delaycompress`.

**Configurable fields:**

| Field | Default | Environment variable |
|---|---|---|
| `max_bytes` | `52 428 800` (50 MB) | `LOG_MAX_BYTES` |
| `backup_count` | `2` | `LOG_BACKUP_COUNT` |

---

### systemd Journal

**Schedule:** applied on journald restart  
**Config:** `/etc/systemd/journald.conf.d/size-limit.conf`

```ini
[Journal]
SystemMaxUse=512M
SystemKeepFree=1G
MaxRetentionSec=30day
```

The journal is capped at 512 MB. Entries older than 30 days are vacuumed automatically.

**Configurable fields (read-only in UI, edit config file manually):**

| Field | Default |
|---|---|
| `max_use_mb` | `512` |
| `retain_days` | `30` |

---

### Prometheus TSDB

**Schedule:** continuous — Prometheus enforces the window internally  
**Config:** `PROMETHEUS_RETENTION_DAYS` in `.env`

Prometheus discards metric blocks older than the configured retention window. The value is injected at container start via the `--storage.tsdb.retention.time` flag in `docker-compose.yml`.

!!! warning "Restart required"
    Changes to `PROMETHEUS_RETENTION_DAYS` require `docker compose up -d moe-prometheus` to take effect.

**Configurable fields:**

| Field | Default | Description |
|---|---|---|
| `retention_days` | `30` | Days of metrics to retain |

---

## Configuration File

All cleanup parameters are stored in `/opt/moe-infra/cleanup-config.json` and mounted read-write into the `moe-admin` container at `/app/cleanup-config.json`.

```json
{
  "docker_prune": {
    "enabled": true,
    "schedule": "daily"
  },
  "checkpoint_archive": {
    "enabled": true,
    "schedule": "daily",
    "keep_rows": 30000,
    "retain_days": 90
  },
  "admin_logs": {
    "enabled": true,
    "schedule": "on_write",
    "max_bytes": 52428800,
    "backup_count": 2
  },
  "journal": {
    "enabled": true,
    "schedule": "on_restart",
    "max_use_mb": 512,
    "retain_days": 30
  },
  "prometheus": {
    "enabled": true,
    "schedule": "continuous",
    "retention_days": 30
  }
}
```

Editing via the Admin UI writes this file directly. Cron scripts re-read it on every run so config changes take effect without restarting containers.

---

## History & Metrics

Every cron run appends a structured record to `/opt/moe-infra/cleanup-history.jsonl`:

```json
{
  "job": "docker_prune",
  "ts": "2026-04-19T01:00:00Z",
  "duration_s": 47,
  "freed_bytes": 48560000000,
  "details": {
    "images_freed": "48.5 GB",
    "build_cache_freed": "0 B"
  }
}
```

The Admin UI reads this file and computes **rolling averages** for freed bytes and duration. This lets you spot when a system grows abnormally fast — for example, if `avg_freed_bytes` for `docker_prune` spikes from 2 GB to 40 GB, it indicates an unusual rebuild burst.

---

## Additional Cleanup Mechanisms

The following mechanisms operate outside the Cleanup Manager UI but are deployed automatically:

### Docker Container Log Rotation

Configured in `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
```

Each container retains at most 3 × 50 MB = 150 MB of logs. Existing containers inherit this limit on their next restart.

### Valkey AOF Compaction

Valkey (`terra_cache`) is configured with automatic AOF rewrite:

```
--auto-aof-rewrite-percentage 50
--auto-aof-rewrite-min-size 32mb
--maxmemory 400mb
--maxmemory-policy allkeys-lru
```

When the AOF grows by 50 % over the last rewrite baseline, Valkey compacts it in the background. The `allkeys-lru` policy evicts the least-recently-used keys when memory reaches 400 MB.

### Neo4j Transaction Logs

Configured in `docker-compose.yml`:

```yaml
- NEO4J_db_tx__log_rotation_retention__policy=2 files
```

Neo4j retains only the two most recent transaction log files, preventing unbounded growth of `/opt/moe-infra/neo4j-data/`.

---

## Disk Budget Reference

| Subsystem | Steady-State Size | TTL Mechanism |
|---|---|---|
| Docker overlay2 | ~47 GB (images) | daily prune clears dangling / build cache |
| Docker build cache | < 5 GB | daily `builder prune --all` |
| Docker container logs | < 150 MB per container | `max-size=50m, max-file=3` |
| Admin JSONL logs | < 150 MB | 50 MB rotation, 2 backups |
| LangGraph checkpoints (live) | ~90 MB | 30 000 row window + daily VACUUM |
| Checkpoint archives | ~32 MB × 90 = 2.9 GB | 90-day rotation |
| Prometheus TSDB | ~0.5–1 GB | 30-day retention |
| Neo4j data | ~500 MB | 2-file TX-log retention |
| systemd journal | < 512 MB | 30-day + 512 MB cap |
| Valkey AOF | < 400 MB | LRU eviction + AOF compaction |
| Kafka data | < 512 MB | 7-day / 512 MB retention |
| **Total estimated** | **~55 GB** | All automated |

---

## Ontology Gap Healer

The **Ontology Gap Healer** processes unknown terms that accumulate in the `moe:ontology_gaps` Redis sorted set and writes classified entities to Neo4j.

### One-shot run

Navigate to **Admin → Maintenance → Ontology Gaps** and click **Einmalig starten**. The healer processes the current gap queue once and stops when the queue is empty or the maximum iteration limit is reached.

### Dedicated (daemon) run

Click **Dauerlauf starten** to launch the healer as a persistent daemon:

- The healer runs continuously, picking up new gaps as they arrive.
- An `auto_restart` flag in Redis ensures a new subprocess is spawned automatically ~30 seconds after each batch completes.
- If the container restarts, `auto_restart` is preserved and the healer resumes on startup (after a 5-second ASGI warmup delay).
- Click **Dauerlauf stoppen** to clear the `auto_restart` flag and terminate the daemon.

#### Monitoring

| Source | What to check |
|---|---|
| **Admin → Statistics → Healer-Historie** | All completed runs with type (Dauerlauf / Einmalig), duration, written, and failed counts |
| **Admin → Live-Monitoring** | Active API requests — a healer run appears as a request on the curator template's node |
| **Watchdog** | Every 60 s the internal watchdog verifies PID liveness and detects stalls (no output for 5 min); both trigger auto-restart |

### Stall detection

If the healer subprocess produces no output for **5 minutes**, the watchdog marks it as stalled (`stalled=1` in Redis) and triggers an auto-restart. The Statistics page shows stalled runs with a red badge.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `HEALER_NODES_JSON` | *(required for overnight script)* | JSON array of `[node_id, ollama_url, [models]]` for the standalone `gap_healer_overnight.py` |
| `REQUEST_TIMEOUT` | `900` | Per-request timeout (seconds) for healer API calls |
| `MOE_API_BASE` | `http://localhost:8000` | MoE orchestrator base URL used by the healer subprocess |

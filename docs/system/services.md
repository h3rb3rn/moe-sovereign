# Docker Services Reference

> Sovereign MoE — Complete Service Documentation  
> Based on: `docker-compose.yml`

> **Note:** `litellm-proxy` has been removed from the stack (April 2026 — was never productively activated). The orchestrator communicates directly with the Ollama servers.

---

## Network Topology — Live

The diagram below shows the full internal container dependency graph. See [Webserver & Reverse Proxy](webserver.md) for the external Nginx layer.

## Service Overview

| Container | Image / Build | Host Port | Container Port | Function |
|---|---|---|---|---|
| `langgraph-orchestrator` | `./Dockerfile` | `8002` | `8000` | LangGraph orchestrator, FastAPI, OpenAI API |
| `mcp-precision` | `./mcp_server/Dockerfile` | `8003` | `8003` | MCP Precision Tools Server (26 deterministic tools) |
| `neo4j-knowledge` | `neo4j` (pinned SHA) | `7474`, `7687` | `7474`, `7687` | Knowledge graph (GraphRAG + ontology) |
| `terra_cache` | `valkey/valkey` (BSD 3-Clause, pinned SHA) | `6379` | `6379` | Valkey: caches, performance scores, session metadata |
| `terra_checkpoints` | `postgres:17-alpine` | (internal) | `5432` | Postgres: LangGraph `AsyncPostgresSaver` checkpoints |
| `chromadb-vector` | `chromadb/chroma` (pinned SHA) | `8001` | `8000` | Vector database for semantic caching |
| `moe-kafka` | `confluentinc/cp-kafka:7.7.0` | `9092` | `9092` | Kafka event streaming (KRaft, no Zookeeper) |
| `docker-socket-proxy` | `tecnavia/docker-socket-proxy:0.3.0` | `2375` | `2375` | Read-only Docker API proxy (security layer) |
| `moe-admin` | `./admin_ui/Dockerfile` | `8088` | `8088` | Admin UI: configuration, user management |
| `moe-prometheus` | `prom/prometheus` (pinned SHA) | `9090` | `9090` | Metrics scraper (90-day retention) |
| `moe-grafana` | `grafana/grafana` (pinned SHA) | `3001` | `3000` | Dashboards & visualization |
| `node-exporter` | `prom/node-exporter` (pinned SHA) | `9100` | `9100` | Host metrics (CPU, RAM, disk, network) |
| `cadvisor` | `gcr.io/cadvisor/cadvisor` (pinned SHA) | `9338` | `8080` | Container resource metrics |
| `moe-docs` | `squidfunk/mkdocs-material` (pinned SHA) | `8098` | `8000` | MkDocs documentation server |
| `moe-docs-sync` | custom Python image | — | — | Background sync agent (every 15 min: status, experts) |
| `moe-dozzle-init` | `python:3.12-alpine` | — | — | One-shot init: generates bcrypt `users.yml` for Dozzle |
| `moe-dozzle` | `amir20/dozzle` (pinned SHA) | `9999` | `8080` | Container log viewer with authentication |
| `moe-caddy` | `caddy` (pinned SHA) | `80`, `443` | `80`, `443` | Internal reverse proxy with TLS for docs/dozzle |

---

## Service Details

### `langgraph-orchestrator`

The core of the system. Processes all incoming requests through a LangGraph pipeline.

- **Build:** `./Dockerfile` (Python app)
- **Port:** `8002` (external) → `8000` (internal)
- **Dependencies:** `terra_cache`, `terra_checkpoints`, `chromadb-vector`, `mcp-precision`, `neo4j-knowledge`, `moe-kafka`
- **Configuration:** via `.env` file (env_file)

**Environment variables (fixed in compose):**

| Variable | Value |
|---|---|
| `REDIS_URL` | `redis://terra_cache:6379` |
| `POSTGRES_CHECKPOINT_URL` | `postgresql://langgraph:***@terra_checkpoints:5432/langgraph` |
| `CHROMA_HOST` | `chromadb-vector` |
| `CHROMA_PORT` | `8000` |
| `MCP_URL` | `http://mcp-precision:8003` |
| `NEO4J_URI` | `bolt://neo4j-knowledge:7687` |
| `NEO4J_USER` | `neo4j` |
| `NEO4J_PASS` | `moe-sovereign` |
| `KAFKA_URL` | `kafka://moe-kafka:9092` |
| `DB_PATH` | `/app/userdb/users.db` |

---

### `mcp-precision`

MCP Precision Tools Server with 16 deterministic calculation tools (no LLM involved).

- **Build:** `./mcp_server/Dockerfile`
- **Port:** `8003`
- **No external dependencies**

---

### `neo4j-knowledge`

Knowledge graph database for GraphRAG. Stores entities and relations extracted from system responses.

- **Image:** `neo4j:5-community`
- **Ports:** `7474` (browser UI), `7687` (Bolt protocol)
- **Heap:** initial 512 MB, max 1 GB
- **Pagecache:** 512 MB

---

### `terra_cache`

Valkey server (BSD 3-Clause, Linux Foundation fork of Redis). Serves as central cache for expert performance scores, plan cache, GraphRAG cache, user API key cache, and the `moe:active:*` live-request registry. **LangGraph checkpoints do not live here** — they are persisted to `terra_checkpoints` (Postgres) because `AsyncRedisSaver` requires the RediSearch module, which is unavailable on hosts without AVX2 CPU support.

- **Image:** `valkey/valkey` (BSD 3-Clause)
- **Port:** `6379`

---

### `terra_checkpoints`

Dedicated Postgres instance for LangGraph state persistence via `AsyncPostgresSaver`. Keeps the `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, and `checkpoint_migrations` tables. Isolated from any other Postgres workload (Authentik has its own instance).

- **Image:** `postgres:17-alpine`
- **Port:** `5432` (internal only)
- **Database:** `langgraph` / user `langgraph`
- **Healthcheck:** `pg_isready -U langgraph -d langgraph`

---

### `chromadb-vector`

Vector database for semantic caching of requests and responses.

- **Image:** `chromadb/chroma:latest`
- **Port:** `8001` (external) → `8000` (internal)
- **Telemetry:** disabled (`ANONYMIZED_TELEMETRY=FALSE`)

---

### `moe-kafka`

Kafka broker in KRaft mode (no separate Zookeeper required). Manages three topics for event streaming.

- **Image:** `confluentinc/cp-kafka:7.7.0`
- **Port:** `9092`
- **Mode:** KRaft (single broker)
- **Retention:** 7 days or 512 MB

---

### `docker-socket-proxy`

Security proxy for Docker socket access. Allows `moe-admin` to read container state
without granting unrestricted socket access.

- **Image:** `tecnavia/docker-socket-proxy:0.3.0`
- **Port:** `2375` (internal only)
- **Permissions:** `CONTAINERS=1`, `SERVICES=1`, `TASKS=1`, `NETWORKS=1` — all read-only
- **Security:** Prevents `moe-admin` from executing arbitrary Docker commands

---

### `moe-admin`

Web-based admin interface for system configuration and user management.

- **Build:** `./admin_ui/Dockerfile`
- **Port:** `8088`
- **Dependencies:** `langgraph-app`, `moe-prometheus`, `terra_cache`, `terra_checkpoints`, `docker-socket-proxy`
- **Networks:** `default` + `authentik_network` (for server-side OIDC token exchange)
- **Docker API:** accessed via `docker-socket-proxy:2375` (read-only, no direct socket mount)
- **Configuration:** reads and writes `/app/.env` (mounted rw)

---

### `moe-prometheus`

Metrics scraper. Collects metrics from the orchestrator, node exporter, and cAdvisor.

- **Image:** `prom/prometheus:latest`
- **Port:** `9090`
- **Retention:** 90 days
- **Configuration:** `/opt/deployment/moe-sovereign/moe-infra/prometheus/prometheus.yml`

---

### `moe-grafana`

Visualization dashboard based on Prometheus data.

- **Image:** `grafana/grafana:latest`
- **Port:** `3001` (external) → `3000` (internal)
- **Default dashboard:** `moe-operations.json`
- **Anonymous access:** disabled

---

### `node-exporter`

Exports host metrics (CPU, RAM, disk, network) for Prometheus.

- **Image:** `prom/node-exporter:latest`
- **Port:** `9100`
- **Access:** `pid: host`, bind-mounts on `/proc`, `/sys`, `/`

---

### `cadvisor`

Container resource metrics for Docker containers.

- **Image:** `gcr.io/cadvisor/cadvisor` (pinned SHA)
- **Port:** `9338` (external) → `8080` (internal)
- **Access:** privileged mode, bind-mounts on Docker directories

---

### `moe-docs`

Serves the MkDocs Material documentation site.

- **Image:** `squidfunk/mkdocs-material` (pinned SHA)
- **Port:** `8098` (external) → `8000` (internal)
- **Content:** `./docs/` directory (bind-mounted read-only)
- **Access:** proxied via `moe-caddy` internally; host Nginx optionally proxies `moe-caddy`

---

### `moe-docs-sync`

Background agent that periodically regenerates dynamic documentation pages.

- **Image:** custom Python build
- **Schedule:** runs every 15 minutes
- **Tasks:** updates system status page, syncs expert definitions, auto-generates status docs
- **Dependencies:** queries `langgraph-orchestrator` for live data

---

### `moe-dozzle-init`

One-time init container that generates the bcrypt-hashed `users.yml` file for Dozzle
authentication from Admin-configured credentials.

- **Image:** `python:3.12-alpine`
- **Lifecycle:** `restart: on-failure` — runs once, then exits
- **Output:** writes `/dozzle-users/users.yml` (shared volume with `moe-dozzle`)

---

### `moe-dozzle`

Web-based Docker log viewer with authentication.

- **Image:** `amir20/dozzle` (pinned SHA)
- **Port:** `9999` (external) → `8080` (internal)
- **Auth:** bcrypt `users.yml` generated by `moe-dozzle-init`
- **Access:** proxied via `moe-caddy`; external URL configurable as `LOG_URL` in Admin UI
- **Dependencies:** `moe-dozzle-init` (for users.yml)

---

### `moe-caddy`

Internal reverse proxy with automatic TLS for the docs/dozzle domain.

- **Image:** `caddy` (pinned SHA)
- **Ports:** `80`, `443`
- **Routes:** proxies to `moe-docs:8000` and `moe-dozzle:8080`
- **TLS:** Caddy automatic HTTPS (independent from host Let's Encrypt)
- **Dependencies:** `moe-docs`, `moe-dozzle`

---

## Network Topology

All services share the `moe-sovereign_default` Docker bridge network. `moe-admin` additionally
joins the external `authentik_network` for OIDC integration.

```mermaid
graph TD
    subgraph EXTERNAL["External (host-level Nginx + Internet)"]
        INET["🌐 Internet"]
        NGINX["Nginx (host-native)\nTLS/Let's Encrypt"]
        INET --> NGINX
    end

    subgraph CORE["Core Services"]
        ORCH["langgraph-orchestrator\n:8002"]
        MCP["mcp-precision\n:8003"]
    end

    subgraph ADMIN_BLOCK["Admin & Config"]
        ADMIN["moe-admin\n:8088"]
        DPROXY["docker-socket-proxy\n:2375 (read-only)"]
        ADMIN -->|"Docker API"| DPROXY
    end

    subgraph STORAGE["Data Storage"]
        REDIS["terra_cache\nValkey :6379"]
        PG["terra_checkpoints\nPostgres :5432"]
        CHROMA["chromadb-vector\n:8001"]
        NEO4J["neo4j-knowledge\n:7687/:7474"]
        KAFKA["moe-kafka\n:9092"]
    end

    subgraph OBS["Observability"]
        PROM["moe-prometheus\n:9090"]
        GRAF["moe-grafana\n:3001"]
        NODE["node-exporter\n:9100"]
        CADV["cadvisor\n:9338"]
    end

    subgraph DOCS_STACK["Docs & Logs"]
        CADDY["moe-caddy\n:80/:443"]
        DOCS["moe-docs\n:8098"]
        DOZZLE["moe-dozzle\n:9999"]
        SYNC["moe-docs-sync"]
        CADDY --> DOCS & DOZZLE
    end

    subgraph AUTHENTIK["External Network: authentik_network"]
        AUTK["Authentik\n(OIDC/SSO)"]
    end

    NGINX -->|":8002"| ORCH
    NGINX -->|":8088"| ADMIN
    NGINX -->|":80/:443"| CADDY

    ORCH --> REDIS & PG & CHROMA & NEO4J & KAFKA & MCP
    KAFKA -.->|"moe.ingest consumer"| ORCH

    ADMIN --> ORCH & PROM & REDIS & PG
    ADMIN -.->|"OIDC token exchange"| AUTK

    PROM -->|"scrape :8002/metrics"| ORCH
    PROM -->|"scrape :9100"| NODE
    PROM -->|"scrape :9338"| CADV
    GRAF --> PROM

    SYNC -->|"HTTP"| ORCH
```

---

## Volume Mounts

### Persistent Host Directories

| Service | Host Path | Container Path | Purpose |
|---|---|---|---|
| `moe-kafka` | `/opt/moe-infra/kafka-data` | `/var/lib/kafka/data` | Kafka log segments |
| `neo4j-knowledge` | `/opt/moe-infra/neo4j-data` | `/data` | Neo4j data |
| `neo4j-knowledge` | `/opt/moe-infra/neo4j-logs` | `/logs` | Neo4j logs |
| `terra_cache` | `/opt/moe-infra/redis-data` | `/data` | Valkey persistence (historical path name) |
| `terra_checkpoints` | `/opt/moe-infra/langgraph-checkpoints` | `/var/lib/postgresql/data` | Postgres PGDATA for LangGraph checkpoints |
| `chromadb-vector` | `/opt/moe-infra/chroma-data` | `/chroma/chroma` | ChromaDB vector data |
| `langgraph-orchestrator` | `/opt/moe-infra/agent-logs` | `/app/logs` | Orchestrator logs |
| `langgraph-orchestrator` | `/opt/moe-infra/chroma-onnx-cache` | `/root/.cache/chroma` | ChromaDB ONNX model cache |
| `moe-admin` | `/opt/moe-infra/admin-logs` | `/app/logs` | Admin UI logs |
| `moe-prometheus` | `/opt/moe-infra/prometheus-data` | `/prometheus` | Prometheus TSDB |
| `moe-grafana` | `/opt/grafana/data` | `/var/lib/grafana` | Grafana data |
| `moe-grafana` | `/opt/grafana/dashboards` | `/var/lib/grafana/dashboards` | Grafana dashboards |

### Configuration Files (Read-Only Mounts)

| Service | Host Path | Container Path |
|---|---|---|
| `langgraph-orchestrator` | `/opt/deployment/moe-sovereign/moe-infra/.env` | `/app/.env` (ro) |
| `moe-admin` | `/opt/deployment/moe-sovereign/moe-infra/.env` | `/app/.env` (rw) |
| `moe-prometheus` | `./prometheus/prometheus.yml` | `/etc/prometheus/prometheus.yml` (ro) |
| `moe-grafana` | `./grafana/provisioning` | `/etc/grafana/provisioning` (ro) |

### Shared Volumes (Docker Named Volumes)

| Volume | Mounted in | Path |
|---|---|---|
| `userdb_data` | `langgraph-orchestrator` | `/app/userdb` |
| `userdb_data` | `moe-admin` | `/app/userdb` |

> The `userdb_data` volume is a bind-mount on `/opt/moe-infra/userdb` and is shared between the orchestrator and admin UI (PostgreSQL user database).

### Skills Directory

| Service | Host Path | Container Path | Mode |
|---|---|---|---|
| `langgraph-orchestrator` | `${CLAUDE_SKILLS_DIR:-./skills}` | `/app/skills` | ro |
| `moe-admin` | `${CLAUDE_SKILLS_DIR:-./skills}` | `/app/skills` | rw |
| `moe-admin` | `./skills-upstream` | `/app/skills-upstream` | rw |

---

## Start and Stop Commands

### All Services

```bash
# Start
sudo docker compose up -d

# Stop (containers are not deleted)
sudo docker compose down

# Stop and remove volumes (data loss!)
sudo docker compose down -v

# Status of all services
sudo docker compose ps
```

### Rebuild Individual Services

```bash
# Rebuild and start orchestrator
sudo docker compose build langgraph-app
sudo docker compose up -d langgraph-app

# Rebuild and start MCP server
sudo docker compose build mcp-precision
sudo docker compose up -d mcp-precision

# Rebuild and start Admin UI
sudo docker compose build moe-admin
sudo docker compose up -d moe-admin

# Rebuild all custom-built services
sudo docker compose build langgraph-app mcp-precision moe-admin
sudo docker compose up -d langgraph-app mcp-precision moe-admin
```

### First Start (New Environment)

```bash
# Create directories
sudo mkdir -p /opt/moe-infra/{neo4j-data,neo4j-logs,redis-data,langgraph-checkpoints,chroma-data,chroma-onnx-cache,kafka-data,agent-logs,admin-logs,userdb,prometheus-data}
sudo mkdir -p /opt/grafana/{data,dashboards}

# Create .env from template and customize
cp /opt/deployment/moe-sovereign/moe-infra/temp.env /opt/deployment/moe-sovereign/moe-infra/.env
# → configure INFERENCE_SERVERS, SEARXNG_URL, EXPERT_MODELS, GF_SECURITY_ADMIN_PASSWORD

# Build and start all services
cd /opt/deployment/moe-sovereign/moe-infra
sudo docker compose up -d --build
```

---

## Useful Docker Commands for Operations

### Logs

```bash
# Live logs of a service
sudo docker compose logs -f langgraph-orchestrator
sudo docker compose logs -f moe-admin
sudo docker compose logs -f moe-kafka

# Last 100 lines
sudo docker logs langgraph-orchestrator --tail=100
sudo docker logs moe-admin --tail=50

# Kafka-specific logs from orchestrator
sudo docker logs langgraph-orchestrator 2>&1 | grep -i kafka
```

### Health Checks

```bash
# Orchestrator API
curl http://localhost:8002/v1/models

# GraphRAG status
curl http://localhost:8002/graph/stats

# MCP server
curl http://localhost:8003/health

# Valkey
sudo docker exec terra_cache valkey-cli ping

# Kafka topics
sudo docker exec moe-kafka kafka-topics \
  --bootstrap-server localhost:9092 --list

# Neo4j Browser
# → http://localhost:7474  (user: neo4j, pass: moe-sovereign)

# Prometheus
# → http://localhost:9090

# Grafana
# → http://localhost:3001
```

### Resources

```bash
# Container resource usage (live)
sudo docker stats

# Disk usage of volumes
du -sh /opt/moe-infra/*/
du -sh /opt/grafana/*/
```

### Troubleshooting

```bash
# Show container details
sudo docker inspect langgraph-orchestrator

# Open shell in container
sudo docker exec -it langgraph-orchestrator bash
sudo docker exec -it terra_cache valkey-cli

# Check network
sudo docker network ls
sudo docker network inspect moe-sovereign_default
```

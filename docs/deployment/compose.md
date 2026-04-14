# Docker Compose

Docker Compose is the **team profile default** and the fastest way to get a
complete MoE Sovereign stack running on a single host. The same
`docker-compose.yaml` that the development team uses is production-ready.

!!! danger "Firewall is mandatory in production"
    The compose stack publishes ports `8002` (orchestrator), `8003` (MCP),
    `8088` (admin UI), `8098` (ChromaDB) and `3001` (Grafana) on `0.0.0.0` —
    they are reachable from any host that can route to this machine.
    Several of these endpoints have **no authentication** (e.g.
    `POST /graph/knowledge/import`) and assume network-level isolation.
    Configure your host firewall to expose only `80/443` to the public
    internet. See [Firewall & Network Exposure](firewall.md) for
    ready-to-run UFW / firewalld / iptables rules.

## What the stack contains

```mermaid
flowchart TB
    subgraph CORE[Core services]
        O[langgraph-app<br/>:8002 → :8000]
        M[mcp-precision<br/>:8003]
        A[moe-admin<br/>:8088]
    end
    subgraph DATA[Data tier]
        PG[(terra_checkpoints<br/>postgres 17)]
        VK[(terra_cache<br/>valkey)]
        CH[(chromadb-vector<br/>:8001)]
        NJ[(neo4j-knowledge<br/>:7474/:7687)]
        KF[(moe-kafka<br/>KRaft :9092)]
    end
    subgraph OBS[Observability]
        PR[(moe-prometheus<br/>:9090)]
        GR[(moe-grafana<br/>:3001)]
        NE[(node-exporter)]
        CA[(cadvisor)]
    end
    subgraph EDGE[Edge]
        CD[moe-caddy<br/>:80/:443]
        DZ[moe-dozzle<br/>:9999]
    end

    CD --> O & A
    O --> PG & VK & CH & NJ & KF & M
    A --> PG & VK
    PR -- scrape --> O & NE & CA
    GR --> PR

    classDef core fill:#eef2ff,stroke:#6366f1;
    classDef data fill:#fef3c7,stroke:#d97706;
    classDef obs  fill:#ecfdf5,stroke:#059669;
    classDef edge fill:#fce7f3,stroke:#db2777;
    class O,M,A core;
    class PG,VK,CH,NJ,KF data;
    class PR,GR,NE,CA obs;
    class CD,DZ edge;
```

## Launch

```bash
# team profile (all 19 services)
sudo docker compose up -d

# solo profile — smaller resource footprint, optional services off
sudo docker compose -f docker-compose.yaml -f docker-compose.solo.yaml up -d

# enterprise profile — omit data-tier services, connect to external clusters
sudo docker compose -f docker-compose.yaml -f docker-compose.enterprise.yaml up -d
```

The profile overrides are additive: Compose merges the base file with the
profile-specific file, so you never maintain two parallel copies.

## Rebuilding after code changes

Per project policy (see `CLAUDE.md`):

```bash
sudo docker compose build langgraph-app && sudo docker compose up -d langgraph-app
sudo docker compose build moe-admin    && sudo docker compose up -d moe-admin
sudo docker compose build mcp-precision && sudo docker compose up -d mcp-precision
```

The Dockerfiles are multi-stage, so rebuilds only redo the layers that
actually changed — a typical `main.py` edit rebuilds in under 10 seconds.

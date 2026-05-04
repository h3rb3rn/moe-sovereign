# UI Screenshots

Screenshots of all MoE Sovereign web interfaces. Generated automatically via Playwright.

---

## Admin UI

> All screenshots use the current grouped navigation bar (Konfiguration · Monitoring · Infra · Tools · Users).

### Login

![Admin Login](../assets/screenshots/admin_login.png)

### Dashboard — Server Status

Live inference-node status table with URL, VRAM, GPU count, API type, token and activity indicators.

![Admin Dashboard](../assets/screenshots/admin_dashboard.png)

### System Monitoring

Six knowledge-stack gauges, LLM server status cards for all inference nodes, and Chart.js widgets for token usage, cache hit rate, expert calls, and latency percentiles.

![System Monitoring](../assets/screenshots/admin_monitoring_system.png)

### Live Monitoring — Active Processes

Real-time request table (5 s auto-refresh). User and IP columns are privacy-blurred.

![Live Monitoring — Active Processes](../assets/screenshots/admin_live_monitoring_active.png)

### Live Monitoring — LLM Instances

Per-node cards with loaded models, VRAM allocation, Ollama metrics, and the expandable model list.

![Live Monitoring — LLM Instances](../assets/screenshots/admin_live_monitoring_llm.png)

### Starfleet — Ambient Intelligence Dashboard

LCARS-style dashboard with live node grid, active alert counters, Starfleet feature toggles, and Watchdog alert feed.

![Starfleet Dashboard](../assets/screenshots/admin_starfleet.png)

### Pipeline Transparency Log

Per-request routing metadata with User / Model / Mode / Complexity / Expert-Domain badges.
Filterable, sortable, CSV-exportable. Data blurred for privacy.

![Pipeline Log](../assets/screenshots/admin_pipeline_log.png)

### Expert Templates

![Expert Templates](../assets/screenshots/admin_expert_templates.jpg)

### Claude Code Profiles

![Profiles](../assets/screenshots/admin_profiles.jpg)

### Users & Roles

![Users](../assets/screenshots/admin_users.jpg)

### Inference Servers

![Servers](../assets/screenshots/admin_servers.jpg)

### Tool Evaluation Log

![Tool Eval](../assets/screenshots/admin_tool_eval.jpg)

### MCP Precision Tools

![MCP Tools](../assets/screenshots/admin_mcp_tools.jpg)

### Skills

![Skills](../assets/screenshots/admin_skills.png)

---

## Grafana Dashboards

### MoE System Overview

![Grafana MoE Overview](../assets/screenshots/grafana_moe_system_overview.png)

### LLM & Expert Usage

![Grafana LLM Usage](../assets/screenshots/grafana_llm_usage.png)

### Knowledge Base Health

Neo4j entity count, relation count, gap-queue depth, and per-template healing throughput.

![Grafana Knowledge Base](../assets/screenshots/grafana_knowledge_base_health.png)

### GPU & Inference Nodes

Real-time GPU utilisation, VRAM occupancy, and inference throughput across all heterogeneous nodes. Captured in kiosk mode.

![Grafana GPU Nodes](../assets/screenshots/grafana_gpu_nodes.png)

### User Metrics

![Grafana User Metrics](../assets/screenshots/grafana_user_metrics.png)

### Grafana Home

![Grafana Home](../assets/screenshots/grafana_home.png)

---

## Prometheus

### Scrape Targets

All configured scrape jobs with health status (UP/DOWN) and last-scrape timing.

![Prometheus Targets](../assets/screenshots/prometheus_targets.png)

---

## Dozzle — Log Viewer

Container log aggregation across all MoE Sovereign services, accessible without SSH access.

![Dozzle](../assets/screenshots/dozzle.png)

## Neo4j — Knowledge Graph

Neo4j Browser showing entity subgraph: nodes (Framework, Concept, Protocol, Tool) and typed relations (IS_A, USES, IMPLEMENTS, PART_OF).

!!! note "Optional component"
    Neo4j is optional since v2026.05. Skip it during `install.sh` for lightweight VMs
    that only need expert templates and CC profiles.

![Neo4j Knowledge Graph](../assets/screenshots/neo4j-knowledge-graph.png)

---

## MkDocs Documentation

### Home

![Docs Home](../assets/screenshots/docs_home.png)

### Architecture Page

![Docs Architecture](../assets/screenshots/docs_architecture.png)

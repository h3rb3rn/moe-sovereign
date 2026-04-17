# UI Screenshots

Screenshots of all MoE Sovereign web interfaces. Generated automatically via Playwright.

---

## Admin UI

### Login

![Admin Login](../assets/screenshots/admin_login.png)

### Dashboard — Full Overview (with Advanced Pipeline Settings)

Full-page screenshot of the Admin UI dashboard including all configuration sections:
server tiles, model routing, SMTP, OIDC, CORS, and the expanded Advanced Pipeline Settings block.
All sensitive fields (URLs, credentials, API keys, server names) are privacy-blurred.

![Admin Overview](../assets/screenshots/moe-admin-overview.png)

### Live Monitoring

Full-page screenshot of the monitoring view showing per-node GPU utilisation, inference
throughput, memory occupancy, and the gap-healer slot counters. Server tiles wait for
the background health-check to complete before capture.

![Admin Monitoring](../assets/screenshots/moe-admin-monitoring.png)

### Dashboard — System Configuration

![Admin Dashboard](../assets/screenshots/admin_dashboard.jpg)

### Users & Roles

![Users](../assets/screenshots/admin_users.jpg)

### Expert Templates

![Expert Templates](../assets/screenshots/admin_expert_templates.jpg)

### Claude Code Profiles

![Profiles](../assets/screenshots/admin_profiles.jpg)

### Inference Servers

![Servers](../assets/screenshots/admin_servers.jpg)

### System Monitoring (legacy)

![Monitoring](../assets/screenshots/admin_monitoring.png)

### Live Monitoring — Process History

![Live Monitoring](../assets/screenshots/admin_live_monitoring.jpg)

### MCP Precision Tools

![MCP Tools](../assets/screenshots/admin_mcp_tools.jpg)

### Skills

![Skills](../assets/screenshots/admin_skills.png)

### Tool Evaluation Log

![Tool Eval](../assets/screenshots/admin_tool_eval.jpg)

---

## Grafana Dashboards

### MoE System Overview

![Grafana MoE Overview](../assets/screenshots/grafana_moe_system_overview.jpg)

### LLM & Expert Usage

![Grafana LLM Usage](../assets/screenshots/grafana_llm_&_expert_usage.jpg)

### Knowledge Base Health

![Grafana Knowledge Base](../assets/screenshots/grafana_knowledge_base_health.jpg)

### Infrastructure & Resources

![Grafana Infrastructure](../assets/screenshots/grafana_infrastructure_&_resources.png)

### User Metrics

![Grafana User Metrics](../assets/screenshots/grafana_moe_-_user_metriken.jpg)

### Dashboard List

![Grafana Dashboard List](../assets/screenshots/grafana_dashboards.jpg)

---

## Prometheus

### Query Interface

![Prometheus Home](../assets/screenshots/prometheus_home.png)

### Scrape Targets

![Prometheus Targets](../assets/screenshots/prometheus_targets.jpg)

---

## Grafana — GPU & Inference Nodes

Real-time GPU utilisation, VRAM occupancy, and per-model inference throughput across all
heterogeneous nodes (RTX 4090, Tesla M60, Tesla M10, GT 1060). Captured in kiosk mode.

![Grafana GPU & Inference Nodes](../assets/screenshots/grafana-gpu-nodes.png)

## Grafana — Knowledge Base Health

Neo4j entity count, relation count, gap-queue depth (`moe:ontology_gaps` ZCARD),
and per-template healing throughput over the last 24 hours.

![Grafana Knowledge Base Health](../assets/screenshots/grafana-knowledge.png)

## Dozzle — Log Viewer

Container log aggregation across all MoE Sovereign services, accessible without
SSH access. Useful for real-time debugging during ontology gap healing runs.

![Dozzle](../assets/screenshots/dozzle.png)

## Neo4j — Knowledge Graph (500+ Entities)

Neo4j Browser showing a 500-entity subgraph excerpt: entities (Framework, Concept,
Protocol, Tool) and their typed relations (IS_A, USES, IMPLEMENTS, PART_OF). The
graph is the product of autonomous healing over multiple sessions.

![Neo4j Knowledge Graph](../assets/screenshots/neo4j-knowledge-graph.png)

---

## MkDocs Documentation

### Home

![Docs Home](../assets/screenshots/docs_home.jpg)

### Architecture Page

![Docs Architecture](../assets/screenshots/docs_architecture.jpg)

### Webserver & Reverse Proxy

![Docs Webserver](../assets/screenshots/docs_webserver.jpg)

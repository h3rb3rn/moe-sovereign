# Admin Backend – Overview

The Admin Backend (`http://localhost:8088`) is the central control panel for the Sovereign MoE cluster. It is accessible to administrators only.

## Login

![Admin Login](../assets/screenshots/admin_login.png)

Administrators can authenticate via local credentials or via SSO (Authentik). The login form supports CSRF protection.

## Dashboard

![Admin Dashboard](../assets/screenshots/admin_dashboard.jpg)

## Navigation

The navbar groups all sections into dropdown menus, visible from ≥992 px screen width.
On smaller screens a hamburger menu opens the full offcanvas sidebar.

### Direct links

| Menu Item | Path | Function |
|-----------|------|---------|
| Config | `/` | System configuration, container status |

### Monitoring (dropdown)

| Menu Item | Path | Function |
|-----------|------|---------|
| Monitoring | `/monitoring` | Prometheus metrics, server status |
| Live Monitoring | `/live-monitoring` | Active processes, process kill, LLM instances |
| Statistics | `/statistics` | Token and cost statistics |
| Benchmarks | `/benchmarks` | GAIA / LongMemEval benchmark runs |

### Infra (dropdown)

| Menu Item | Path | Function |
|-----------|------|---------|
| Servers | `/servers` | Configure inference servers |
| Knowledge | `/knowledge` | Neo4j knowledge graph management |
| Federation | `/federation` | Multi-tenant cluster federation |
| Quarantine | `/quarantine` | Blocked content review |
| Maintenance | `/maintenance` | System maintenance tasks |

### Tools (dropdown)

| Menu Item | Path | Function |
|-----------|------|---------|
| CC Profiles | `/profiles` | Claude Code profiles |
| Skills | `/skills` | Manage slash commands |
| MCP Tools | `/mcp-tools` | Enable/disable precision tools |
| Tool Eval | `/tool-eval` | Tool usage evaluation log |
| Templates | `/templates` | Manage expert configurations |

### Users (dropdown)

| Menu Item | Path | Function |
|-----------|------|---------|
| Users | `/users` | User CRUD, budgets, permissions, API keys |
| Teams | `/teams` | Team and tenant management |
| User Content | `/user-content` | All user templates and profiles |

## Dashboard – Global Configuration

The Admin Dashboard (`/`) allows changing system-wide settings. Changes are written to the `.env` file via `POST /save`.

### Configurable Fields

| Field | Env Variable | Meaning |
|-------|-------------|---------|
| Token Price | `TOKEN_PRICE_EUR` | EUR per token for cost calculation (default: `0.00002`) |
| Expert Models | `EXPERT_MODELS` | JSON array: model assignments per category |
| Inference Servers | `INFERENCE_SERVERS` | JSON array: server configurations |
| Claude Code URL | `CC_API_BASE` | Base URL for Claude Code API |
| Planner Model | `PLANNER_MODEL` | Global default planner LLM |
| Judge Model | `JUDGE_MODEL` | Global default judge LLM |
| SMTP | `SMTP_HOST` etc. | Email configuration for user notifications |

### Container Status

The dashboard shows the live status of these Docker containers:

- `langgraph-orchestrator`
- `moe-kafka`
- `neo4j-knowledge`
- `mcp-precision`
- `terra_cache` (Valkey)
- `chromadb-vector`

## Inference Servers

![Inference Servers](../assets/screenshots/admin_servers.jpg)

## Skills

![Skills Management](../assets/screenshots/admin_skills.png)

## Tool Evaluation Log

![Tool Eval](../assets/screenshots/admin_tool_eval.jpg)

## Cluster Impact

Every change made via the Admin Backend takes effect immediately:

| Action | Immediate Effect |
|--------|-----------------|
| Change token price | Applies immediately to all new requests |
| Change inference servers | New requests use the updated server list |
| Toggle profile | Orchestrator is restarted |
| Suspend user | Valkey cache is immediately invalidated |
| Revoke permission | Next API request from the user will be rejected |
| Set budget | Valkey counter is validated against the new limit |

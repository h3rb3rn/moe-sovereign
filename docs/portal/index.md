# User Portal — Overview

The User Portal (`/user/dashboard`) is the self-service interface for end users. It enables management of API keys, viewing token consumption and costs, and creation of custom expert templates and CC profiles (for `expert` users).

## Access

| Method | URL | Description |
|---------|-----|-------------|
| Direct login | `/user/login` | Username + password |
| Forgot password | `/user/forgot-password` | Email-based reset link (1h TTL) |

The User Portal is fully separated from the Admin backend. There is no OIDC support for the User Portal.

## Sidebar Navigation

| Menu item | Available for | Description |
|-----------|--------------|-------------|
| Dashboard | All | Token consumption, budget status, active keys |
| Billing | All | Detailed cost breakdown |
| Usage History | All | All API requests with filters |
| **Audit Log** | **All** | **Full conversation history: prompts, responses, metadata. Configurable retention per user.** |
| API Keys | All | Create and manage keys |
| My Templates | `expert`, `admin` | Custom expert templates |
| CC Profiles | `expert`, `admin` | Custom Claude Code profiles |
| Profile & Password | All | Account settings |

## Status Indicators in the Sidebar

- **Active CC Profile** badge (green when a profile is active)
- **Assigned Templates** (list of admin-assigned templates)
- **Budget progress bar** (daily / monthly / total)

## Impersonation Banner

When an admin impersonates a user, an **orange banner** appears at the top of every page:

```
You are logged in as: [Username] (Admin Impersonation)  [Exit]
```

Clicking **Exit** → `GET /user/impersonate/exit` → returns to the admin account.

## Profile & Password

| Field | Editable | Description |
|------|------------|-------------|
| Username | No | Read-only |
| Display name | Yes | Friendly name |
| Email | Yes | For alerts and reset |
| Password | Yes | Leave empty = no change |
| Timezone | Yes | Offset in hours (−12 to +14) |

The timezone affects the display of timestamps in the usage history.

## Audit Log

The Audit Log (`/user/audit-log`) gives every user full visibility into their own conversation history.

### What is logged

Each completed API request is stored as a JSONL entry containing:

| Field | Content |
|---|---|
| `ts` | ISO-8601 UTC timestamp |
| `request_id` | MoE chat ID |
| `model` | Model name used |
| `moe_mode` | Routing mode (standard, research, code, …) |
| `messages` | Full input message list (all roles) |
| `response` | Full assistant response text |
| `prompt_tokens` / `completion_tokens` | Token counts |
| `latency_ms` | End-to-end response time |
| `expert_domains` | Routing categories engaged |
| `cache_hit` | Whether a semantic cache hit occurred |
| `agentic_rounds` | Number of agentic loop iterations |

### Filters & Export

- **Date range filter** (from / to)
- **Full-text search** across prompts and responses
- **CSV and JSON export** of the current filtered view
- **Click-to-expand modal** showing the complete prompt and response with copy button

### Retention

Users can configure their own retention period (in days) in the Audit Log page. The admin sets the global default and the maximum allowed value. Rotated log files beyond the configured retention are deleted automatically once per day.

| Setting | Environment variable | Default |
|---|---|---|
| Enable/disable logging | `CONVERSATION_LOG_ENABLED` | `true` |
| Global default retention | `CONVERSATION_LOG_RETENTION_DAYS_DEFAULT` | `90` |
| Maximum user-configurable retention | `CONVERSATION_LOG_RETENTION_MAX` | `365` |
| Log directory (container path) | `CONVERSATION_LOG_DIR` | `/app/logs/users` |

Log files are rotated daily by logrotate (`/etc/logrotate.d/moe-user-audit`) and stored at `${MOE_DATA_ROOT}/user-audit-logs/` on the host.

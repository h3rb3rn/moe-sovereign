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

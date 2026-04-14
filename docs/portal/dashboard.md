# Dashboard, Billing & Usage History

## Dashboard (`/user/dashboard`)

The dashboard provides a compact overview of token consumption and remaining budget.

### Budget Cards (3 total)

A card is displayed for each budget period:

| Element | Description |
|---------|-------------|
| **Title** | Today / This Month / Total |
| **Consumed** | Number of tokens used |
| **Limit** | Configured limit or ∞ (unlimited) |
| **Progress bar** | Percentage consumed, color-coded |
| **Percent** | Exact percentage value |
| **Reset countdown** | When the limit resets |

**Progress bar color coding:**

| Color | Range | Meaning |
|-------|---------|-----------|
| Green | < 70% | Normal |
| Yellow | 70–90% | Warning |
| Red | > 90% | Critical |

Cards refresh automatically every **30 seconds** via `GET /user/api/budget`.

### 14-Day Usage Chart

Bar chart (Canvas) with daily token consumption for the last 14 days. Also updated every 30 seconds.

### Active API Keys

Compact table of currently active API keys:

| Column | Description |
|--------|-------------|
| Key prefix | First 16 characters (e.g. `moe-sk-abc123...`) |
| Label | User-assigned name |
| Last used | Timestamp of last use |

Quick link: **Manage keys** → `/user/keys`

### My Expert Configuration

Shows the user's active expert template with per-category breakdown:

- Category name
- Assigned models with endpoint
- Lock icon = Required (mandatory)
- Shuffle icon = Optional (two-tier fallback)
- Cost factor of the template (if ≠ 1.0)

---

## Billing (`/user/billing`)

The billing page provides complete cost transparency.

### Budget Status Table

| Column | Description |
|--------|-------------|
| Period | Daily / Monthly / Total |
| Consumed | Tokens in this period |
| Limit | Configured limit (or ∞) |
| Remaining | Tokens still available |
| Progress | Visualized consumption |
| Reset | When the period resets |
| Type | Budget type badge (Subscription / One-time) |

### Consumption by Model (last 30 days)

Table with breakdown by model and mode:

| Column | Description |
|--------|-------------|
| Model | LLM model name |
| Mode | MoE mode |
| Tokens | Tokens consumed |
| Requests | Number of API calls |

### Summary Cards

| Card | Description |
|-------|-------------|
| Total requests | Lifetime API calls |
| Total tokens | Lifetime token consumption |
| Tokens this month | Consumption in the current month |

### Cost Calculation

All token figures are converted to EUR:

```
Cost = token consumption × TOKEN_PRICE_EUR × cost factor
```

The TOKEN_PRICE_EUR is configured by the admin (default: 0.00002 EUR/token).

### Configure Budget Alerts

At the bottom of the billing page:

| Setting | Description | Default |
|------------|-------------|---------|
| Enable alerts | On/Off toggle | Off |
| Threshold | Percentage trigger threshold | 80% |
| Alert email | Recipient address | Account email |

Save: `POST /user/alerts`

---

## Usage History (`/user/usage`)

Detailed listing of all API requests with extensive filter options.

### Time Range Selection

Dropdown: **7 days** / **30 days** / **90 days**

### Filter Options

| Filter | Description |
|--------|-------------|
| Token name | Search by key label or key prefix |
| Mode | Dropdown of all used MoE modes |
| Status | All / `ok` / `error` |
| From / To | Date range selection |
| Reset | Clear all filters |

### Usage Table

| Column | Description |
|--------|-------------|
| Timestamp | Date + time (in user timezone, HH:MM) |
| Model / Template | Model name; note icon when note is present |
| API Key | Label badge or shortened prefix |
| Mode | Colored mode badge |
| Prompt | Input tokens |
| Completion | Output tokens |
| Total | Sum (bold) |
| Status | `ok` (green) or error code (red) |
| ✎ | Edit note |

### Notes Feature

Each request can be annotated with a personal note:

1. Click the note icon in the row → modal opens
2. Enter free text (or leave empty to delete)
3. **Save** → `PUT /user/api/usage/{usage_id}/note`
4. **Delete** → Removes the existing note

Notes are only visible to the respective user. Admins can view all notes in the database.

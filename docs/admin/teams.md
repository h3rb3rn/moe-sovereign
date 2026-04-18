# Teams & Tenants

The **Teams & Tenants** section (`/teams`) manages the hierarchical knowledge governance model. It controls which users share access to AI-generated knowledge and how knowledge can be promoted through organizational layers.

## Knowledge Hierarchy

MoE Sovereign organizes knowledge across four levels:

```
User (private)  →  Team  →  Tenant (Mandant)  →  Global
user:{id}           team:{id}   tenant:{id}          NULL
```

Knowledge created during a conversation is assigned to the user's **private namespace** (`user:{id}`) by default. It remains invisible to all other users until explicitly promoted by a privileged role.

| Level | Namespace Pattern | Visible to |
|-------|------------------|-----------|
| Private | `user:{user_id}` | Only the creating user |
| Team | `team:{team_id}` | All team members |
| Tenant | `tenant:{tenant_id}` | All tenant members |
| Global | *(null)* | Everyone |

!!! info "How Namespaces Work"
    When a user is added to a team, the system automatically grants a `graph_tenant` permission for `team:{team_id}`. This means the Neo4j GraphRAG engine includes that team's knowledge in all future queries for that user — without any code changes required.

---

## Teams

### Creating a Team

Click **Team erstellen** and fill in:

| Field | Required | Description |
|-------|----------|-------------|
| Name | ✓ | Display name (e.g. "Backend Engineering") |
| Slug | ✓ | URL-safe identifier, auto-filled from name |
| Tenant | – | Optionally assign the team to a Mandant |

When a team is assigned to a tenant, all members of that team automatically receive access to the tenant's knowledge namespace as well.

### Team Roles

| Role | Capabilities |
|------|-------------|
| `member` | Reads team knowledge; contributes own knowledge to team via promotion |
| `lead` | All member capabilities + can promote team knowledge to tenant namespace |

### Adding Members

Click **Mitglied hinzufügen** on a team card. Enter the **User ID** (e.g. `usr_abc123`) and select a role.

!!! tip "Finding User IDs"
    User IDs are shown on the [Users page](/users) next to each user's name.

When a member is added:
- A `graph_tenant: team:{id}` permission is granted automatically
- The user's Valkey/Redis cache is invalidated so the new namespace takes effect immediately (next request)

### Team Budget

Each team can have a **shared token budget**. When a team budget is configured:

- Every request by a team member checks the team's remaining budget **before** routing to any LLM
- Tokens are deducted from both the individual user's budget and the team pool
- If the team pool is exhausted, requests return HTTP 429 regardless of individual user limits

Configure budgets via the wallet icon (⊙) on the team card.

| Field | Description |
|-------|-------------|
| Monthly limit | Max tokens per calendar month across all team members |
| Daily limit | Max tokens per calendar day across all team members |

---

## Tenants (Mandanten)

Tenants represent organizational units (e.g. a client, department, or subsidiary). A tenant groups multiple teams under a shared knowledge namespace.

### Creating a Tenant

Click **Mandant erstellen**. Provide a name and slug. The tenant gets a unique `tenant:{id}` namespace in Neo4j.

### Tenant Roles

| Role | Capabilities |
|------|-------------|
| `member` | Reads tenant knowledge |
| `admin` | All member capabilities + can promote tenant knowledge to Global |

### Adding Members

A tenant can receive members in two ways:

- **Direct user**: Add a single user directly with a role
- **Entire team**: Add all current members of a team at once — each team member receives `graph_tenant: tenant:{id}`

!!! warning "Team membership changes after adding"
    Users added to a team **after** the team was added to a tenant do **not** automatically receive tenant access. Add them directly to the tenant, or remove and re-add the team membership.

---

## Promoting Knowledge

The **Wissen promoten** tab transfers entities from one namespace to the next level in the hierarchy.

### Who Can Promote?

| Action | Required Role |
|--------|--------------|
| Own namespace → any team/tenant | Any authenticated user |
| Team namespace → tenant or global | Team **lead** |
| Tenant namespace → global | Tenant **admin** |
| Any → any | Platform **admin** |

### How to Promote

1. Enter the **source namespace** (e.g. `user:abc123` or `team:xyz`)
2. Enter the **target namespace** (e.g. `team:xyz`, `tenant:def456`, or leave empty for Global)
3. Optionally list comma-separated **entity names** to promote only specific knowledge
4. Click **Promoten** and confirm the dialog

The number of updated Neo4j entities is shown in the result.

!!! danger "Promotion is not reversible via the UI"
    Once entities are promoted to a wider namespace, they are visible to all members of that namespace immediately. To revert, use the [Knowledge Management](/knowledge) page or the Neo4j browser directly.

---

## Technical Reference

### Permission Flow

```
add_team_member(team_id, user_id)
  → INSERT team_memberships
  → grant_permission(user_id, "graph_tenant", "team:{team_id}")
  → sync_user_to_redis(user_id)          # invalidates Valkey cache
```

### GraphRAG Query-Time Filtering

Every GraphRAG context query receives the user's `tenant_ids` list:

```
tenant_ids = ["user:{id}", "team:{t1}", "tenant:{t2}"]
```

The Cypher query filters accordingly:

```cypher
AND (e.tenant_id IN $tenant_ids OR e.tenant_id IS NULL)
```

Global entities (`tenant_id IS NULL`) are always included.

### Database Tables

| Table | Purpose |
|-------|---------|
| `teams` | Team definitions with optional tenant assignment |
| `team_memberships` | User ↔ Team with role |
| `tenants` | Tenant (Mandant) definitions |
| `tenant_memberships` | User or Team ↔ Tenant with role |
| `team_budgets` | Shared token pool per team |

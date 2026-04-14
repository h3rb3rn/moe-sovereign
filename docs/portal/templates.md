# Custom Templates & CC Profiles (Expert Users)

Users with the `expert` or `admin` role can create custom expert templates and Claude Code profiles in the User Portal. These supplement the configurations assigned by the admin.

!!! info "Prerequisite"
    For custom templates and profiles, the user requires the `expert` (or `admin`) role and access to at least one `model_endpoint`.

---

## Custom Expert Templates (`/user/templates`)

### Why custom templates?

Custom templates allow users to define specialized LLM configurations for their use cases — without admin intervention. The template applies exclusively to their own account.

### Create / Edit a template

Same fields as in the Admin backend (see [Expert Templates](../admin/templates.md)), with one restriction: only endpoints assigned via `model_endpoint` permissions are available as **inference servers**.

### Template management

| Action | Description |
|--------|-------------|
| Create | Modal with form → `POST /user/api/templates` |
| Edit | Edit modal → `PUT /user/api/templates/{id}` |
| Copy | "Copy as template" → new modal with pre-filled values |
| Delete | Confirmation → `DELETE /user/api/templates/{id}` |
| Activate / Deactivate | Toggle → template is/is not used for API requests |

### Import / Export

```
My Templates → Export button  →  expert_templates_user.json
My Templates → Import button  →  upload JSON
```

**Import modes:** `merge` (skip entries with matching names) or `replace` (overwrite).

### Database schema

```sql
CREATE TABLE user_expert_templates (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    cost_factor REAL DEFAULT 1.0,
    config_json TEXT NOT NULL,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

The `cost_factor` field acts as a token multiplier (1.0 = no surcharge).

---

## Custom CC Profiles (`/user/cc-profiles`)

### Why custom profiles?

Custom CC profiles allow users to individually control the MoE mode, tool model, and other settings for their Claude Code session — independently of the active admin profile.

### Create / Edit a profile

Same fields as in the Admin backend (see [Claude Code Profiles](../admin/profiles.md)).

Restriction: Only endpoints from the user's own `model_endpoint` permissions are available as **tool endpoints**.

### Profile management

| Action | Description |
|--------|-------------|
| Create | Modal → `POST /user/api/cc-profiles` |
| Edit | Edit modal → `PUT /user/api/cc-profiles/{id}` |
| Delete | Confirmation → `DELETE /user/api/cc-profiles/{id}` |
| Activate / Deactivate | Toggle |

### Import / Export

```
CC Profiles → Export button  →  cc_profiles_user.json
CC Profiles → Import button  →  upload JSON
```

**Import modes:** `merge` or `replace`.

### Database schema

```sql
CREATE TABLE user_cc_profiles (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

---

## Admin View: User Content (`/user-content`)

Admins can view and delete all user templates and profiles under `/user-content`:

| Feature | Description |
|---------|-------------|
| All user templates | List with creator info |
| All user CC profiles | List with creator info |
| Admin delete | `DELETE /api/admin/user-templates/{id}` / `DELETE /api/admin/user-cc-profiles/{id}` |

---

## Import / Export — Complete JSON Schemas

Complete schemas and examples → [Import & Export](../reference/import-export.md)

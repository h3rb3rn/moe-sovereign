# Authentication & Authorization

The MoE system distinguishes two completely separate authentication levels:

| Level | Entry point | Users |
|-------|-------------|--------|
| **Admin backend** | `/login` | Admins (with `is_admin = 1`) |
| **User portal** | `/user/login` | End users (all roles) |

---

## Admin Authentication

### Local login

1. Fill in form: **username + password + CSRF token**
2. Password check: bcrypt comparison via passlib
3. `is_admin = 1` must be set — regular users are rejected
4. Session is created:
   ```python
   session["authenticated"] = True
   session["user"] = username
   session["admin_user_id"] = user_id
   ```

### OIDC / Authentik (optional)

Requires the following environment variables:

| Variable | Description |
|----------|-------------|
| `AUTHENTIK_URL` | Base URL of the Authentik instance |
| `OIDC_CLIENT_ID` | OAuth2 client ID |
| `OIDC_CLIENT_SECRET` | OAuth2 client secret |

**Flow:**

```mermaid
flowchart TD
    A[Admin opens /login] --> B["Button: Login with Authentik (SSO)"]
    B --> C["GET /auth/login → redirect to<br/>Authentik /application/o/authorize/<br/>Scopes: openid profile email groups"]
    C --> D[User authenticates with Authentik]
    D --> E["GET /auth/callback?code=...<br/>token exchange /application/o/token/"]
    E --> F["Admin check:<br/>user must be in group moe-admins<br/>OR is_superuser=true"]
    F --> G[Create session + store OIDC token]
```

**Admin check for OIDC:**
```python
is_admin = "moe-admins" in userinfo.get("groups", []) or userinfo.get("is_superuser", False)
```

### Logout

- Local login: delete session
- OIDC: delete session + redirect to Authentik `end-session/` endpoint

---

## User Portal Authentication

### Login

Form at `/user/login`:

1. Enter username + password
2. bcrypt password comparison
3. `is_active = 1` is checked (blocked users are rejected)
4. Session:
   ```python
   session["user_authenticated"] = True
   session["user_id"] = user_id
   session["username"] = username
   session["user_role"] = role
   ```

### Password Reset Flow

```mermaid
flowchart TD
    A["/user/forgot-password<br/>Enter email address"]
    A --> B["Generate token<br/>secrets.token_urlsafe(32)"]
    B --> C["Store token hash in DB:<br/>password_reset_tokens<br/>(TTL 1 hour, single-use)"]
    C --> D["Send email (if SMTP configured)"]
    D --> E["/user/reset-password?token=...<br/>Validate token (not expired, not yet used)"]
    E --> F["Enter new password (min. 8 characters)"]
    F --> G[Store bcrypt hash]
    G --> H[Mark token as used]
```

!!! info "Security behavior"
    At `/user/forgot-password`, the system always shows the message "If an account exists..." — regardless of whether the email is actually registered. This prevents account enumeration.

---

## API Key Authentication

API keys are used for API requests to the orchestrator. No session cookie, no login — stateless.

### Supported Headers

```
Authorization: Bearer moe-sk-{48 hex chars}
x-api-key: moe-sk-{48 hex chars}
```

### Validation Flow

```mermaid
flowchart TD
    REQ[Incoming request] --> P[Parse header → extract key]
    P --> H["Compute SHA-256:<br/>hash = sha256(key)"]
    H --> L["Valkey lookup:<br/>GET user:apikey:{hash}"]
    L -->|Cache hit<br/>TTL 5 minutes| CHK
    L -->|Cache miss| SQL["PostgreSQL lookup:<br/>SELECT ... FROM api_keys<br/>WHERE key_hash = ? AND is_active = 1<br/>JOIN users WHERE is_active = 1"]
    SQL --> POP["Populate Valkey cache<br/>TTL 300s"]
    POP --> CHK
    CHK["Check user object:<br/>- is_active == 1?<br/>- Budget not exceeded?<br/>- Permissions for requested resource?"]
    CHK --> EX["Execute or reject request<br/>401 / 403 / 429"]
```

### Valkey Schema

```
user:apikey:{sha256-hash}   →   HASH
    user_id          STRING
    username         STRING
    role             STRING   (user|subscriber|expert|admin)
    is_active        STRING   (1|0)
    daily_limit      STRING   (integer or empty = unlimited)
    monthly_limit    STRING
    total_limit      STRING
    permissions      STRING   (JSON: {resource_type: [id, ...]})
    cost_factor      STRING   (float)

TTL: 300 seconds
```

---

## CSRF Protection

All forms in the Admin backend and User Portal are CSRF-protected:

```html
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
```

- Token generated per session: `secrets.token_hex(16)`
- Validated server-side with `secrets.compare_digest()`
- Session lifetime: max 8 hours (`SESSION_MAX_AGE = 28800`)

---

## Session Configuration

```python
SESSION_MAX_AGE = 28800  # 8 hours
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "lax"
```

---

## Admin Impersonation

Admins can take over user sessions:

```
GET /admin/users/{uid}/impersonate
```

1. Admin session is checked (`is_admin = 1`)
2. User session is set for the current session:
   ```python
   session["user_authenticated"] = True
   session["user_id"] = uid
   session["admin_impersonating"] = True
   ```
3. Redirect to `/user/dashboard`
4. Orange impersonation banner appears

**Exit:**
```
GET /user/impersonate/exit
```
Resets admin session, clears user impersonation flags.

---

## Security Overview

| Mechanism | Implementation |
|-------------|----------------|
| Password hashing | bcrypt (passlib) |
| API key storage | SHA-256 hash, never plaintext |
| CSRF protection | HMAC-based session token |
| Session TTL | 8 hours |
| Valkey cache TTL | 5 minutes (API keys) |
| OIDC group | `moe-admins` |
| Password reset TTL | 1 hour, single-use |
| Budget enforcement | Valkey counter + orchestrator check |

"""
User-management database for MoE Admin UI.
Postgres (psycopg3) via a module-global AsyncConnectionPool + bcrypt for passwords.
Valkey sync for fast auth lookups in the orchestrator.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

import bcrypt as _bcrypt
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.getenv(
    "MOE_USERDB_URL",
    "postgresql://moe_admin@terra_checkpoints:5432/moe_userdb",
)
REDIS_URL    = os.getenv("REDIS_URL", "redis://terra_cache:6379")

# Backwards-compat shim: older code paths referenced DB_PATH for log messages.
# The variable is kept as a human-readable identifier only.
DB_PATH = DATABASE_URL


# ─── Schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                    TEXT PRIMARY KEY,
    username              TEXT UNIQUE NOT NULL,
    email                 TEXT UNIQUE NOT NULL,
    display_name          TEXT NOT NULL DEFAULT '',
    hashed_password       TEXT NOT NULL,
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin              BOOLEAN NOT NULL DEFAULT FALSE,
    role                  TEXT NOT NULL DEFAULT 'user',
    language              TEXT NOT NULL DEFAULT 'de_DE',
    timezone_offset_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
    alert_enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    alert_threshold_pct   INTEGER NOT NULL DEFAULT 80,
    alert_email           TEXT,
    last_alert_sent_at    TEXT,
    default_cc_profile_id TEXT,
    -- Stammdaten
    first_name            TEXT NOT NULL DEFAULT '',
    last_name             TEXT NOT NULL DEFAULT '',
    street_address        TEXT NOT NULL DEFAULT '',
    postal_code           TEXT NOT NULL DEFAULT '',
    city                  TEXT NOT NULL DEFAULT '',
    country               TEXT NOT NULL DEFAULT '',
    date_of_birth         TEXT,
    gender                TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash      TEXT UNIQUE NOT NULL,
    key_prefix    TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    cc_profile_id TEXT,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    expires_at    TEXT
);

CREATE TABLE IF NOT EXISTS token_budgets (
    user_id       TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    daily_limit   BIGINT,
    monthly_limit BIGINT,
    total_limit   BIGINT,
    budget_type   TEXT NOT NULL DEFAULT 'subscription',
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    granted_at    TEXT NOT NULL,
    UNIQUE(user_id, resource_type, resource_id)
);

CREATE TABLE IF NOT EXISTS usage_log (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(id),
    api_key_id        TEXT REFERENCES api_keys(id),
    request_id        TEXT NOT NULL,
    session_id        TEXT,
    model             TEXT NOT NULL,
    moe_mode          TEXT NOT NULL,
    prompt_tokens     BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens      BIGINT NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'ok',
    notes             TEXT,
    requested_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS user_expert_templates (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cost_factor DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    config_json TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_cc_profiles (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash       ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_user       ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_permissions_user    ON permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_user_date     ON usage_log(user_id, requested_at);
CREATE INDEX IF NOT EXISTS idx_usage_request       ON usage_log(request_id);
CREATE INDEX IF NOT EXISTS idx_user_templates_user ON user_expert_templates(user_id);
CREATE INDEX IF NOT EXISTS idx_user_cc_profiles_user ON user_cc_profiles(user_id);

CREATE TABLE IF NOT EXISTS admin_expert_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS federation_config (
    id                    TEXT PRIMARY KEY DEFAULT 'default',
    enabled               BOOLEAN NOT NULL DEFAULT FALSE,
    hub_url               TEXT NOT NULL DEFAULT '',
    hub_api_key           TEXT NOT NULL DEFAULT '',
    node_id               TEXT NOT NULL DEFAULT '',
    node_name             TEXT NOT NULL DEFAULT '',
    sync_interval_seconds INTEGER NOT NULL DEFAULT 3600,
    auto_push_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    last_push_at          TEXT,
    last_pull_at          TEXT,
    updated_at            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS federation_domain_policy (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL UNIQUE,
    mode            TEXT NOT NULL DEFAULT 'blocked',
    min_confidence  DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    only_verified   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS federation_outbox (
    id            TEXT PRIMARY KEY,
    bundle_json   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    domain        TEXT NOT NULL,
    triple_count  INTEGER NOT NULL DEFAULT 0,
    entity_count  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    sent_at       TEXT,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_fed_outbox_status ON federation_outbox(status);
CREATE INDEX IF NOT EXISTS idx_fed_outbox_domain ON federation_outbox(domain);
"""


# ─── Pool lifecycle ───────────────────────────────────────────────────────────

_pool: Optional[AsyncConnectionPool] = None


async def _bootstrap_role_and_db() -> None:
    """Legacy-install rescue: create moe_admin role + moe_userdb database on an
    existing Postgres volume that pre-dates scripts/postgres-init/01-moe_admin.sh.

    /docker-entrypoint-initdb.d/* only runs on first init. Operators who
    initialised their volume before that script existed get stuck with a
    'database "moe_userdb" does not exist' error forever. We fix it from
    the application side: connect as the superuser (POSTGRES_USER, which
    owns the bootstrap 'langgraph' database), CREATE ROLE/DATABASE if
    missing, then let init_db() retry the normal pool open.
    """
    import urllib.parse as _url

    parsed = _url.urlparse(DATABASE_URL)
    if not parsed.hostname:
        raise RuntimeError("MOE_USERDB_URL has no hostname — cannot bootstrap")
    su_user = os.getenv("POSTGRES_USER", "langgraph")
    su_pass = os.getenv("POSTGRES_CHECKPOINT_PASSWORD", "")
    su_db   = os.getenv("POSTGRES_DB", su_user)
    if not su_pass:
        raise RuntimeError(
            "POSTGRES_CHECKPOINT_PASSWORD not set — cannot bootstrap moe_userdb"
        )

    target_user = parsed.username or "moe_admin"
    target_pass = _url.unquote(parsed.password or "")
    target_db   = (parsed.path or "/moe_userdb").lstrip("/") or "moe_userdb"

    dsn = (
        f"host={parsed.hostname} port={parsed.port or 5432} "
        f"user={su_user} password={su_pass} dbname={su_db}"
    )
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s",
                (target_user,),
            )
            if not await cur.fetchone():
                # Role name and password are not user-controlled here but we
                # still route the password through a parameterised string:
                # psycopg does not parameterise DDL, so use literal escaping
                # (double single-quotes) on the password only.
                await cur.execute(
                    f"CREATE ROLE {target_user} LOGIN PASSWORD "
                    f"'{target_pass.replace(chr(39), chr(39)*2)}'"
                )
            else:
                await cur.execute(
                    f"ALTER ROLE {target_user} WITH LOGIN PASSWORD "
                    f"'{target_pass.replace(chr(39), chr(39)*2)}'"
                )
            await cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target_db,)
            )
            if not await cur.fetchone():
                await cur.execute(f"CREATE DATABASE {target_db} OWNER {target_user}")
            await cur.execute(
                f"GRANT ALL PRIVILEGES ON DATABASE {target_db} TO {target_user}"
            )
    logger.info(
        "🔧 bootstrapped role %s and database %s via %s superuser",
        target_user, target_db, su_user,
    )


async def init_db() -> None:
    """Open the connection pool and create the schema if it doesn't exist yet.

    On legacy Postgres volumes that were initialised before the
    /docker-entrypoint-initdb.d/ init script existed, the moe_admin role
    and moe_userdb database are missing. We detect that once and bootstrap
    them as the Postgres superuser so the user never has to wipe the
    volume manually."""
    global _pool
    if _pool is None:
        for attempt in range(2):
            try:
                _pool = AsyncConnectionPool(
                    DATABASE_URL,
                    min_size=1,
                    max_size=10,
                    open=False,
                    kwargs={"row_factory": dict_row, "autocommit": False},
                )
                await _pool.open()
                await _pool.wait()
                break
            except Exception as e:
                msg = str(e).lower()
                legacy_db = 'database "moe_userdb"' in msg and "does not exist" in msg
                legacy_role = 'role "moe_admin"' in msg and "does not exist" in msg
                bad_pw = 'password authentication failed for user "moe_admin"' in msg
                if attempt == 0 and (legacy_db or legacy_role or bad_pw):
                    logger.warning(
                        "moe_userdb connection failed (%s) — running one-time bootstrap",
                        e.__class__.__name__,
                    )
                    if _pool is not None:
                        try:
                            await _pool.close()
                        except Exception:
                            pass
                        _pool = None
                    await _bootstrap_role_and_db()
                    continue
                raise
        logger.info("moe_userdb pool opened: %s", DATABASE_URL.split("@")[-1])
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SCHEMA)


async def close_db() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("moe_userdb pool not initialized — call init_db() first")
    return _pool


async def seed_initial_admin() -> None:
    """Legt den ersten Admin-User aus Umgebungsvariablen an, falls noch kein Admin existiert."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM users WHERE role='admin' OR is_admin=TRUE LIMIT 1"
            )
            if await cur.fetchone():
                return
    admin_user  = os.getenv("ADMIN_USER",  "admin")
    admin_pass  = os.getenv("ADMIN_PASSWORD", "changeme")
    admin_email = os.getenv("ADMIN_EMAIL", f"{admin_user}@localhost")
    await create_user(admin_user, admin_email, admin_pass,
                      display_name="Administrator", is_admin=True, role="admin")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix)."""
    raw = "moe-sk-" + secrets.token_hex(24)
    return raw, hash_api_key(raw), raw[:16]


async def hash_password(password: str) -> str:
    pw = password.encode("utf-8")
    hashed = await asyncio.to_thread(_bcrypt.hashpw, pw, _bcrypt.gensalt(rounds=12))
    return hashed.decode()


async def verify_password(password: str, hashed: str) -> bool:
    hashed_bytes = hashed.encode()
    if await asyncio.to_thread(_bcrypt.checkpw, password.encode("utf-8"), hashed_bytes):
        return True
    legacy_pw = hashlib.sha256(password.encode("utf-8")).digest()
    return await asyncio.to_thread(_bcrypt.checkpw, legacy_pw, hashed_bytes)


async def is_legacy_hash(password: str, hashed: str) -> bool:
    """True if the hash was created with the old SHA-256 scheme — re-hash needed."""
    hashed_bytes = hashed.encode()
    direct_ok = await asyncio.to_thread(_bcrypt.checkpw, password.encode("utf-8"), hashed_bytes)
    if direct_ok:
        return False
    legacy_pw = hashlib.sha256(password.encode("utf-8")).digest()
    return await asyncio.to_thread(_bcrypt.checkpw, legacy_pw, hashed_bytes)


async def update_user_language(user_id: str, lang: str) -> None:
    """Saves the language preference for a user."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET language=%s, updated_at=%s WHERE id=%s",
                (lang, now_iso(), user_id),
            )


# ─── User CRUD ───────────────────────────────────────────────────────────────

async def create_user(
    username: str, email: str, password: str,
    display_name: str = "", is_admin: bool = False, role: str = "user",
    first_name: str = "", last_name: str = "",
    street_address: str = "", postal_code: str = "", city: str = "",
    country: str = "", date_of_birth: str = "", gender: str = "",
) -> dict:
    """Creates a user, returns user dict. Raises ValueError on duplicate."""
    if role == "admin":
        is_admin = True
    hashed = await hash_password(password)
    uid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO users "
                    "(id,username,email,display_name,hashed_password,is_active,is_admin,role,"
                    "first_name,last_name,street_address,postal_code,city,country,date_of_birth,gender,"
                    "created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (uid, username, email, display_name, hashed, bool(is_admin), role,
                     first_name, last_name, street_address, postal_code, city,
                     country or "", date_of_birth or None, gender or None,
                     now, now),
                )
                await cur.execute(
                    "INSERT INTO token_budgets (user_id,daily_limit,monthly_limit,total_limit,budget_type,updated_at) "
                    "VALUES (%s,NULL,NULL,NULL,'subscription',%s)",
                    (uid, now),
                )
            except psycopg.errors.UniqueViolation as e:
                raise ValueError(f"Username or email already taken: {e}") from e
    return await get_user(uid)


async def get_user(user_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.*, b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id WHERE u.id=%s",
                (user_id,),
            )
            return await cur.fetchone()


async def get_user_by_username(username: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.*, b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id WHERE u.username=%s",
                (username,),
            )
            return await cur.fetchone()


async def list_users() -> list[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.id, u.username, u.email, u.display_name, u.first_name, u.last_name, "
                "u.is_active, u.is_admin, u.role, u.created_at, u.updated_at, "
                "b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id ORDER BY u.created_at DESC"
            )
            return await cur.fetchall()


async def update_user(user_id: str, **kwargs) -> Optional[dict]:
    allowed = {
        "email", "display_name", "is_active", "is_admin", "role",
        "alert_enabled", "alert_threshold_pct", "alert_email", "last_alert_sent_at",
        "first_name", "last_name", "street_address", "postal_code", "city",
        "country", "date_of_birth", "gender",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return await get_user(user_id)
    # Normalize booleans for Postgres
    for bool_key in ("is_active", "is_admin", "alert_enabled"):
        if bool_key in updates:
            updates[bool_key] = bool(updates[bool_key])
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=%s" for k in updates)
    vals = list(updates.values()) + [user_id]
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"UPDATE users SET {sets} WHERE id=%s", vals)
    return await get_user(user_id)


async def update_password(user_id: str, new_password: str) -> None:
    hashed = await hash_password(new_password)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET hashed_password=%s, updated_at=%s WHERE id=%s",
                (hashed, now_iso(), user_id),
            )


async def update_user_timezone(user_id: str, offset_hours: float) -> None:
    """Setzt den Zeitzonen-Offset eines Users (−12.0 bis +14.0 Stunden)."""
    offset_hours = max(-12.0, min(14.0, float(offset_hours)))
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET timezone_offset_hours=%s, updated_at=%s WHERE id=%s",
                (offset_hours, now_iso(), user_id),
            )


async def delete_user(user_id: str) -> None:
    """Soft-delete: setzt is_active=FALSE."""
    await update_user(user_id, is_active=False)


async def get_user_by_email(email: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.*, b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id "
                "WHERE u.email=%s AND u.is_active=TRUE",
                (email,),
            )
            return await cur.fetchone()


# ─── Password Reset Tokens ────────────────────────────────────────────────────

async def create_reset_token(user_id: str) -> str:
    """Creates a one-time reset token (TTL 1h) and returns it."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO password_reset_tokens (token, user_id, expires_at, used) VALUES (%s,%s,%s,FALSE)",
                (token, user_id, expires_at),
            )
    return token


async def get_reset_token(token: str) -> Optional[dict]:
    """Returns the token dict if valid (unused, not expired), otherwise None."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM password_reset_tokens WHERE token=%s AND used=FALSE",
                (token,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            return None
    except (ValueError, KeyError):
        return None
    return row


async def consume_reset_token(token: str) -> None:
    """Markiert Token als verbraucht."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE password_reset_tokens SET used=TRUE WHERE token=%s",
                (token,),
            )


# ─── Budget CRUD ─────────────────────────────────────────────────────────────

async def set_budget(user_id: str, daily: Optional[int], monthly: Optional[int],
                     total: Optional[int], budget_type: str = "subscription") -> None:
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO token_budgets (user_id,daily_limit,monthly_limit,total_limit,budget_type,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET "
                "daily_limit=excluded.daily_limit, monthly_limit=excluded.monthly_limit, "
                "total_limit=excluded.total_limit, budget_type=excluded.budget_type, "
                "updated_at=excluded.updated_at",
                (user_id, daily, monthly, total, budget_type, now),
            )


async def get_budget(user_id: str) -> dict:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM token_budgets WHERE user_id=%s",
                (user_id,),
            )
            row = await cur.fetchone()
    return row or {
        "user_id": user_id, "daily_limit": None, "monthly_limit": None,
        "total_limit": None, "budget_type": "subscription",
    }


# ─── Permissions CRUD ────────────────────────────────────────────────────────

async def grant_permission(user_id: str, resource_type: str, resource_id: str) -> dict:
    pid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO permissions (id,user_id,resource_type,resource_id,granted_at) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (pid, user_id, resource_type, resource_id, now),
                )
            except psycopg.errors.UniqueViolation:
                pass  # already granted
    return {"id": pid, "user_id": user_id, "resource_type": resource_type,
            "resource_id": resource_id, "granted_at": now}


async def revoke_permission(perm_id: str) -> None:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM permissions WHERE id=%s", (perm_id,))


async def revoke_permission_by_resource(user_id: str, resource_type: str, resource_id: str) -> None:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM permissions WHERE user_id=%s AND resource_type=%s AND resource_id=%s",
                (user_id, resource_type, resource_id),
            )


async def get_permissions(user_id: str) -> list[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM permissions WHERE user_id=%s ORDER BY resource_type, resource_id",
                (user_id,),
            )
            return await cur.fetchall()


async def get_all_granted_model_endpoints() -> list[str]:
    """Returns all system-wide granted model_endpoint resource IDs (deduplicated)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT resource_id FROM permissions WHERE resource_type='model_endpoint'"
            )
            rows = await cur.fetchall()
    return [r["resource_id"] for r in rows]


async def get_permissions_map(user_id: str) -> dict:
    """Returns compact {resource_type: [resource_id, ...]} for Valkey cache."""
    perms = await get_permissions(user_id)
    result: dict = {}
    for p in perms:
        rt = p["resource_type"]
        result.setdefault(rt, [])
        result[rt].append(p["resource_id"])
    return result


# ─── API Key CRUD ─────────────────────────────────────────────────────────────

async def create_api_key(user_id: str, label: str = "") -> tuple[str, dict]:
    """Creates a new API key. Returns (raw_key, key_dict).
    raw_key is only returned once — never visible again."""
    raw, key_hash, key_prefix = generate_api_key()
    kid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO api_keys (id,user_id,key_hash,key_prefix,label,is_active,created_at) "
                "VALUES (%s,%s,%s,%s,%s,TRUE,%s)",
                (kid, user_id, key_hash, key_prefix, label, now),
            )
    key_dict = {"id": kid, "user_id": user_id, "key_prefix": key_prefix,
                "label": label, "is_active": True, "created_at": now,
                "last_used_at": None, "expires_at": None}
    return raw, key_dict


async def list_api_keys(user_id: str) -> list[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id,user_id,key_prefix,label,is_active,created_at,last_used_at,expires_at,cc_profile_id "
                "FROM api_keys WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,),
            )
            return await cur.fetchall()


async def revoke_api_key(key_id: str) -> Optional[str]:
    """Locks a key; returns key_hash for Valkey DEL."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT key_hash FROM api_keys WHERE id=%s", (key_id,))
            row = await cur.fetchone()
            if not row:
                return None
            key_hash = row["key_hash"]
            await cur.execute("UPDATE api_keys SET is_active=FALSE WHERE id=%s", (key_id,))
    return key_hash


async def get_active_key_hashes(user_id: str) -> list[dict]:
    """All active API keys for a user (id + key_hash + cc_profile_id) for Valkey sync."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, key_hash, cc_profile_id FROM api_keys WHERE user_id=%s AND is_active=TRUE",
                (user_id,),
            )
            return await cur.fetchall()


async def update_api_key_label(key_id: str, label: str, user_id: Optional[str] = None) -> bool:
    """Updates the label of an API key. user_id restricts to own keys."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            if user_id:
                await cur.execute(
                    "UPDATE api_keys SET label=%s WHERE id=%s AND user_id=%s",
                    (label.strip(), key_id, user_id),
                )
            else:
                await cur.execute(
                    "UPDATE api_keys SET label=%s WHERE id=%s",
                    (label.strip(), key_id),
                )
            return cur.rowcount > 0


async def set_user_default_cc_profile(user_id: str, profile_id: Optional[str]) -> bool:
    """Sets or clears the default CC profile for a user."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET default_cc_profile_id=%s, updated_at=%s WHERE id=%s",
                (profile_id, now_iso(), user_id),
            )
            ok = cur.rowcount > 0
    if ok:
        await sync_user_to_redis(user_id)
    return ok


async def set_api_key_cc_profile(key_id: str, user_id: str, profile_id: Optional[str]) -> bool:
    """Weist einem API-Key ein spezifisches CC-Profil zu (oder entfernt die Zuweisung)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE api_keys SET cc_profile_id=%s WHERE id=%s AND user_id=%s",
                (profile_id, key_id, user_id),
            )
            ok = cur.rowcount > 0
    if ok:
        await sync_user_to_redis(user_id)
    return ok


# ─── Usage Log ────────────────────────────────────────────────────────────────

async def log_usage(user_id: str, api_key_id: Optional[str], request_id: str,
                    model: str, moe_mode: str, prompt_tokens: int,
                    completion_tokens: int, status: str = "ok") -> None:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO usage_log (id,user_id,api_key_id,request_id,model,moe_mode,"
                "prompt_tokens,completion_tokens,total_tokens,status,requested_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (new_id(), user_id, api_key_id, request_id, model, moe_mode,
                 prompt_tokens, completion_tokens, prompt_tokens + completion_tokens,
                 status, now_iso()),
            )


async def update_usage_note(usage_id: str, user_id: str, note: str) -> bool:
    """Sets or clears the note for a usage entry (only if it belongs to the user)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE usage_log SET notes=%s WHERE id=%s AND user_id=%s",
                (note.strip() or None, usage_id, user_id),
            )
            return cur.rowcount > 0


async def get_usage(user_id: str, days: int = 30, limit: int = 200) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT MIN(ul.id) AS id, "
                "COALESCE(ul.session_id, ul.request_id) AS session_id, "
                "ul.request_id, ul.user_id, ul.api_key_id, "
                "ul.model, ul.moe_mode, "
                "SUM(ul.prompt_tokens) AS prompt_tokens, "
                "SUM(ul.completion_tokens) AS completion_tokens, "
                "SUM(ul.total_tokens) AS total_tokens, "
                "CASE WHEN COUNT(CASE WHEN ul.status != 'ok' THEN 1 END) > 0 THEN 'error' ELSE 'ok' END AS status, "
                "MIN(ul.requested_at) AS requested_at, "
                "MAX(ul.requested_at) AS last_requested_at, "
                "COUNT(*) AS turn_count, "
                "(SELECT u2.notes FROM usage_log u2 WHERE u2.request_id = ul.request_id "
                " ORDER BY u2.requested_at ASC LIMIT 1) AS notes, "
                "MAX(ak.label) AS key_label, MAX(ak.key_prefix) AS key_prefix, "
                "MAX(ul.request_id) AS _req_id "
                "FROM usage_log ul "
                "LEFT JOIN api_keys ak ON ul.api_key_id = ak.id "
                "WHERE ul.user_id=%s AND ul.requested_at >= %s "
                "GROUP BY COALESCE(ul.session_id, ul.request_id), ul.request_id, ul.user_id, "
                "ul.api_key_id, ul.model, ul.moe_mode "
                "ORDER BY MIN(ul.requested_at) DESC LIMIT %s",
                (user_id, cutoff, limit),
            )
            return await cur.fetchall()


async def get_usage_summary(user_id: str) -> dict:
    """Aggregated statistics for user dashboard (incl. input/output breakdown).

    Uses ISO-timestamp string comparisons. `requested_at` is stored as
    `datetime.isoformat()` so LEFT(x,10) gives YYYY-MM-DD and LEFT(x,7) gives YYYY-MM.
    """
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    cutoff_30 = (now - timedelta(days=30)).isoformat()
    cutoff_14 = (now - timedelta(days=14)).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            # Heute
            # Note: SUM() over numeric columns returns PG numeric → Python Decimal,
            # which Jinja's |tojson filter cannot serialize. Cast to bigint so
            # psycopg returns plain ints.
            await cur.execute(
                "SELECT COALESCE(SUM(total_tokens),0)::bigint AS tokens, "
                "COALESCE(SUM(prompt_tokens),0)::bigint AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens),0)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND LEFT(requested_at, 10) = %s",
                (user_id, today_str),
            )
            today = await cur.fetchone()
            # Dieser Monat
            await cur.execute(
                "SELECT COALESCE(SUM(total_tokens),0)::bigint AS tokens, "
                "COALESCE(SUM(prompt_tokens),0)::bigint AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens),0)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND LEFT(requested_at, 7) = %s",
                (user_id, month_str),
            )
            month = await cur.fetchone()
            # Gesamt
            await cur.execute(
                "SELECT COALESCE(SUM(total_tokens),0)::bigint AS tokens, "
                "COALESCE(SUM(prompt_tokens),0)::bigint AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens),0)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s",
                (user_id,),
            )
            total = await cur.fetchone()
            # Broken down by model (30 days)
            await cur.execute(
                "SELECT model, moe_mode, "
                "SUM(total_tokens)::bigint AS tokens, "
                "SUM(prompt_tokens)::bigint AS prompt_tokens, "
                "SUM(completion_tokens)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND requested_at >= %s "
                "GROUP BY model, moe_mode ORDER BY tokens DESC LIMIT 20",
                (user_id, cutoff_30),
            )
            by_model = await cur.fetchall()
            # Tagesweise (letzte 14 Tage)
            await cur.execute(
                "SELECT LEFT(requested_at, 10) AS day, "
                "SUM(total_tokens)::bigint AS tokens, "
                "SUM(prompt_tokens)::bigint AS prompt_tokens, "
                "SUM(completion_tokens)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND requested_at >= %s "
                "GROUP BY LEFT(requested_at, 10) ORDER BY day",
                (user_id, cutoff_14),
            )
            daily = await cur.fetchall()
    return {
        "today": today, "month": month, "total": total,
        "by_model": by_model, "daily": daily,
    }


# ─── User Expert Templates ───────────────────────────────────────────────────

async def create_user_template(user_id: str, name: str, description: str,
                                cost_factor: float, config: dict) -> dict:
    """Creates a user-owned expert template. ID prefix: 'user:'."""
    tmpl_id = "user:" + new_id()
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO user_expert_templates "
                "(id,user_id,name,description,cost_factor,config_json,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (tmpl_id, user_id, name, description, cost_factor, config_str, now, now),
            )
    return await get_user_template(tmpl_id, user_id)


async def get_user_template(tmpl_id: str, user_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_expert_templates WHERE id=%s AND user_id=%s",
                (tmpl_id, user_id),
            )
            return await cur.fetchone()


async def list_user_templates(user_id: str) -> list:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_expert_templates WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,),
            )
            return await cur.fetchall()


async def update_user_template(tmpl_id: str, user_id: str, name: str, description: str,
                                cost_factor: float, config: dict) -> Optional[dict]:
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_expert_templates "
                "SET name=%s,description=%s,cost_factor=%s,config_json=%s,updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (name, description, cost_factor, config_str, now, tmpl_id, user_id),
            )
    return await get_user_template(tmpl_id, user_id)


async def delete_user_template(tmpl_id: str, user_id: str) -> bool:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM user_expert_templates WHERE id=%s AND user_id=%s",
                (tmpl_id, user_id),
            )
            return cur.rowcount > 0


# ─── User CC Profiles ─────────────────────────────────────────────────────────

async def create_user_cc_profile(user_id: str, name: str, config: dict) -> dict:
    """Creates a user-owned CC profile. ID prefix: 'ucp-'."""
    profile_id = "ucp-" + new_id()
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO user_cc_profiles (id,user_id,name,config_json,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (profile_id, user_id, name, config_str, now, now),
            )
    return await get_user_cc_profile(profile_id, user_id)


async def get_user_cc_profile(profile_id: str, user_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_cc_profiles WHERE id=%s AND user_id=%s",
                (profile_id, user_id),
            )
            return await cur.fetchone()


async def list_user_cc_profiles(user_id: str) -> list:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_cc_profiles WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,),
            )
            return await cur.fetchall()


async def update_user_cc_profile(profile_id: str, user_id: str, name: str, config: dict) -> Optional[dict]:
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_cc_profiles SET name=%s,config_json=%s,updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (name, config_str, now, profile_id, user_id),
            )
    return await get_user_cc_profile(profile_id, user_id)


async def delete_user_cc_profile(profile_id: str, user_id: str) -> bool:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM user_cc_profiles WHERE id=%s AND user_id=%s",
                (profile_id, user_id),
            )
            return cur.rowcount > 0


# ─── Admin Functions (cross-user) ────────────────────────────────────────────

async def list_all_user_templates() -> list:
    """Admin: all user_expert_templates for all users, JOIN with users for username."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT uet.*, u.username, u.display_name FROM user_expert_templates uet "
                "JOIN users u ON uet.user_id = u.id ORDER BY uet.created_at DESC"
            )
            return await cur.fetchall()


async def list_all_user_cc_profiles() -> list:
    """Admin: all user_cc_profiles for all users, JOIN with users for username."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT ucp.*, u.username, u.display_name FROM user_cc_profiles ucp "
                "JOIN users u ON ucp.user_id = u.id ORDER BY ucp.created_at DESC"
            )
            return await cur.fetchall()


async def set_user_template_active(tmpl_id: str, is_active: bool) -> Optional[dict]:
    """Admin: aktiviert/deaktiviert ein User-Template (ohne user_id-Filter)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_expert_templates SET is_active=%s WHERE id=%s",
                (bool(is_active), tmpl_id),
            )
            await cur.execute("SELECT * FROM user_expert_templates WHERE id=%s", (tmpl_id,))
            return await cur.fetchone()


async def set_user_cc_profile_active(profile_id: str, is_active: bool) -> Optional[dict]:
    """Admin: aktiviert/deaktiviert ein User-CC-Profil (ohne user_id-Filter)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_cc_profiles SET is_active=%s WHERE id=%s",
                (bool(is_active), profile_id),
            )
            await cur.execute("SELECT * FROM user_cc_profiles WHERE id=%s", (profile_id,))
            return await cur.fetchone()


async def admin_delete_user_template(tmpl_id: str) -> Optional[str]:
    """Admin: deletes a user template without user_id check. Returns user_id."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM user_expert_templates WHERE id=%s", (tmpl_id,))
            row = await cur.fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            await cur.execute("DELETE FROM user_expert_templates WHERE id=%s", (tmpl_id,))
            return user_id if cur.rowcount > 0 else None


async def admin_delete_user_cc_profile(profile_id: str) -> Optional[str]:
    """Admin: deletes a user CC profile without user_id check. Returns user_id."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM user_cc_profiles WHERE id=%s", (profile_id,))
            row = await cur.fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            await cur.execute("DELETE FROM user_cc_profiles WHERE id=%s", (profile_id,))
            return user_id if cur.rowcount > 0 else None


# ─── Valkey Sync ──────────────────────────────────────────────────────────────

async def _get_redis():
    """Lazy Valkey connection (only when Valkey is available)."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.ping()
        return r
    except Exception:
        return None


def _get_user_cost_factor(perms: dict) -> float:
    """Cost factor from unlocked inference servers (maximum over all allowed servers)."""
    import os as _os, json as _js
    endpoint_perms = perms.get("model_endpoint", [])
    if not endpoint_perms:
        return 1.0
    try:
        all_servers = _js.loads(_os.getenv("INFERENCE_SERVERS", "[]"))
        server_map = {s["name"]: float(s.get("cost_factor", 1.0)) for s in all_servers}
        max_factor = 1.0
        for ep in endpoint_perms:
            _, _, srv = ep.partition("@")
            if not srv:
                srv = ep
            if srv == "*":
                max_factor = max(max_factor, max(server_map.values(), default=1.0))
                break
            if srv in server_map:
                max_factor = max(max_factor, server_map[srv])
        return max_factor
    except Exception:
        return 1.0


async def sync_user_to_redis(user_id: str) -> None:
    """Synct alle aktiven Keys eines Users in den Valkey-Cache."""
    user = await get_user(user_id)
    if not user:
        return
    budget = await get_budget(user_id)
    perms = await get_permissions_map(user_id)
    perms_json = json.dumps(perms)
    cost_factor = _get_user_cost_factor(perms)

    # Cache user-owned templates inline (for orchestrator access without DB hit)
    user_tmpls = await list_user_templates(user_id)
    user_templates_map = {
        t["id"]: json.loads(t["config_json"])
        for t in user_tmpls if t.get("is_active", True)
    }
    user_templates_json = json.dumps(user_templates_map)

    # User-eigene CC-Profile inline cachen
    user_cc = await list_user_cc_profiles(user_id)
    user_cc_map = {
        p["id"]: json.loads(p["config_json"])
        for p in user_cc if p.get("is_active", True)
    }
    user_cc_profiles_json = json.dumps(user_cc_map)

    r = await _get_redis()
    if not r:
        return
    try:
        key_records = await get_active_key_hashes(user_id)
        for rec in key_records:
            redis_key = f"user:apikey:{rec['key_hash']}"
            if user["is_active"]:
                await r.hset(redis_key, mapping={
                    "user_id":                user_id,
                    "username":               user["username"],
                    "is_active":              "1",
                    "permissions_json":       perms_json,
                    "user_templates_json":    user_templates_json,
                    "user_cc_profiles_json":  user_cc_profiles_json,
                    "budget_daily":           str(budget.get("daily_limit") or ""),
                    "budget_monthly":         str(budget.get("monthly_limit") or ""),
                    "budget_total":           str(budget.get("total_limit") or ""),
                    "budget_type":            budget.get("budget_type", "subscription"),
                    "budget_cost_factor":     str(cost_factor),
                    "key_id":                 rec["id"],
                    "default_cc_profile_id":  user.get("default_cc_profile_id") or "",
                    "key_cc_profile_id":      rec.get("cc_profile_id") or "",
                })
                await r.expire(redis_key, 86400)
            else:
                await r.delete(redis_key)
        await r.set(f"user:{user_id}:cost_factor", str(cost_factor), ex=86400)
    finally:
        await r.aclose()


async def invalidate_user_redis(user_id: str) -> None:
    """Deletes all Valkey keys for a user (on deactivation/deletion)."""
    r = await _get_redis()
    if not r:
        return
    try:
        key_records = await get_active_key_hashes(user_id)
        for rec in key_records:
            await r.delete(f"user:apikey:{rec['key_hash']}")
    finally:
        await r.aclose()


async def invalidate_api_key_redis(key_hash: str) -> None:
    """Deletes a single key from Valkey (on revoke)."""
    r = await _get_redis()
    if not r:
        return
    try:
        await r.delete(f"user:apikey:{key_hash}")
    finally:
        await r.aclose()


async def get_redis_budget_usage(user_id: str) -> dict:
    """Liest aktuellen Token-Verbrauch aus Valkey-Countern (inkl. Input/Output-Trennung)."""
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    month = date.today().strftime("%Y-%m")
    r = await _get_redis()
    if not r:
        return {
            "daily_used": 0, "monthly_used": 0, "total_used": 0,
            "daily_input": 0, "daily_output": 0,
            "monthly_input": 0, "monthly_output": 0,
            "total_input": 0, "total_output": 0,
        }
    try:
        daily          = await r.get(f"user:{user_id}:tokens:daily:{today}")
        monthly        = await r.get(f"user:{user_id}:tokens:monthly:{month}")
        total          = await r.get(f"user:{user_id}:tokens:total")
        daily_input    = await r.get(f"user:{user_id}:tokens:daily:{today}:input")
        daily_output   = await r.get(f"user:{user_id}:tokens:daily:{today}:output")
        monthly_input  = await r.get(f"user:{user_id}:tokens:monthly:{month}:input")
        monthly_output = await r.get(f"user:{user_id}:tokens:monthly:{month}:output")
        total_input    = await r.get(f"user:{user_id}:tokens:total:input")
        total_output   = await r.get(f"user:{user_id}:tokens:total:output")
        return {
            "daily_used":      int(daily          or 0),
            "monthly_used":    int(monthly        or 0),
            "total_used":      int(total          or 0),
            "daily_input":     int(daily_input    or 0),
            "daily_output":    int(daily_output   or 0),
            "monthly_input":   int(monthly_input  or 0),
            "monthly_output":  int(monthly_output or 0),
            "total_input":     int(total_input    or 0),
            "total_output":    int(total_output   or 0),
        }
    finally:
        await r.aclose()


# ─── Admin Expert Templates (database-backed) ────────────────────────────────

async def list_admin_templates() -> list[dict]:
    """List all admin expert templates from the database."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, name, description, config_json, is_active, created_at, updated_at "
                "FROM admin_expert_templates ORDER BY created_at ASC"
            )
            rows = await cur.fetchall()
    result = []
    for row in rows:
        tmpl = json.loads(row["config_json"])
        tmpl["id"] = row["id"]
        tmpl["name"] = row["name"]
        tmpl["description"] = row["description"]
        tmpl["is_active"] = row["is_active"]
        result.append(tmpl)
    return result


async def get_admin_template(tmpl_id: str) -> Optional[dict]:
    """Get a single admin expert template by ID."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, name, description, config_json, is_active "
                "FROM admin_expert_templates WHERE id = %s", (tmpl_id,)
            )
            row = await cur.fetchone()
    if not row:
        return None
    tmpl = json.loads(row["config_json"])
    tmpl["id"] = row["id"]
    tmpl["name"] = row["name"]
    tmpl["description"] = row["description"]
    tmpl["is_active"] = row["is_active"]
    return tmpl


async def upsert_admin_template(tmpl: dict) -> dict:
    """Insert or update an admin expert template in the database."""
    tmpl_id = tmpl.get("id", f"tmpl-{secrets.token_hex(4)}")
    name = tmpl.get("name", "")
    description = tmpl.get("description", "")
    now = datetime.now(timezone.utc).isoformat()

    # Store everything except id/name/description/is_active in config_json
    config = {k: v for k, v in tmpl.items()
              if k not in ("id", "name", "description", "is_active")}
    config_json = json.dumps(config, ensure_ascii=False)

    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    config_json = EXCLUDED.config_json,
                    updated_at = EXCLUDED.updated_at
            """, (tmpl_id, name, description, config_json, now, now))
        await conn.commit()

    tmpl["id"] = tmpl_id
    return tmpl


async def delete_admin_template(tmpl_id: str) -> bool:
    """Delete an admin expert template from the database."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM admin_expert_templates WHERE id = %s", (tmpl_id,)
            )
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted


async def save_all_admin_templates(templates: list[dict]) -> None:
    """Replace all admin expert templates in the database (bulk save)."""
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM admin_expert_templates")
            for tmpl in templates:
                tmpl_id = tmpl.get("id", f"tmpl-{secrets.token_hex(4)}")
                config = {k: v for k, v in tmpl.items()
                          if k not in ("id", "name", "description", "is_active")}
                await cur.execute("""
                    INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    tmpl_id,
                    tmpl.get("name", ""),
                    tmpl.get("description", ""),
                    json.dumps(config, ensure_ascii=False),
                    tmpl.get("is_active", True),
                    now, now,
                ))
        await conn.commit()


async def migrate_env_templates_to_db(env_templates: list[dict]) -> int:
    """One-time migration: move templates from .env to database.

    Only imports templates whose IDs don't already exist in the DB.
    Returns the number of templates migrated.
    """
    existing = await list_admin_templates()
    existing_ids = {t["id"] for t in existing}
    existing_names = {t["name"] for t in existing}
    migrated = 0

    for tmpl in env_templates:
        if tmpl.get("id") in existing_ids or tmpl.get("name") in existing_names:
            continue
        await upsert_admin_template(tmpl)
        migrated += 1

    return migrated


# ─── Federation Config ────────────────────────────────────────────────────────

async def get_federation_config() -> dict:
    """Get the federation configuration (singleton row)."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM federation_config WHERE id = 'default'")
            row = await cur.fetchone()
    if not row:
        return {
            "id": "default", "enabled": False, "hub_url": "", "hub_api_key": "",
            "node_id": "", "node_name": "", "sync_interval_seconds": 3600,
            "auto_push_enabled": False, "last_push_at": None, "last_pull_at": None,
        }
    return dict(row)


async def save_federation_config(config: dict) -> None:
    """Insert or update the federation configuration."""
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO federation_config (id, enabled, hub_url, hub_api_key,
                    node_id, node_name, sync_interval_seconds, auto_push_enabled, updated_at)
                VALUES ('default', %(enabled)s, %(hub_url)s, %(hub_api_key)s,
                    %(node_id)s, %(node_name)s, %(sync_interval_seconds)s,
                    %(auto_push_enabled)s, %(updated_at)s)
                ON CONFLICT (id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    hub_url = EXCLUDED.hub_url,
                    hub_api_key = CASE WHEN EXCLUDED.hub_api_key = '' THEN federation_config.hub_api_key
                                      ELSE EXCLUDED.hub_api_key END,
                    node_id = EXCLUDED.node_id,
                    node_name = EXCLUDED.node_name,
                    sync_interval_seconds = EXCLUDED.sync_interval_seconds,
                    auto_push_enabled = EXCLUDED.auto_push_enabled,
                    updated_at = EXCLUDED.updated_at
            """, {**config, "updated_at": now})
        await conn.commit()


async def update_federation_sync_timestamp(direction: str) -> None:
    """Update last_push_at or last_pull_at."""
    now = datetime.now(timezone.utc).isoformat()
    col = "last_push_at" if direction == "push" else "last_pull_at"
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE federation_config SET {col} = %s WHERE id = 'default'", (now,)
            )
        await conn.commit()


# ─── Federation Domain Policies ───────────────────────────────────────────────

async def list_federation_policies() -> list[dict]:
    """List all domain policies."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM federation_domain_policy ORDER BY domain"
            )
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_federation_policy(domain: str) -> Optional[dict]:
    """Get policy for a specific domain."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM federation_domain_policy WHERE domain = %s", (domain,)
            )
            row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_federation_policy(domain: str, mode: str,
                                    min_confidence: float = 0.7,
                                    only_verified: bool = True) -> dict:
    """Create or update a domain policy."""
    now = datetime.now(timezone.utc).isoformat()
    policy_id = f"fedpol-{domain}"
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO federation_domain_policy (id, domain, mode, min_confidence, only_verified, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (domain) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    min_confidence = EXCLUDED.min_confidence,
                    only_verified = EXCLUDED.only_verified,
                    updated_at = EXCLUDED.updated_at
            """, (policy_id, domain, mode, min_confidence, only_verified, now))
        await conn.commit()
    return {"id": policy_id, "domain": domain, "mode": mode,
            "min_confidence": min_confidence, "only_verified": only_verified}


async def delete_federation_policy(domain: str) -> bool:
    """Delete a domain policy."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM federation_domain_policy WHERE domain = %s", (domain,)
            )
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted


# ─── Federation Outbox ────────────────────────────────────────────────────────

async def create_outbox_entry(bundle_json: str, domain: str,
                               triple_count: int, entity_count: int) -> dict:
    """Queue a knowledge bundle for manual review before push."""
    entry_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO federation_outbox (id, bundle_json, status, domain,
                    triple_count, entity_count, created_at)
                VALUES (%s, %s, 'pending', %s, %s, %s, %s)
            """, (entry_id, bundle_json, domain, triple_count, entity_count, now))
        await conn.commit()
    return {"id": entry_id, "status": "pending", "domain": domain,
            "triple_count": triple_count, "entity_count": entity_count}


async def list_outbox(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    """List outbox entries, optionally filtered by status."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            if status:
                await cur.execute(
                    "SELECT id, status, domain, triple_count, entity_count, created_at, sent_at, error "
                    "FROM federation_outbox WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                    (status, limit)
                )
            else:
                await cur.execute(
                    "SELECT id, status, domain, triple_count, entity_count, created_at, sent_at, error "
                    "FROM federation_outbox ORDER BY created_at DESC LIMIT %s",
                    (limit,)
                )
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_outbox_entry(entry_id: str) -> Optional[dict]:
    """Get a single outbox entry with full bundle data."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM federation_outbox WHERE id = %s", (entry_id,)
            )
            row = await cur.fetchone()
    return dict(row) if row else None


async def update_outbox_status(entry_id: str, status: str,
                                error: Optional[str] = None) -> None:
    """Update outbox entry status (pending → sent/failed/approved)."""
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            if status == "sent":
                await cur.execute(
                    "UPDATE federation_outbox SET status = %s, sent_at = %s WHERE id = %s",
                    (status, now, entry_id)
                )
            elif error:
                await cur.execute(
                    "UPDATE federation_outbox SET status = %s, error = %s WHERE id = %s",
                    (status, error, entry_id)
                )
            else:
                await cur.execute(
                    "UPDATE federation_outbox SET status = %s WHERE id = %s",
                    (status, entry_id)
                )
        await conn.commit()

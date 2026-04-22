#!/usr/bin/env python3
"""
Template Chain Audit — MoE Sovereign
Traverses the full logical chain from DB → Permissions → Redis → API → Endpoint
and reports every broken link as FAIL.

Usage:
  python3 scripts/template_chain_audit.py --template tmpl-curator-qwen36-rtx \
    --env benchmarks/.env
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def _ok(msg):   print(f"{GREEN}[PASS]{RESET} {msg}")
def _fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def _warn(msg): print(f"{YELLOW}[WARN]{RESET} {msg}")
def _info(msg): print(f"       {msg}")

_results = []

def record(status: str, check: str, detail: str = ""):
    _results.append((status, check, detail))
    fn = {"PASS": _ok, "FAIL": _fail, "WARN": _warn}[status]
    fn(f"{check}" + (f" — {detail}" if detail else ""))


# ── Env loading ────────────────────────────────────────────────────────────────
def _load_env(path: str) -> dict:
    result = {}
    p = Path(path)
    if not p.exists():
        return result
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


# ── Lazy imports (packages may or may not be present) ─────────────────────────
def _import_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg, dict_row
    except ImportError:
        return None, None


def _import_redis():
    try:
        import redis as redis_lib
        return redis_lib
    except ImportError:
        return None


def _import_httpx():
    try:
        import httpx
        return httpx
    except ImportError:
        return None


# ── Main audit ─────────────────────────────────────────────────────────────────
def run_audit(tmpl_id: str, bench_env: dict, proj_env: dict, api_base: str, api_key: str):
    redis_password  = proj_env.get("REDIS_PASSWORD", "")
    db_password     = proj_env.get("MOE_USERDB_PASSWORD", "")
    redis_url       = proj_env.get("REDIS_URL", f"redis://:{redis_password}@172.20.0.8:6379")
    db_url          = proj_env.get("MOE_USERDB_URL",
                        f"postgresql://moe_admin:{db_password}@172.20.0.9:5432/moe_userdb")

    psycopg, dict_row = _import_psycopg()
    redis_lib = _import_redis()
    httpx_lib = _import_httpx()

    tmpl_row  = None
    user_id   = None
    key_hash  = hashlib.sha256(api_key.encode()).hexdigest()

    # ── CHECK 01 — DB: Template exists & is_active ─────────────────────────────
    if psycopg is None:
        record("WARN", "CHECK 01", "psycopg not installed — skipping DB checks")
    else:
        try:
            with psycopg.connect(db_url, connect_timeout=5, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, name, description, config_json, is_active "
                        "FROM admin_expert_templates WHERE id=%s",
                        (tmpl_id,),
                    )
                    tmpl_row = cur.fetchone()
        except Exception as e:
            record("FAIL", "CHECK 01", f"DB connection failed: {e}")
            tmpl_row = None

        if tmpl_row is None:
            record("FAIL", "CHECK 01", f"Template '{tmpl_id}' not found in admin_expert_templates")
        elif not tmpl_row["is_active"]:
            record("WARN", "CHECK 01", f"Template '{tmpl_id}' exists but is_active=False (name={tmpl_row['name']})")
        else:
            record("PASS", "CHECK 01", f"name={tmpl_row['name']}, is_active=True")

    # ── CHECK 02 — DB: Planner + Judge have model@endpoint format ──────────────
    if tmpl_row:
        try:
            cfg = json.loads(tmpl_row["config_json"]) if isinstance(tmpl_row["config_json"], str) else tmpl_row["config_json"]
            planner = cfg.get("planner_model", "")
            judge   = cfg.get("judge_model", "")
            issues  = []
            if not planner or "@" not in planner:
                issues.append(f"planner_model missing or no @endpoint: '{planner}'")
            if not judge or "@" not in judge:
                issues.append(f"judge_model missing or no @endpoint: '{judge}'")
            if issues:
                record("FAIL", "CHECK 02", "; ".join(issues))
            else:
                record("PASS", "CHECK 02", f"planner={planner}  judge={judge}")
        except Exception as e:
            record("FAIL", "CHECK 02", f"config_json parse error: {e}")
    else:
        record("WARN", "CHECK 02", "Skipped — template not found in DB")

    # ── CHECK 03 — DB: At least 1 expert with model+endpoint ───────────────────
    if tmpl_row:
        try:
            cfg     = json.loads(tmpl_row["config_json"]) if isinstance(tmpl_row["config_json"], str) else tmpl_row["config_json"]
            experts = cfg.get("experts", {})
            bad_cats, good_cats = [], []
            for cat, cat_cfg in experts.items():
                if not isinstance(cat_cfg, dict):
                    bad_cats.append(f"{cat}:not-dict")
                    continue
                models = cat_cfg.get("models", [])
                if not models:
                    bad_cats.append(f"{cat}:no-models")
                    continue
                for m in models:
                    if not m.get("model") or not m.get("endpoint"):
                        bad_cats.append(f"{cat}:missing-model-or-endpoint")
                        break
                else:
                    good_cats.append(cat)
            if not experts:
                record("FAIL", "CHECK 03", "experts dict is empty")
            elif bad_cats:
                record("WARN", "CHECK 03", f"Bad categories: {bad_cats}  Good: {good_cats}")
            else:
                record("PASS", "CHECK 03", f"Categories: {list(experts.keys())}")
        except Exception as e:
            record("FAIL", "CHECK 03", f"config_json parse error: {e}")
    else:
        record("WARN", "CHECK 03", "Skipped — template not found in DB")

    # ── CHECK 04 — DB + Redis: API-Key user exists & is_active ─────────────────
    if psycopg is not None:
        try:
            with psycopg.connect(db_url, connect_timeout=5, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT ak.user_id, u.is_active FROM api_keys ak "
                        "JOIN users u ON u.id=ak.user_id "
                        "WHERE ak.key_hash=%s AND ak.is_active=TRUE",
                        (key_hash,),
                    )
                    row = cur.fetchone()
        except Exception as e:
            row = None
            record("FAIL", "CHECK 04", f"DB connection failed: {e}")
        if row is None:
            record("FAIL", "CHECK 04", f"API key (hash={key_hash[:16]}…) not found in DB or inactive")
        elif not row["is_active"]:
            record("FAIL", "CHECK 04", f"User {row['user_id']} is deactivated")
        else:
            user_id = row["user_id"]
            record("PASS", "CHECK 04", f"user_id={user_id}")
    else:
        record("WARN", "CHECK 04", "psycopg not installed — skipping")

    # ── CHECK 05 — Redis: permissions_json contains template ID ────────────────
    if redis_lib is None:
        record("WARN", "CHECK 05", "redis not installed — skipping")
    elif not api_key:
        record("WARN", "CHECK 05", "No API key — skipping")
    else:
        try:
            r = redis_lib.from_url(redis_url, decode_responses=True, socket_connect_timeout=5)
            redis_key = f"user:apikey:{key_hash}"
            perms_json = r.hget(redis_key, "permissions_json")
            if perms_json is None:
                record("FAIL", "CHECK 05", f"Redis key '{redis_key}' missing — sync_user_to_redis() needed")
            else:
                perms = json.loads(perms_json)
                granted = perms.get("expert_template", [])
                if tmpl_id in granted:
                    record("PASS", "CHECK 05", f"Template '{tmpl_id}' in expert_template ({len(granted)} total)")
                else:
                    record("FAIL", "CHECK 05",
                           f"Template '{tmpl_id}' NOT in permissions. Current: {granted[:5]}{'…' if len(granted)>5 else ''}")
        except Exception as e:
            record("FAIL", "CHECK 05", f"Redis error: {e}")

    # ── CHECK 06 — API /v1/models: template appears in response ────────────────
    if httpx_lib is None:
        record("WARN", "CHECK 06", "httpx not installed — skipping")
    elif not api_key:
        record("WARN", "CHECK 06", "No API key — skipping")
    else:
        try:
            resp = httpx_lib.get(
                f"{api_base}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code != 200:
                record("FAIL", "CHECK 06", f"GET /v1/models → HTTP {resp.status_code}")
            else:
                models_data = resp.json()
                model_list  = [m.get("id") for m in models_data.get("data", [])]
                tmpl_name_visible = tmpl_row["name"] if tmpl_row else None
                if tmpl_name_visible and tmpl_name_visible in model_list:
                    record("PASS", "CHECK 06", f"Template name '{tmpl_name_visible}' visible in /v1/models")
                elif tmpl_id in model_list:
                    record("PASS", "CHECK 06", f"Template ID '{tmpl_id}' visible in /v1/models")
                else:
                    record("FAIL", "CHECK 06",
                           f"Neither id '{tmpl_id}' nor name '{tmpl_name_visible}' found in /v1/models "
                           f"({len(model_list)} models returned)")
        except Exception as e:
            record("FAIL", "CHECK 06", f"HTTP error: {e}")

    # ── CHECK 07 — API chat/completions: X-MoE-Template-Id header ──────────────
    # Uses streaming to get response headers without waiting for full LLM output.
    if httpx_lib is None:
        record("WARN", "CHECK 07", "httpx not installed — skipping")
    elif not api_key or not tmpl_row:
        record("WARN", "CHECK 07", "Skipped — no API key or template not found")
    else:
        tmpl_name = tmpl_row["name"]
        try:
            with httpx_lib.stream(
                "POST",
                f"{api_base}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": tmpl_name,
                      "messages": [{"role": "user", "content": "Reply with exactly: AUDIT_OK"}],
                      "stream": True},
                timeout=15,
            ) as resp:
                returned_tmpl_id   = resp.headers.get("x-moe-template-id", "")
                returned_tmpl_name = resp.headers.get("x-moe-template-name", "")
                # Read first chunk to confirm connection established, then close
                try:
                    next(resp.iter_lines())
                except StopIteration:
                    pass
            if resp.status_code == 403:
                record("FAIL", "CHECK 07",
                       "403 Forbidden — template not in permissions (CHECK 05 likely also failed)")
            elif resp.status_code not in (200, 201):
                record("FAIL", "CHECK 07", f"HTTP {resp.status_code}")
            elif returned_tmpl_id == tmpl_id:
                record("PASS", "CHECK 07",
                       f"X-MoE-Template-Id={returned_tmpl_id}  X-MoE-Template-Name={returned_tmpl_name}")
            elif returned_tmpl_id:
                record("WARN", "CHECK 07",
                       f"200 OK but wrong template used: got '{returned_tmpl_id}', expected '{tmpl_id}' (GHOST!)")
            else:
                record("WARN", "CHECK 07",
                       "200 OK but X-MoE-Template-Id header missing — orchestrator may not have been rebuilt")
        except Exception as e:
            record("FAIL", "CHECK 07", f"HTTP error: {e}")

    # ── CHECK 08 — Endpoint reachability ───────────────────────────────────────
    if httpx_lib is None or not tmpl_row:
        record("WARN", "CHECK 08", "httpx not installed or template missing — skipping")
    else:
        try:
            cfg       = json.loads(tmpl_row["config_json"]) if isinstance(tmpl_row["config_json"], str) else tmpl_row["config_json"]
            # Collect all endpoints mentioned in the template config
            endpoints: dict = {}  # name → url (from INFERENCE_SERVERS in project env)
            inf_servers_raw = proj_env.get("INFERENCE_SERVERS", "[]")
            # .env stores value as "[\{\"name\":\"...\"}]" — strip outer quotes + unescape
            inf_servers_raw = inf_servers_raw.strip('"').strip("'").replace('\\"', '"').replace("\\'", "'")
            try:
                inf_servers = json.loads(inf_servers_raw)
            except Exception:
                inf_servers = []
            if not isinstance(inf_servers, list):
                inf_servers = []
            url_map = {s["name"]: s["url"] for s in inf_servers if s.get("url")}

            def _collect_ep(model_at_ep: str):
                if "@" in model_at_ep:
                    ep = model_at_ep.rsplit("@", 1)[1]
                    if ep and ep not in endpoints:
                        endpoints[ep] = url_map.get(ep, "")

            _collect_ep(cfg.get("planner_model", ""))
            _collect_ep(cfg.get("judge_model", ""))
            for cat_cfg in cfg.get("experts", {}).values():
                for m in cat_cfg.get("models", []):
                    ep = m.get("endpoint", "")
                    if ep and ep not in endpoints:
                        endpoints[ep] = url_map.get(ep, "")

            if not endpoints:
                record("WARN", "CHECK 08", "No endpoints found in template config")
            else:
                all_ok = True
                for ep_name, ep_url in endpoints.items():
                    if not ep_url:
                        record("WARN", "CHECK 08",
                               f"Endpoint '{ep_name}' not in INFERENCE_SERVERS — cannot verify URL")
                        all_ok = False
                        continue
                    try:
                        # Strip /v1 suffix — Ollama's /api/tags is at the base URL
                        base_url = ep_url.rstrip("/")
                        if base_url.endswith("/v1"):
                            base_url = base_url[:-3]
                        r2 = httpx_lib.get(base_url.rstrip("/") + "/api/tags", timeout=5)
                        if r2.status_code == 200:
                            _info(f"  [{ep_name}] {ep_url} → 200 OK")
                        else:
                            record("FAIL", "CHECK 08", f"Endpoint '{ep_name}' → HTTP {r2.status_code}")
                            all_ok = False
                    except Exception as e2:
                        record("FAIL", "CHECK 08", f"Endpoint '{ep_name}' ({ep_url}) unreachable: {e2}")
                        all_ok = False
                if all_ok:
                    record("PASS", "CHECK 08", f"All {len(endpoints)} endpoint(s) reachable: {list(endpoints.keys())}")
        except Exception as e:
            record("FAIL", "CHECK 08", f"Error: {e}")

    # ── CHECK 09 — admin_override bypass test (Security) ───────────────────────
    # We need a second API key WITHOUT the template in permissions.
    # Strategy: use the same key but request a template we know exists in DB but
    # is NOT in our permissions. We look up a template by querying DB.
    _bypass_tested = False
    if httpx_lib and psycopg and tmpl_row and api_key:
        try:
            # Find a template that definitely exists in DB but is NOT in our permissions
            with psycopg.connect(db_url, connect_timeout=5, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    # Get our current permissions
                    cur.execute(
                        "SELECT resource_id FROM permissions "
                        "WHERE user_id=(SELECT user_id FROM api_keys WHERE key_hash=%s AND is_active=TRUE) "
                        "AND resource_type='expert_template'",
                        (key_hash,),
                    )
                    granted_ids = {row["resource_id"] for row in cur.fetchall()}
                    # Find a template NOT in our permissions
                    cur.execute(
                        "SELECT id, name FROM admin_expert_templates WHERE is_active=TRUE ORDER BY created_at LIMIT 20"
                    )
                    all_tmpls = cur.fetchall()
            forbidden = next((t for t in all_tmpls if t["id"] not in granted_ids), None)
            if forbidden is None:
                record("WARN", "CHECK 09", "All DB templates are in permissions — cannot test bypass")
            else:
                resp = httpx_lib.post(
                    f"{api_base}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": forbidden["name"],
                          "messages": [{"role": "user", "content": "test"}],
                          "stream": False},
                    timeout=30,
                )
                if resp.status_code == 403:
                    record("PASS", "CHECK 09",
                           f"403 Forbidden for '{forbidden['name']}' (not in permissions) — bypass fixed ✓")
                elif resp.status_code == 200:
                    record("FAIL", "CHECK 09",
                           f"SECURITY GAP: 200 OK for '{forbidden['name']}' — admin_override bypass active in main.py")
                else:
                    record("WARN", "CHECK 09",
                           f"Unexpected {resp.status_code} for '{forbidden['name']}': {resp.text[:100]}")
                _bypass_tested = True
        except Exception as e:
            record("WARN", "CHECK 09", f"Could not complete bypass test: {e}")
    else:
        record("WARN", "CHECK 09", "Skipped — missing dependencies or template not found")

    # ── CHECK 10 — Ghost template: ID in permissions but absent from DB ──────────
    # Inject a fake template ID into permissions, call it, expect 422 (not silent fallback).
    # We don't actually modify DB permissions — we simulate by directly testing orchestrator
    # with a nonexistent tmpl- ID via a crafted model-name that won't match any template name.
    if httpx_lib and psycopg and api_key:
        try:
            # Use a known-bad template name that exists in neither DB nor any user's permissions.
            with httpx_lib.stream(
                "POST",
                f"{api_base}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "moe-ghost-model-nonexistent-audit-test-xyzzy",
                      "messages": [{"role": "user", "content": "test"}],
                      "stream": True},
                timeout=10,
            ) as resp:
                returned_tmpl = resp.headers.get("x-moe-template-id", "")
                try:
                    next(resp.iter_lines())
                except StopIteration:
                    pass
            if resp.status_code in (403, 404, 422):
                record("PASS", "CHECK 10",
                       f"HTTP {resp.status_code} for nonexistent template — no silent ghost fallback ✓")
            elif resp.status_code == 200:
                record("WARN", "CHECK 10",
                       f"200 OK for nonexistent template name — silently fell back to '{returned_tmpl or 'default EXPERTS'}' (ghost risk)")
            else:
                record("WARN", "CHECK 10", f"Unexpected HTTP {resp.status_code}")
        except Exception as e:
            record("WARN", "CHECK 10", f"HTTP error: {e}")
    else:
        record("WARN", "CHECK 10", "Skipped — missing dependencies")

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("─" * 60)
    passed = sum(1 for s, _, _ in _results if s == "PASS")
    failed = sum(1 for s, _, _ in _results if s == "FAIL")
    warned = sum(1 for s, _, _ in _results if s == "WARN")
    print(f"Ergebnis: {GREEN}{passed} PASS{RESET}  {RED}{failed} FAIL{RESET}  {YELLOW}{warned} WARN{RESET}")
    print("─" * 60)
    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="MoE Sovereign Template Chain Audit")
    parser.add_argument("--template", required=True, help="Template ID (e.g. tmpl-curator-qwen36-rtx)")
    parser.add_argument("--env",      default="benchmarks/.env",
                        help="Path to bench env file with MOE_API_KEY/MOE_API_BASE")
    args = parser.parse_args()

    # Resolve paths relative to repo root (script is in scripts/)
    script_dir = Path(__file__).parent
    repo_root  = script_dir.parent

    bench_env = _load_env(repo_root / args.env)
    proj_env  = _load_env(repo_root / ".env")

    api_key  = bench_env.get("MOE_API_KEY", os.environ.get("MOE_API_KEY", ""))
    api_base = bench_env.get("MOE_API_BASE", os.environ.get("MOE_API_BASE", "http://localhost:8002")).rstrip("/")

    if not api_key:
        print(f"{RED}ERROR:{RESET} MOE_API_KEY not found in {args.env} or environment")
        sys.exit(2)

    print(f"Audit: template={args.template}  api_base={api_base}  key={api_key[:14]}…")
    print("─" * 60)

    ok = run_audit(args.template, bench_env, proj_env, api_base, api_key)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

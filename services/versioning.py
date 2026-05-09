"""
services/versioning.py — lakeFS data versioning for knowledge bundles.

Implements a minimal async HTTP client against the lakeFS REST API
(https://docs.lakefs.io/reference/api.html) so that every imported or
exported knowledge bundle is recorded as a content-addressed snapshot
on the ``main`` branch of the ``moe-knowledge`` repository.

## Why
Knowledge bundles flow into Neo4j via ``/graph/knowledge/import``. Once
imported, the source JSON is gone — there is no audit trail of what was
ingested when, which entities a given commit added, or how to revert a
bad import. lakeFS gives us:

- A git-style commit log over bundle JSON
- Content-addressed storage so the same bundle imported twice dedupes
- Branch-per-import for risky imports that can be merged after review
- Rollback by reading the prior commit's bundle JSON and re-importing

## Authentication
HTTP Basic with ``LAKEFS_ACCESS_KEY_ID`` / ``LAKEFS_SECRET_ACCESS_KEY``
(installation credentials wired up in ``docker-compose.enterprise.yml``).

## Graceful degradation
When ``LAKEFS_ENDPOINT`` is empty, every public function is a no-op and
returns ``None`` (or an empty list) without raising. The caller does not
need to gate on ``_enabled()`` — passing through is safe.

## Fire-and-forget
The convenience wrapper :func:`archive_bundle_background` schedules the
commit on the running event loop and never blocks the caller. Failures
are logged at INFO level only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import LAKEFS_ENDPOINT

logger = logging.getLogger("MOE-SOVEREIGN")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_NAME    = os.getenv("LAKEFS_KNOWLEDGE_REPO", "moe-knowledge")
DEFAULT_BRANCH = "main"
# When the repo is auto-created we point it at MinIO under a stable prefix.
STORAGE_NAMESPACE = os.getenv(
    "LAKEFS_KNOWLEDGE_STORAGE_NAMESPACE",
    "s3://lakefs-data/moe-knowledge",
)
REQUEST_TIMEOUT  = 5.0
# Calls to ensure_repository memoise per-process so we hit lakeFS once per repo.
_repo_ready: set = set()


# ---------------------------------------------------------------------------
# Public predicates
# ---------------------------------------------------------------------------

def _enabled() -> bool:
    return bool(LAKEFS_ENDPOINT)


def _auth() -> Optional[Tuple[str, str]]:
    key = os.getenv("LAKEFS_ACCESS_KEY_ID", "")
    sec = os.getenv("LAKEFS_SECRET_ACCESS_KEY", "")
    if not key or not sec:
        return None
    return (key, sec)


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

async def _request(method: str, path: str, *,
                   json_body: Optional[Dict[str, Any]] = None,
                   content: Optional[bytes] = None,
                   content_type: Optional[str] = None,
                   params: Optional[Dict[str, Any]] = None,
                   timeout: float = REQUEST_TIMEOUT) -> Optional[httpx.Response]:
    """Send an authenticated request to lakeFS. Returns None on transport error
    or when versioning is disabled."""
    if not _enabled():
        return None
    auth = _auth()
    if auth is None:
        logger.debug("lakeFS credentials not set — versioning disabled")
        return None
    url = f"{LAKEFS_ENDPOINT.rstrip('/')}{path}"
    headers: Dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type
    try:
        async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
            resp = await client.request(
                method, url, json=json_body, content=content,
                headers=headers, params=params,
            )
            return resp
    except Exception as exc:
        logger.debug("lakeFS request failed: %s %s — %s", method, url, exc)
        return None


# ---------------------------------------------------------------------------
# Repository / branch lifecycle
# ---------------------------------------------------------------------------

async def ensure_repository(repo: str = REPO_NAME,
                            storage_namespace: str = STORAGE_NAMESPACE) -> bool:
    """Create the repository if it does not exist (idempotent)."""
    if not _enabled():
        return False
    if repo in _repo_ready:
        return True
    head = await _request("GET", f"/api/v1/repositories/{repo}")
    if head is not None and head.status_code == 200:
        _repo_ready.add(repo)
        return True
    create = await _request("POST", "/api/v1/repositories", json_body={
        "name":              repo,
        "storage_namespace": storage_namespace,
        "default_branch":    DEFAULT_BRANCH,
    })
    if create is None:
        return False
    if create.status_code in (200, 201, 409):
        _repo_ready.add(repo)
        return True
    logger.info("lakeFS repo create failed: %s %s", create.status_code, create.text[:200])
    return False


# ---------------------------------------------------------------------------
# Bundle archival
# ---------------------------------------------------------------------------

async def archive_bundle(bundle: Dict[str, Any], *,
                         source_tag: str,
                         repo: str = REPO_NAME,
                         branch: str = DEFAULT_BRANCH,
                         metadata: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Upload the bundle JSON and commit it. Returns the commit ID or None."""
    if not _enabled():
        return None
    if not await ensure_repository(repo):
        return None
    ts        = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_tag  = "".join(c if (c.isalnum() or c in "-_") else "_" for c in source_tag)[:64]
    obj_path  = f"bundles/{safe_tag}/{ts}.json"
    payload   = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    upload = await _request(
        "POST",
        f"/api/v1/repositories/{repo}/branches/{branch}/objects",
        params={"path": obj_path},
        content=payload,
        content_type="application/octet-stream",
        timeout=10.0,
    )
    if upload is None or upload.status_code not in (200, 201):
        logger.info("lakeFS upload failed for %s: %s", obj_path,
                    getattr(upload, "status_code", "transport_err"))
        return None

    commit_meta: Dict[str, str] = {
        "source_tag":   source_tag,
        "object_count": str(len(bundle.get("entities", []))),
        "schema":       bundle.get("@context", ""),
    }
    if metadata:
        for k, v in metadata.items():
            commit_meta[str(k)] = str(v)

    commit = await _request(
        "POST",
        f"/api/v1/repositories/{repo}/branches/{branch}/commits",
        json_body={
            "message":  f"archive bundle {source_tag} ({obj_path})",
            "metadata": commit_meta,
        },
    )
    if commit is None or commit.status_code not in (200, 201):
        logger.info("lakeFS commit failed for %s: %s", obj_path,
                    getattr(commit, "status_code", "transport_err"))
        return None
    try:
        return commit.json().get("id")
    except Exception:
        return None


def archive_bundle_background(bundle: Dict[str, Any], *,
                              source_tag: str,
                              metadata: Optional[Dict[str, str]] = None) -> None:
    """Schedule archival on the running loop without blocking the caller."""
    if not _enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(archive_bundle(bundle, source_tag=source_tag, metadata=metadata))


# ---------------------------------------------------------------------------
# Read-side helpers (rollback and audit log)
# ---------------------------------------------------------------------------

async def list_commits(repo: str = REPO_NAME, *,
                       branch: str = DEFAULT_BRANCH,
                       limit: int = 25) -> List[Dict[str, Any]]:
    """Return commit history for the given branch (newest first)."""
    if not _enabled():
        return []
    resp = await _request(
        "GET",
        f"/api/v1/repositories/{repo}/refs/{branch}/commits",
        params={"amount": str(limit)},
    )
    if resp is None or resp.status_code != 200:
        return []
    try:
        return list(resp.json().get("results", []))[:limit]
    except Exception:
        return []


async def get_bundle_at(repo: str, ref: str, path: str) -> Optional[Dict[str, Any]]:
    """Download a bundle JSON at a specific commit. Used for rollback flows."""
    if not _enabled():
        return None
    resp = await _request(
        "GET",
        f"/api/v1/repositories/{repo}/refs/{ref}/objects",
        params={"path": path},
        timeout=10.0,
    )
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None

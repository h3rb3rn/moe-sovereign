"""
pm_connector.py — Backend-agnostic PM adapter for MoE Sovereign MCP tools.

Supports three PM backends selected via PM_BACKEND:
  linear   → Linear GraphQL API  (teams.linear.app)
  github   → GitHub Issues REST  (api.github.com or self-hosted)
  webhook  → Generic JSON webhook (ClickUp, Jira, custom REST APIs)

Required env vars:
  PM_BACKEND      backend selector: linear | github | webhook  (default: "" = disabled)
  PM_API_KEY      Linear personal API key | GitHub PAT | webhook bearer token
  PM_PROJECT_ID   Linear team ID | GitHub "owner/repo" | webhook project slug
  PM_BASE_URL     optional: override default API base URL (for self-hosted GitHub/Linear)
  PM_DEFAULT_LABELS  comma-separated labels added to every created task (default: "moe-ai")
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("MOE-SOVEREIGN.MCP.pm")

_BACKEND     = os.getenv("PM_BACKEND", "").strip().lower()
_API_KEY     = os.getenv("PM_API_KEY", "")
_PROJECT_ID  = os.getenv("PM_PROJECT_ID", "")
_BASE_URL    = os.getenv("PM_BASE_URL", "").rstrip("/")
_DEF_LABELS  = [l.strip() for l in os.getenv("PM_DEFAULT_LABELS", "moe-ai").split(",") if l.strip()]

_LINEAR_URL  = _BASE_URL or "https://api.linear.app/graphql"
_GITHUB_URL  = _BASE_URL or "https://api.github.com"
_WEBHOOK_URL = _BASE_URL  # no default — must be set for webhook backend

_PRIORITY_LINEAR = {"urgent": 1, "high": 2, "medium": 3, "low": 4, "none": 0}


def _not_configured() -> str:
    return (
        "PM connector not configured. "
        "Set PM_BACKEND (linear | github | webhook), PM_API_KEY, "
        "and PM_PROJECT_ID in .env."
    )


# ── Linear backend ────────────────────────────────────────────────────────────

async def _linear_gql(query: str, variables: dict) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(
            _LINEAR_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": _API_KEY, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def _linear_resolve_id(identifier: str) -> str:
    """Resolve a human-readable identifier ('ENG-123') or passthrough UUID."""
    import re
    if re.fullmatch(r"[0-9a-f-]{36}", identifier, re.I):
        return identifier
    data = await _linear_gql(
        "query($id: String!) { issue(id: $id) { id } }",
        {"id": identifier},
    )
    return ((data.get("data") or {}).get("issue") or {}).get("id", identifier)


async def _linear_create(title: str, description: str, labels: list[str], priority: int) -> str:
    q = """
    mutation IssueCreate($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        issue { id identifier title url state { name } }
      }
    }
    """
    data = await _linear_gql(q, {"input": {
        "title": title,
        "description": description,
        "teamId": _PROJECT_ID,
        "priority": priority,
    }})
    issue = ((data.get("data") or {}).get("issueCreate") or {}).get("issue")
    if not issue:
        return f"Linear create failed: {data.get('errors', 'unknown error')}"
    return json.dumps({
        "id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "state": (issue.get("state") or {}).get("name"),
    }, ensure_ascii=False)


async def _linear_list(status: str, limit: int) -> str:
    filters: dict[str, Any] = {"team": {"id": {"eq": _PROJECT_ID}}}
    if status:
        filters["state"] = {"name": {"eq": status}}
    q = """
    query($filter: IssueFilter, $first: Int) {
      issues(filter: $filter, first: $first, orderBy: updatedAt) {
        nodes { id identifier title url state { name } assignee { name } }
      }
    }
    """
    data = await _linear_gql(q, {"filter": filters, "first": limit})
    nodes = ((data.get("data") or {}).get("issues") or {}).get("nodes", [])
    return json.dumps([{
        "id": n.get("id"),
        "identifier": n.get("identifier"),
        "title": n.get("title"),
        "state": (n.get("state") or {}).get("name"),
        "assignee": (n.get("assignee") or {}).get("name"),
        "url": n.get("url"),
    } for n in nodes], ensure_ascii=False, indent=2)


async def _linear_update(task_id: str, status: str, comment: str) -> str:
    uuid = await _linear_resolve_id(task_id)
    parts = []
    if status:
        # Resolve state name → stateId
        sq = "query($teamId: String!) { workflowStates(filter:{team:{id:{eq:$teamId}}}) { nodes { id name } } }"
        sdata = await _linear_gql(sq, {"teamId": _PROJECT_ID})
        states = ((sdata.get("data") or {}).get("workflowStates") or {}).get("nodes", [])
        state_id = next((s["id"] for s in states if s.get("name", "").lower() == status.lower()), None)
        if state_id:
            uq = "mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { issue { state { name } } } }"
            udata = await _linear_gql(uq, {"id": uuid, "input": {"stateId": state_id}})
            new_state = (((udata.get("data") or {}).get("issueUpdate") or {}).get("issue") or {}).get("state", {}).get("name", status)
            parts.append(f"status → '{new_state}'")
        else:
            parts.append(f"state '{status}' not found in workflow — skipped")
    if comment:
        cq = "mutation($input: CommentCreateInput!) { commentCreate(input: $input) { comment { id } } }"
        await _linear_gql(cq, {"input": {"issueId": uuid, "body": comment}})
        parts.append("comment added")
    return "; ".join(parts) if parts else "nothing to update"


async def _linear_search(query: str, limit: int) -> str:
    q = """
    query($term: String!, $teamId: String, $first: Int) {
      issueSearch(query: $term, filter: {team: {id: {eq: $teamId}}}, first: $first) {
        nodes { id identifier title url state { name } }
      }
    }
    """
    data = await _linear_gql(q, {"term": query, "teamId": _PROJECT_ID, "first": limit})
    nodes = ((data.get("data") or {}).get("issueSearch") or {}).get("nodes", [])
    return json.dumps([{
        "id": n.get("id"),
        "identifier": n.get("identifier"),
        "title": n.get("title"),
        "state": (n.get("state") or {}).get("name"),
        "url": n.get("url"),
    } for n in nodes], ensure_ascii=False, indent=2)


# ── GitHub Issues backend ─────────────────────────────────────────────────────

def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json", "X-GitHub-Api-Version": "2022-11-28"}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


async def _github_create(title: str, description: str, labels: list[str], assignee: str) -> str:
    body: dict[str, Any] = {"title": title, "body": description, "labels": labels}
    if assignee:
        body["assignees"] = [assignee]
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(
            f"{_GITHUB_URL}/repos/{_PROJECT_ID}/issues",
            json=body, headers=_gh_headers(),
        )
        r.raise_for_status()
        d = r.json()
    return json.dumps({
        "id": str(d.get("number")),
        "title": d.get("title"),
        "url": d.get("html_url"),
        "state": d.get("state"),
    }, ensure_ascii=False)


async def _github_list(status: str, assignee: str, label: str, limit: int) -> str:
    params: dict[str, Any] = {"state": status or "open", "per_page": min(limit, 100)}
    if assignee:
        params["assignee"] = assignee
    if label:
        params["labels"] = label
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(
            f"{_GITHUB_URL}/repos/{_PROJECT_ID}/issues",
            params=params, headers=_gh_headers(),
        )
        r.raise_for_status()
        items = r.json()
    return json.dumps([{
        "id": str(i.get("number")),
        "title": i.get("title"),
        "state": i.get("state"),
        "url": i.get("html_url"),
        "labels": [l.get("name") for l in i.get("labels", [])],
    } for i in items if "pull_request" not in i][:limit], ensure_ascii=False, indent=2)


async def _github_update(task_id: str, status: str, comment: str) -> str:
    parts = []
    async with httpx.AsyncClient(timeout=20.0) as c:
        if status:
            gh_state = "closed" if status.lower() in ("done", "closed", "cancelled", "resolved") else "open"
            r = await c.patch(
                f"{_GITHUB_URL}/repos/{_PROJECT_ID}/issues/{task_id}",
                json={"state": gh_state}, headers=_gh_headers(),
            )
            r.raise_for_status()
            parts.append(f"status → '{gh_state}'")
        if comment:
            r = await c.post(
                f"{_GITHUB_URL}/repos/{_PROJECT_ID}/issues/{task_id}/comments",
                json={"body": comment}, headers=_gh_headers(),
            )
            r.raise_for_status()
            parts.append("comment added")
    return "; ".join(parts) if parts else "nothing to update"


async def _github_search(query: str, limit: int) -> str:
    params = {"q": f"{query} repo:{_PROJECT_ID} is:issue", "per_page": min(limit, 30)}
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(
            f"{_GITHUB_URL}/search/issues",
            params=params, headers=_gh_headers(),
        )
        r.raise_for_status()
        data = r.json()
    return json.dumps([{
        "id": str(i.get("number")),
        "title": i.get("title"),
        "state": i.get("state"),
        "url": i.get("html_url"),
    } for i in data.get("items", [])[:limit]], ensure_ascii=False, indent=2)


# ── Generic webhook backend ───────────────────────────────────────────────────

def _wh_headers() -> dict:
    return {"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"}


async def _webhook_create(title: str, description: str, labels: list[str], priority: str, assignee: str) -> str:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(f"{_WEBHOOK_URL}/tasks", headers=_wh_headers(),
                         json={"title": title, "description": description, "labels": labels,
                               "priority": priority, "assignee": assignee})
        r.raise_for_status()
        return r.text


async def _webhook_list(status: str, assignee: str, label: str, limit: int) -> str:
    params: dict[str, Any] = {"limit": limit}
    if status:   params["status"] = status
    if assignee: params["assignee"] = assignee
    if label:    params["label"] = label
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(f"{_WEBHOOK_URL}/tasks", params=params, headers=_wh_headers())
        r.raise_for_status()
        return r.text


async def _webhook_update(task_id: str, status: str, comment: str) -> str:
    body: dict[str, Any] = {}
    if status:  body["status"] = status
    if comment: body["comment"] = comment
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.patch(f"{_WEBHOOK_URL}/tasks/{task_id}", json=body, headers=_wh_headers())
        r.raise_for_status()
        return r.text


async def _webhook_search(query: str, limit: int) -> str:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(f"{_WEBHOOK_URL}/tasks/search",
                        params={"q": query, "limit": limit}, headers=_wh_headers())
        r.raise_for_status()
        return r.text


# ── Public dispatch API ───────────────────────────────────────────────────────

async def create_task(
    title: str,
    description: str = "",
    labels: list | None = None,
    priority: str = "medium",
    assignee: str = "",
) -> str:
    if not _BACKEND:
        return _not_configured()
    merged = list(set((labels or []) + _DEF_LABELS))
    try:
        if _BACKEND == "linear":
            return await _linear_create(title, description, merged, _PRIORITY_LINEAR.get(priority.lower(), 3))
        if _BACKEND == "github":
            return await _github_create(title, description, merged, assignee)
        if _BACKEND == "webhook":
            return await _webhook_create(title, description, merged, priority, assignee)
        return f"Unknown PM_BACKEND '{_BACKEND}'. Use: linear | github | webhook"
    except Exception as exc:
        logger.error("pm.create_task error: %s", exc)
        return f"PM create_task error: {exc}"


async def list_tasks(
    status: str = "",
    assignee: str = "",
    label: str = "",
    limit: int = 20,
) -> str:
    if not _BACKEND:
        return _not_configured()
    try:
        if _BACKEND == "linear":
            return await _linear_list(status, limit)
        if _BACKEND == "github":
            return await _github_list(status, assignee, label, limit)
        if _BACKEND == "webhook":
            return await _webhook_list(status, assignee, label, limit)
        return f"Unknown PM_BACKEND '{_BACKEND}'"
    except Exception as exc:
        logger.error("pm.list_tasks error: %s", exc)
        return f"PM list_tasks error: {exc}"


async def update_task(task_id: str, status: str = "", comment: str = "") -> str:
    if not _BACKEND:
        return _not_configured()
    if not task_id:
        return "Error: task_id is required"
    try:
        if _BACKEND == "linear":
            return await _linear_update(task_id, status, comment)
        if _BACKEND == "github":
            return await _github_update(task_id, status, comment)
        if _BACKEND == "webhook":
            return await _webhook_update(task_id, status, comment)
        return f"Unknown PM_BACKEND '{_BACKEND}'"
    except Exception as exc:
        logger.error("pm.update_task error: %s", exc)
        return f"PM update_task error: {exc}"


async def search_tasks(query: str, limit: int = 10) -> str:
    if not _BACKEND:
        return _not_configured()
    if not query:
        return "Error: query is required"
    try:
        if _BACKEND == "linear":
            return await _linear_search(query, limit)
        if _BACKEND == "github":
            return await _github_search(query, limit)
        if _BACKEND == "webhook":
            return await _webhook_search(query, limit)
        return f"Unknown PM_BACKEND '{_BACKEND}'"
    except Exception as exc:
        logger.error("pm.search_tasks error: %s", exc)
        return f"PM search_tasks error: {exc}"

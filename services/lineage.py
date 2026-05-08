"""
services/lineage.py — OpenLineage event emission to Marquez.

Implements the OpenLineage spec (https://openlineage.io) as a minimal async HTTP
client. Events are POSTed to ``{MARQUEZ_URL}/api/v1/lineage``. All emission is
fire-and-forget: failures are logged at DEBUG level and never propagate to the
caller. When ``MARQUEZ_URL`` is empty, all functions are no-ops.

## Event model

A pipeline run is bracketed by a START event and a COMPLETE (or FAIL) event
sharing the same ``run_id``. Sub-jobs (e.g. ``planner``, ``merger``) emit their
own START/COMPLETE pair with a separate run_id; the parent run is referenced via
the ``parent`` run facet.

## Producer URL

The producer field identifies the system that emitted the event. We use the
project's GitHub URL — Marquez treats this as an opaque identifier.

## Usage

    from services.lineage import start_run, complete_run, fail_run

    run_id = await start_run(
        job_name="chat_completion",
        inputs=[{"namespace": "moe-sovereign", "name": "user_query"}],
    )
    try:
        result = await pipeline(...)
        await complete_run(run_id, job_name="chat_completion",
                           outputs=[{"namespace": "moe-sovereign", "name": "response"}])
    except Exception as e:
        await fail_run(run_id, job_name="chat_completion", error=str(e))
        raise
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from config import MARQUEZ_URL

logger = logging.getLogger("MOE-SOVEREIGN")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NAMESPACE      = "moe-sovereign"
PRODUCER       = "https://github.com/h3rb3rn/moe-sovereign"
SCHEMA_URL     = "https://openlineage.io/spec/2-0-2/OpenLineage.json"
EMIT_TIMEOUT   = 3.0  # seconds — never block the pipeline more than this


def _now_iso() -> str:
    """Return the current UTC time in ISO-8601 with millisecond precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _enabled() -> bool:
    """OpenLineage emission is active iff MARQUEZ_URL is configured."""
    return bool(MARQUEZ_URL)


# ---------------------------------------------------------------------------
# HTTP transport — fire-and-forget POST to /api/v1/lineage
# ---------------------------------------------------------------------------

async def _emit(event: Dict[str, Any]) -> None:
    """POST a fully-formed OpenLineage event to Marquez. Never raises."""
    if not _enabled():
        return
    url = f"{MARQUEZ_URL.rstrip('/')}/api/v1/lineage"
    try:
        async with httpx.AsyncClient(timeout=EMIT_TIMEOUT) as client:
            r = await client.post(url, json=event)
            if r.status_code >= 400:
                logger.debug(
                    "OpenLineage emit %s/%s → HTTP %d: %s",
                    event.get("job", {}).get("name"),
                    event.get("eventType"),
                    r.status_code,
                    r.text[:200],
                )
    except Exception as e:  # network down, Marquez restarting, etc.
        logger.debug("OpenLineage emit failed: %s", e)


def _emit_background(event: Dict[str, Any]) -> None:
    """Schedule _emit() as a background task. Caller never waits."""
    if not _enabled():
        return
    try:
        asyncio.get_running_loop().create_task(_emit(event))
    except RuntimeError:
        # No running loop — silently drop (e.g. called from sync context outside FastAPI)
        pass


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _dataset(name: str, *, namespace: Optional[str] = None,
             facets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build an OpenLineage dataset reference."""
    d: Dict[str, Any] = {"namespace": namespace or NAMESPACE, "name": name}
    if facets:
        d["facets"] = facets
    return d


def _build_event(
    *,
    event_type: str,
    run_id: str,
    job_name: str,
    inputs: Optional[List[Dict[str, Any]]] = None,
    outputs: Optional[List[Dict[str, Any]]] = None,
    parent_run_id: Optional[str] = None,
    parent_job_name: Optional[str] = None,
    error: Optional[str] = None,
    extra_run_facets: Optional[Dict[str, Any]] = None,
    extra_job_facets: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct an OpenLineage event payload."""
    run_facets: Dict[str, Any] = {}
    if parent_run_id and parent_job_name:
        run_facets["parent"] = {
            "_producer": PRODUCER,
            "_schemaURL": f"{SCHEMA_URL}#/$defs/ParentRunFacet",
            "run": {"runId": parent_run_id},
            "job": {"namespace": NAMESPACE, "name": parent_job_name},
        }
    if error:
        run_facets["errorMessage"] = {
            "_producer": PRODUCER,
            "_schemaURL": f"{SCHEMA_URL}#/$defs/ErrorMessageRunFacet",
            "message":     error[:1000],
            "programmingLanguage": "python",
        }
    if extra_run_facets:
        run_facets.update(extra_run_facets)

    job_facets = extra_job_facets or {}

    event: Dict[str, Any] = {
        "eventType": event_type,
        "eventTime": _now_iso(),
        "producer":  PRODUCER,
        "schemaURL": SCHEMA_URL,
        "run":       {"runId": run_id, **({"facets": run_facets} if run_facets else {})},
        "job":       {"namespace": NAMESPACE, "name": job_name,
                      **({"facets": job_facets} if job_facets else {})},
        "inputs":    inputs or [],
        "outputs":   outputs or [],
    }
    return event


# ---------------------------------------------------------------------------
# Public API — coroutines that callers await (with fire-and-forget option)
# ---------------------------------------------------------------------------

async def start_run(
    job_name: str,
    *,
    run_id: Optional[str] = None,
    inputs: Optional[List[Dict[str, Any]]] = None,
    parent_run_id: Optional[str] = None,
    parent_job_name: Optional[str] = None,
    extra_facets: Optional[Dict[str, Any]] = None,
) -> str:
    """Emit a START event and return the run_id (generated if not supplied).

    Returns the run_id even when emission is disabled — callers can pair it with
    a later complete_run/fail_run regardless of whether Marquez is reachable.
    """
    rid = run_id or str(uuid.uuid4())
    event = _build_event(
        event_type="START",
        run_id=rid,
        job_name=job_name,
        inputs=inputs,
        parent_run_id=parent_run_id,
        parent_job_name=parent_job_name,
        extra_run_facets=extra_facets,
    )
    _emit_background(event)
    return rid


async def complete_run(
    run_id: str,
    *,
    job_name: str,
    outputs: Optional[List[Dict[str, Any]]] = None,
    extra_facets: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a COMPLETE event for the given run_id."""
    event = _build_event(
        event_type="COMPLETE",
        run_id=run_id,
        job_name=job_name,
        outputs=outputs,
        extra_run_facets=extra_facets,
    )
    _emit_background(event)


async def fail_run(
    run_id: str,
    *,
    job_name: str,
    error: str,
    outputs: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Emit a FAIL event with an error message run facet."""
    event = _build_event(
        event_type="FAIL",
        run_id=run_id,
        job_name=job_name,
        outputs=outputs,
        error=error,
    )
    _emit_background(event)


# ---------------------------------------------------------------------------
# High-level helpers — typed dataset constructors for common MoE entities
# ---------------------------------------------------------------------------

def dataset_user_query(session_id: str = "") -> Dict[str, Any]:
    """User input as an OpenLineage dataset.

    Sessions are tracked as separate logical datasets so a Marquez user can see
    one lineage chain per conversation.
    """
    name = f"user_query/{session_id}" if session_id else "user_query"
    return _dataset(name)


def dataset_response(chat_id: str) -> Dict[str, Any]:
    """The synthesized response for one request."""
    return _dataset(f"response/{chat_id}")


def dataset_kg(domain: str = "global") -> Dict[str, Any]:
    """The knowledge graph used as input or output (Neo4j)."""
    return _dataset(f"kg/{domain}", namespace=f"{NAMESPACE}/neo4j")


def dataset_kafka_topic(topic: str) -> Dict[str, Any]:
    """A Kafka topic — used for ingest and audit lineage."""
    return _dataset(topic, namespace=f"{NAMESPACE}/kafka")


def dataset_expert_template(template_id: str) -> Dict[str, Any]:
    """An expert template (config dataset)."""
    return _dataset(f"template/{template_id}", namespace=f"{NAMESPACE}/templates")

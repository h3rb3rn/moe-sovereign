"""
Tests for services/lineage.py — OpenLineage event emission to Marquez.

Strategy: monkey-patch the ``MARQUEZ_URL`` symbol in ``services.lineage`` AND
patch ``_emit`` to capture the constructed event payloads. We never make a real
HTTP call. The contract under test is:

  - ``start_run`` returns a uuid run_id (or the supplied one) and emits a
    well-formed START event via the background task path.
  - ``complete_run`` / ``fail_run`` emit COMPLETE / FAIL events with the same
    run_id.
  - When ``MARQUEZ_URL`` is empty, ``_enabled()`` returns False and no event is
    scheduled — but ``start_run`` still returns a run_id (so callers can pair
    START/COMPLETE without a conditional).
  - Event payloads include: producer, schemaURL, run.runId, job.namespace,
    job.name, eventTime in ISO-8601, and inputs/outputs as supplied.
"""

import asyncio
import re
import uuid
from typing import List

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

ISO_8601_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.fixture
def captured_events(monkeypatch):
    """Capture every event scheduled via _emit_background → _emit."""
    from services import lineage

    events: List[dict] = []

    async def _capture(event):
        events.append(event)

    monkeypatch.setattr(lineage, "_emit", _capture)
    # Mark Marquez as configured so _emit_background actually schedules
    monkeypatch.setattr(lineage, "MARQUEZ_URL", "http://marquez-fake:5000")
    return events


async def _drain():
    """Yield control so background tasks scheduled with create_task complete."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ─────────────────────────────────────────────────────────────────────────────
# start_run / complete_run / fail_run
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_run_emits_well_formed_event(captured_events):
    from services.lineage import start_run

    run_id = await start_run(
        "chat_completion",
        inputs=[{"namespace": "moe-sovereign", "name": "user_query/abc"}],
    )
    await _drain()

    assert isinstance(run_id, str) and len(run_id) == 36
    assert len(captured_events) == 1
    ev = captured_events[0]

    assert ev["eventType"] == "START"
    assert ev["job"]["namespace"] == "moe-sovereign"
    assert ev["job"]["name"] == "chat_completion"
    assert ev["run"]["runId"] == run_id
    assert ev["inputs"] == [{"namespace": "moe-sovereign", "name": "user_query/abc"}]
    assert ev["outputs"] == []
    assert ev["producer"].startswith("https://")
    assert "openlineage.io/spec" in ev["schemaURL"]
    assert ISO_8601_MS.match(ev["eventTime"]), f"bad ISO timestamp: {ev['eventTime']}"


@pytest.mark.asyncio
async def test_complete_run_carries_run_id_and_outputs(captured_events):
    from services.lineage import start_run, complete_run

    rid = await start_run("merger")
    await complete_run(
        rid,
        job_name="merger",
        outputs=[{"namespace": "moe-sovereign", "name": "response/xyz"}],
    )
    await _drain()

    assert len(captured_events) == 2
    start_ev, complete_ev = captured_events
    assert start_ev["run"]["runId"] == rid
    assert complete_ev["run"]["runId"] == rid
    assert complete_ev["eventType"] == "COMPLETE"
    assert complete_ev["outputs"][0]["name"] == "response/xyz"


@pytest.mark.asyncio
async def test_fail_run_includes_error_facet(captured_events):
    from services.lineage import start_run, fail_run

    rid = await start_run("planner")
    await fail_run(rid, job_name="planner", error="boom: planner timeout")
    await _drain()

    fail_ev = captured_events[-1]
    assert fail_ev["eventType"] == "FAIL"
    assert "errorMessage" in fail_ev["run"]["facets"]
    assert "boom" in fail_ev["run"]["facets"]["errorMessage"]["message"]


@pytest.mark.asyncio
async def test_parent_run_facet_links_subjob_to_parent(captured_events):
    from services.lineage import start_run

    parent_rid = str(uuid.uuid4())
    child_rid = await start_run(
        "expert_worker",
        parent_run_id=parent_rid,
        parent_job_name="chat_completion",
    )
    await _drain()

    ev = captured_events[0]
    parent_facet = ev["run"]["facets"]["parent"]
    assert parent_facet["run"]["runId"] == parent_rid
    assert parent_facet["job"]["name"] == "chat_completion"
    assert child_rid != parent_rid


# ─────────────────────────────────────────────────────────────────────────────
# Disabled mode (no MARQUEZ_URL configured)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_when_marquez_url_empty(monkeypatch):
    from services import lineage

    captured: List[dict] = []

    async def _capture(event):
        captured.append(event)

    monkeypatch.setattr(lineage, "_emit", _capture)
    monkeypatch.setattr(lineage, "MARQUEZ_URL", "")  # disable

    rid = await lineage.start_run("chat_completion")
    await lineage.complete_run(rid, job_name="chat_completion")
    await _drain()

    # run_id is still returned so callers don't need conditional code
    assert isinstance(rid, str) and len(rid) == 36
    # But no events were scheduled
    assert captured == []


# ─────────────────────────────────────────────────────────────────────────────
# Dataset constructors
# ─────────────────────────────────────────────────────────────────────────────

def test_dataset_constructors_use_correct_namespaces():
    from services.lineage import (
        dataset_user_query, dataset_response, dataset_kg,
        dataset_kafka_topic, dataset_expert_template,
    )

    assert dataset_user_query("sess-1") == {"namespace": "moe-sovereign", "name": "user_query/sess-1"}
    assert dataset_user_query() == {"namespace": "moe-sovereign", "name": "user_query"}
    assert dataset_response("chat-42") == {"namespace": "moe-sovereign", "name": "response/chat-42"}

    kg = dataset_kg("medical")
    assert kg["namespace"] == "moe-sovereign/neo4j"
    assert kg["name"] == "kg/medical"

    topic = dataset_kafka_topic("moe.ingest")
    assert topic["namespace"] == "moe-sovereign/kafka"
    assert topic["name"] == "moe.ingest"

    tmpl = dataset_expert_template("legal-de")
    assert tmpl["namespace"] == "moe-sovereign/templates"
    assert tmpl["name"] == "template/legal-de"


# ─────────────────────────────────────────────────────────────────────────────
# Fire-and-forget guarantee — emission failure must not propagate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_failure_is_swallowed(monkeypatch):
    """If _emit raises, callers must NOT see the exception."""
    from services import lineage

    monkeypatch.setattr(lineage, "MARQUEZ_URL", "http://marquez-fake:5000")

    async def _explode(event):
        raise RuntimeError("simulated marquez outage")

    monkeypatch.setattr(lineage, "_emit", _explode)

    # Should complete cleanly even though _emit raises in the background task.
    rid = await lineage.start_run("chat_completion")
    await lineage.complete_run(rid, job_name="chat_completion")
    await _drain()

    assert isinstance(rid, str)

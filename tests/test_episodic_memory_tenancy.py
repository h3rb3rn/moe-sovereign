"""tests/test_episodic_memory_tenancy.py — Regression for episodic_memory.py
retrieval/dedup having no owner scoping.

get_episode_hint() stored user_id on write (_STORE_EPISODE) but never filtered
on it during retrieval (_QUERY_EPISODES / _QUERY_EPISODES_FALLBACK) — any
user's episodic routing-history hints (routing path, tools used, model,
confidence) were visible to any other user whose query matched the same
task_type/similarity. _episode_hash also didn't include the owner, so two
different users asking an identical-looking question collided into the same
Episode node, letting one user's query bump another's confidence/recall_count.

Found during external review (GAP_REPORT_2026-09-11.md, GAP-04).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import episodic_memory as em


def _make_driver_with_mock_session(run_side_effect=None):
    """A fake Neo4j async driver whose driver.session() yields a mock session
    with the given session.run(...) behavior (mirrors the pattern already
    used in tests/test_graph_rag_import_relations_created.py)."""
    session = AsyncMock()
    if run_side_effect is not None:
        session.run = AsyncMock(side_effect=run_side_effect)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock()
    driver.session = MagicMock(return_value=session_cm)
    return driver, session


def _empty_result():
    result = AsyncMock()
    result.__aiter__.return_value = iter([])
    return result


def test_episode_hash_differs_by_user():
    """Same query + task_type but different owners must not collide."""
    h_alice = em._episode_hash("deploy the service", "devops", "alice")
    h_bob = em._episode_hash("deploy the service", "devops", "bob")
    assert h_alice != h_bob


def test_episode_hash_stable_for_same_user():
    h1 = em._episode_hash("deploy the service", "devops", "alice")
    h2 = em._episode_hash("deploy the service", "devops", "alice")
    assert h1 == h2


@pytest.mark.asyncio
async def test_get_episode_hint_fails_closed_without_user_id(monkeypatch):
    """Missing/empty user_id must return "" without ever querying Neo4j —
    there is no safe unscoped default here."""
    monkeypatch.setattr(em, "_ENABLED", True)
    driver, session = _make_driver_with_mock_session()

    result = await em.get_episode_hint(driver, "some query", "general", "")

    assert result == ""
    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_get_episode_hint_scopes_query_to_user_id(monkeypatch):
    """The Cypher params passed to session.run must carry the caller's
    user_id, so retrieval is scoped server-side, not just in Python."""
    monkeypatch.setattr(em, "_ENABLED", True)
    captured = {}

    async def _run(query, params):
        captured["query"] = query
        captured["params"] = params
        return _empty_result()

    driver, _session = _make_driver_with_mock_session(_run)

    await em.get_episode_hint(driver, "some query", "general", "alice")

    assert captured["params"]["user_id"] == "alice"
    assert "ep.user_id = $user_id" in captured["query"]


@pytest.mark.asyncio
async def test_get_episode_hint_fallback_also_scopes_to_user_id(monkeypatch):
    """The no-APOC fallback query must carry the same owner scoping as the
    primary similarity query, not just the primary path."""
    monkeypatch.setattr(em, "_ENABLED", True)
    captured = {}
    call_count = {"n": 0}

    async def _run(query, params):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("apoc.text.sorensenDiceSimilarity not found (no APOC)")
        captured["query"] = query
        captured["params"] = params
        return _empty_result()

    driver, _session = _make_driver_with_mock_session(_run)

    await em.get_episode_hint(driver, "some query", "general", "alice")

    assert captured["params"]["user_id"] == "alice"
    assert "ep.user_id = $user_id" in captured["query"]


@pytest.mark.asyncio
async def test_log_episode_stores_the_per_user_hash(monkeypatch):
    """log_episode must key the stored Episode on the same per-user hash
    get_episode_hint/dedup rely on, not a bare query+task_type hash."""
    monkeypatch.setattr(em, "_ENABLED", True)
    captured = {}

    async def _run(query, params):
        captured["params"] = params
        return AsyncMock()

    driver, _session = _make_driver_with_mock_session(_run)
    state = {
        "plan": [{"category": "devops"}],
        "input": "deploy the service",
        "user_id": "alice",
        "expert_results": [],
        "final_response": "x" * 700,  # push confidence above the 0.3 floor
    }

    await em.log_episode(driver, state)

    assert captured["params"]["user_id"] == "alice"
    assert captured["params"]["hash"] == em._episode_hash("deploy the service", "devops", "alice")

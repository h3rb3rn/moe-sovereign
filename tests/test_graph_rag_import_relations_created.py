"""tests/test_graph_rag_import_relations_created.py — Regression for
graph_rag/manager.py::import_knowledge_bundle over-reporting relations_created.

MATCH (s:Entity {name: $s}), (o:Entity {name: $o}) silently returns zero rows
(no exception) when either endpoint doesn't resolve, so MERGE never runs —
but stats["relations_created"] was incremented unconditionally regardless.
Observed live: 1286 reported created, only 12 actually landed (+231
pre-existing = 243 total). Fix: check the write summary's
counters.relationships_created before counting a relation as created.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from graph_rag.manager import GraphRAGManager


def _make_manager_with_mock_session(run_side_effect):
    """A GraphRAGManager whose self.driver.session() yields a mock session
    with the given `session.run(...)` behavior. neo4j's driver() call is
    lazy (no network I/O), so real init + a post-hoc driver swap is safe."""
    manager = GraphRAGManager("bolt://unused:7687", "neo4j", "unused")
    session = AsyncMock()
    session.run = AsyncMock(side_effect=run_side_effect)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    manager.driver = MagicMock()
    manager.driver.session = MagicMock(return_value=session_cm)
    return manager, session


def _relation_result(relationships_created: int):
    """A session.run() result whose consume() reports the given counters —
    the real signal for whether MERGE actually created anything."""
    summary = MagicMock()
    summary.counters.relationships_created = relationships_created
    result = AsyncMock()
    result.consume = AsyncMock(return_value=summary)
    return result


@pytest.mark.asyncio
async def test_relation_not_counted_when_endpoints_dont_resolve():
    # MATCH (s),(o) found nothing -> MERGE never ran -> 0 relationships_created.
    manager, _session = _make_manager_with_mock_session([_relation_result(0)])
    bundle = {
        "entities": [],
        "relations": [{"subject": "Ghost", "predicate": "RELATES_TO", "object": "Nowhere",
                       "trust_score": 0.6}],
    }
    stats = await manager.import_knowledge_bundle(bundle)
    assert stats["relations_created"] == 0
    assert stats["relations_skipped"] == 1


@pytest.mark.asyncio
async def test_relation_counted_when_actually_created():
    manager, _session = _make_manager_with_mock_session([_relation_result(1)])
    bundle = {
        "entities": [],
        "relations": [{"subject": "A", "predicate": "RELATES_TO", "object": "B",
                       "trust_score": 0.6}],
    }
    stats = await manager.import_knowledge_bundle(bundle)
    assert stats["relations_created"] == 1
    assert stats["relations_skipped"] == 0

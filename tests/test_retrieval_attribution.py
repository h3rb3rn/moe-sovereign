"""End-to-end contracts for GraphRAG retrieval attribution."""

import pytest

from services.retrieval_attribution import (
    chunk_used_in_answer,
    graph_attribution_chunks,
    record_attribution,
)


class _FakeSession:
    def __init__(self):
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query, **params):
        self.queries.append((query, params))


class _FakeDriver:
    def __init__(self):
        self.session_instance = _FakeSession()

    def session(self):
        return self.session_instance


def test_graph_attribution_chunks_extracts_identifiable_entities():
    context = (
        "[Knowledge Graph]\n"
        "• Python (Framework): IMPLEMENTS CPython runtime and standard library\n"
        "  ↳ Language → RELATED_TO → Programming\n"
        "• DORA (Law): REGULATES financial organisations"
    )

    chunks = graph_attribution_chunks(context)

    assert [chunk["id"] for chunk in chunks] == ["Python", "DORA"]
    assert all(chunk["id_field"] == "name" for chunk in chunks)
    assert chunks[0]["text"].startswith("• Python")


def test_chunk_usage_requires_meaningful_overlap():
    chunk = "Python framework implements CPython runtime and standard library"
    assert chunk_used_in_answer(chunk, "CPython is Python's runtime with a standard library")
    assert not chunk_used_in_answer(chunk, "The weather is sunny today")


@pytest.mark.asyncio
async def test_record_attribution_updates_entity_hit_and_miss(monkeypatch):
    monkeypatch.setenv("MOE_RETRIEVAL_ATTRIBUTION", "1")
    driver = _FakeDriver()
    chunks = [
        {
            "id": "Python",
            "id_field": "name",
            "text": "Python framework implements CPython runtime and standard library",
        },
        {
            "id": "DORA",
            "id_field": "name",
            "text": "DORA regulation defines operational resilience requirements",
        },
    ]

    await record_attribution(
        driver,
        chunks,
        "Python uses the CPython runtime and includes a standard library.",
    )

    assert len(driver.session_instance.queries) == 2
    hit_query, hit_params = driver.session_instance.queries[0]
    miss_query, miss_params = driver.session_instance.queries[1]
    assert "n.name IN $ids" in hit_query
    assert "hit_count" in hit_query
    assert hit_params["ids"] == ["Python"]
    assert "miss_count" in miss_query
    assert miss_params["ids"] == ["DORA"]

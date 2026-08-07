"""no_cache must bypass every cache layer, including Chroma soft lookup."""

from unittest.mock import AsyncMock

import pytest

import graph.router_nodes as router_nodes


class _ExplodingCollection:
    def query(self, **_kwargs):
        raise AssertionError("L1 cache query must not run")


@pytest.mark.asyncio
async def test_no_cache_returns_before_l0_and_l1(monkeypatch):
    record_stage = AsyncMock()
    monkeypatch.setattr(router_nodes, "_record_stage", record_stage)
    monkeypatch.setattr(router_nodes.state, "cache_collection", _ExplodingCollection())
    monkeypatch.setattr(router_nodes.state, "redis_client", None)

    result = await router_nodes.cache_lookup_node(
        {
            "input": "fresh benchmark request",
            "response_id": "req-no-cache",
            "mode": "default",
            "enable_cache": True,
            "no_cache": True,
        }
    )

    assert result == {
        "cached_facts": "",
        "cache_hit": False,
        "soft_cache_examples": "",
    }
    assert record_stage.await_args.args[2] == "bypassed"

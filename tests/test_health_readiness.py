"""Readiness is deeper than the process liveness endpoint."""

from types import SimpleNamespace

import pytest

import state
from routes.health import _readiness_checks


class _Redis:
    async def ping(self):
        return True


class _Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _query):
        return None


class _Pool:
    def connection(self):
        return _Connection()


class _Driver:
    async def verify_connectivity(self):
        return None


class _Response:
    def raise_for_status(self):
        return None


class _Http:
    async def get(self, _url):
        return _Response()


@pytest.mark.asyncio
async def test_readiness_checks_all_configured_dependencies(monkeypatch):
    monkeypatch.setattr(state, "app_graph", object())
    monkeypatch.setattr(state, "redis_client", _Redis())
    monkeypatch.setattr(state, "_userdb_pool", _Pool())
    monkeypatch.setattr(
        state, "graph_manager", SimpleNamespace(driver=_Driver())
    )
    monkeypatch.setattr(state, "http_client", _Http())
    monkeypatch.setattr(state, "cache_collection", object())

    checks, critical_ready = await _readiness_checks()

    assert critical_ready is True
    assert all(check["ok"] for check in checks.values())


@pytest.mark.asyncio
async def test_readiness_fails_closed_when_graph_is_not_compiled(monkeypatch):
    monkeypatch.setattr(state, "app_graph", None)
    monkeypatch.setattr(state, "redis_client", _Redis())
    monkeypatch.setattr(state, "_userdb_pool", _Pool())
    monkeypatch.setattr(state, "graph_manager", None)
    monkeypatch.setattr(state, "http_client", _Http())
    monkeypatch.setattr(state, "cache_collection", None)

    checks, critical_ready = await _readiness_checks()

    assert critical_ready is False
    assert checks["orchestration_graph"]["ok"] is False
    assert checks["neo4j"]["critical"] is False


@pytest.mark.asyncio
async def test_readiness_fails_closed_when_boundary_contracts_are_invalid(monkeypatch):
    monkeypatch.setattr(state, "app_graph", object())
    monkeypatch.setattr(state, "redis_client", _Redis())
    monkeypatch.setattr(state, "_userdb_pool", _Pool())
    monkeypatch.setattr(state, "graph_manager", None)
    monkeypatch.setattr(state, "http_client", _Http())
    monkeypatch.setattr(state, "cache_collection", None)
    monkeypatch.setattr(
        "routes.health.validate_boundary_contracts",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid contracts")),
    )

    checks, critical_ready = await _readiness_checks()

    assert critical_ready is False
    assert checks["boundary_contracts"] == {
        "ok": False,
        "critical": True,
        "detail": "invalid contracts",
    }

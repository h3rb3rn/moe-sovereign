"""tests/test_rust_loom_mcp_tool.py — Unit tests for the rust_loom_check MCP
tool wrapper (mcp_server/server.py). Mirrors the untested rust_compile_check
wrapper's shape: input validation raises ValueError before any sandbox call,
a successful sandbox response is reduced to the documented facts dict, and a
sandbox/transport failure fails open (compiles=None, passed=None) instead of
raising.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from mcp_server.server import rust_loom_check


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *args, **kwargs):
        if self._exc:
            raise self._exc
        return _FakeResponse(self._payload)


class TestRustLoomCheckValidation:
    @pytest.mark.asyncio
    async def test_rejects_empty_source(self):
        with pytest.raises(ValueError, match="source_must_be_non_empty_string"):
            await rust_loom_check("")

    @pytest.mark.asyncio
    async def test_rejects_non_string_source(self):
        with pytest.raises(ValueError, match="source_must_be_non_empty_string"):
            await rust_loom_check(None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_rejects_oversized_source(self):
        with pytest.raises(ValueError, match="source_exceeds_size_limit"):
            await rust_loom_check("x" * 200_001)

    @pytest.mark.asyncio
    async def test_rejects_unsupported_edition(self):
        with pytest.raises(ValueError, match="unsupported_edition"):
            await rust_loom_check("fn x() {}", edition="2018")


class TestRustLoomCheckSandboxCall:
    @pytest.mark.asyncio
    async def test_success_reduces_to_documented_facts(self, monkeypatch):
        import mcp_server.server as server_mod

        fake_result = {
            "compiles": True, "passed": False,
            "output_tail": "test result: FAILED. 0 passed; 1 failed;",
            "duration_ms": 4200, "timed_out": False,
        }
        monkeypatch.setattr(
            server_mod.httpx, "AsyncClient",
            lambda *a, **k: _FakeAsyncClient(payload=fake_result),
        )
        raw = await rust_loom_check("use loom::sync::Arc;\n#[test]\nfn t() {}")
        facts = json.loads(raw)
        assert facts["compiles"] is True
        assert facts["passed"] is False
        assert facts["duration_ms"] == 4200
        assert "source_hash" in facts and len(facts["source_hash"]) == 64

    @pytest.mark.asyncio
    async def test_sandbox_failure_fails_open(self, monkeypatch):
        import mcp_server.server as server_mod

        monkeypatch.setattr(
            server_mod.httpx, "AsyncClient",
            lambda *a, **k: _FakeAsyncClient(exc=ConnectionError("sandbox unreachable")),
        )
        raw = await rust_loom_check("use loom::sync::Arc;\n#[test]\nfn t() {}")
        facts = json.loads(raw)
        assert facts["compiles"] is None
        assert facts["passed"] is None
        assert "sandbox_error" in facts

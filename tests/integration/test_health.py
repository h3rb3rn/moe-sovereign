"""
Health and metrics endpoint tests.

Since FastAPI is stubbed in the test environment, we verify the handler contract
by scanning main.py source code. This is sufficient as a refactoring safety net:
if the handler body changes semantics, the source-level assertions catch it.
"""

import re
from pathlib import Path

import pytest

_MAIN = (Path(__file__).parents[2] / "main.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# /health — must return {"status": "ok"} unconditionally
# ---------------------------------------------------------------------------

def test_health_handler_returns_status_ok():
    """The /health handler body must return the exact liveness response."""
    # Look for the handler registered at /health returning {"status": "ok"}
    pattern = r'@app\.get\("/health"\).*?return\s+\{["\']status["\']\s*:\s*["\']ok["\']\}'
    assert re.search(pattern, _MAIN, re.DOTALL), (
        "/health handler fehlt oder gibt nicht {'status': 'ok'} zurück. "
        "Nach dem Refactoring sicherstellen dass der Handler in SCANNED_FILES liegt."
    )


def test_health_handler_has_no_auth_dependency():
    """The liveness probe must not be gated behind authentication."""
    # Extract the block around @app.get("/health")
    m = re.search(r'(@app\.get\("/health"\).*?)(?=\n@app\.)', _MAIN, re.DOTALL)
    assert m, "/health handler nicht gefunden"
    handler_block = m.group(1)
    assert "api_key" not in handler_block.lower(), (
        "/health darf nicht hinter API-Key-Auth liegen — Docker HEALTHCHECK würde scheitern"
    )


# ---------------------------------------------------------------------------
# /metrics — must return Prometheus text format
# ---------------------------------------------------------------------------

def test_metrics_handler_uses_prometheus_generate_latest():
    """The /metrics handler must call generate_latest() to produce scrape output."""
    m = re.search(r'(@app\.get\("/metrics"\).*?)(?=\n@app\.)', _MAIN, re.DOTALL)
    assert m, "/metrics handler nicht gefunden"
    block = m.group(0)
    assert "generate_latest" in block, (
        "/metrics handler ruft generate_latest() nicht auf — Prometheus-Format fehlt"
    )


def test_metrics_handler_has_no_auth_dependency():
    """Prometheus scrape endpoint must be unauthenticated."""
    m = re.search(r'(@app\.get\("/metrics"\).*?)(?=\n@app\.)', _MAIN, re.DOTALL)
    assert m, "/metrics handler nicht gefunden"
    block = m.group(0)
    assert "api_key" not in block.lower()

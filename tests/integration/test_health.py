"""
Health and metrics endpoint tests.

Since FastAPI is stubbed in the test environment, we verify the handler contract
by scanning main.py source code. This is sufficient as a refactoring safety net:
if the handler body changes semantics, the source-level assertions catch it.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_MAIN = (
    (_ROOT / "main.py").read_text(encoding="utf-8")
    + (_ROOT / "routes" / "health.py").read_text(encoding="utf-8")
)


# ---------------------------------------------------------------------------
# /health — must return {"status": "ok"} unconditionally
# ---------------------------------------------------------------------------

def test_health_handler_returns_status_ok():
    """The /health handler body must guarantee {"status": "ok"} in its response.

    The handler may return the dict inline, or delegate to a local helper
    (routes/health.py does this — see _health_payload — so the payload stays
    unit-testable despite fastapi being fully stubbed in this environment;
    @router.get on a MagicMock router replaces the decorated name itself with
    a MagicMock, so the helper can't be decorated directly). Either way,
    "status": "ok" must appear, and additional keys (e.g. a build revision)
    are allowed — this only guards the liveness guarantee, not the exact
    literal shape.
    """
    pattern = r'@(?:app|router)\.get\("/health"\)\s*\nasync def (\w+)\(\):(.*?)(?=\n@(?:app|router)\.|\Z)'
    m = re.search(pattern, _MAIN, re.DOTALL)
    assert m, "/health handler nicht gefunden"
    handler_body = m.group(2)

    delegate = re.search(r'return\s+(\w+)\(\)', handler_body)
    if delegate:
        helper_name = delegate.group(1)
        helper_pattern = rf'def {re.escape(helper_name)}\(.*?\).*?(?=\ndef |\nasync def |\Z)'
        helper_m = re.search(helper_pattern, _MAIN, re.DOTALL)
        assert helper_m, f"/health delegates to {helper_name}(), but its definition was not found"
        searched = helper_m.group(0)
    else:
        searched = handler_body

    assert re.search(r'["\']status["\']\s*:\s*["\']ok["\']', searched), (
        "/health handler fehlt oder garantiert nicht \"status\": \"ok\". "
        "Nach dem Refactoring sicherstellen dass der Handler in SCANNED_FILES liegt."
    )


def test_health_handler_has_no_auth_dependency():
    """The liveness probe must not be gated behind authentication."""
    # Extract the block around @app.get("/health")
    m = re.search(r'(@(?:app|router)\.get\("/health"\).*?)(?=\n@(?:app|router)\.|\Z)', _MAIN, re.DOTALL)
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
    m = re.search(r'(@(?:app|router)\.get\("/metrics"\).*?)(?=\n@(?:app|router)\.|\Z)', _MAIN, re.DOTALL)
    assert m, "/metrics handler nicht gefunden"
    block = m.group(0)
    assert "generate_latest" in block, (
        "/metrics handler ruft generate_latest() nicht auf — Prometheus-Format fehlt"
    )


def test_metrics_handler_has_no_auth_dependency():
    """Prometheus scrape endpoint must be unauthenticated."""
    m = re.search(r'(@(?:app|router)\.get\("/metrics"\).*?)(?=\n@(?:app|router)\.|\Z)', _MAIN, re.DOTALL)
    assert m, "/metrics handler nicht gefunden"
    block = m.group(0)
    assert "api_key" not in block.lower()

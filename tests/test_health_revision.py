"""tests/test_health_revision.py — GET /health exposes a non-secret build
identity (GIT_REVISION) so a running container can be correlated back to the
exact source commit it was built from.

Found during external review (GAP_REPORT_2026-09-11.md, GAP-10): the image
carried no revision label or runtime-visible identity at all.
"""

from routes.health import _health_payload


def test_health_defaults_to_unknown_revision(monkeypatch):
    monkeypatch.delenv("GIT_REVISION", raising=False)
    result = _health_payload()
    assert result["status"] == "ok"
    assert result["revision"] == "unknown"


def test_health_reports_configured_revision(monkeypatch):
    monkeypatch.setenv("GIT_REVISION", "deadbeef1234")
    result = _health_payload()
    assert result["revision"] == "deadbeef1234"

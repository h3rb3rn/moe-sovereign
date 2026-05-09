"""Phase 18 — lakeFS bundle versioning contract tests.

Asserts that the public surface of services.versioning stays stable, that
the import-graph hook is wired in, and that fire-and-forget archival is a
no-op when LAKEFS_ENDPOINT is empty.
"""

import re
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

_ROOT = Path(__file__).parents[2]


# ─── Source-scan contract ────────────────────────────────────────────────────

def test_versioning_module_exists():
    assert (_ROOT / "services" / "versioning.py").exists(), \
        "services/versioning.py must exist for Phase 18"


@pytest.mark.parametrize(
    "symbol",
    [
        "def _enabled(",
        "async def ensure_repository(",
        "async def archive_bundle(",
        "def archive_bundle_background(",
        "async def list_commits(",
        "async def get_bundle_at(",
    ],
)
def test_versioning_public_surface(symbol):
    src = (_ROOT / "services" / "versioning.py").read_text()
    assert symbol in src, f"services/versioning.py missing public symbol: {symbol!r}"


def test_versioning_no_app_dependencies():
    """services/versioning.py must NOT depend on main, routes, or graph_rag.

    Lineage learned the hard way: lazy from-imports inside service modules
    cause hidden circular references. versioning.py must be a leaf module.
    """
    src = (_ROOT / "services" / "versioning.py").read_text()
    forbidden = ["from main import", "from routes", "from graph_rag", "import main"]
    for token in forbidden:
        assert token not in src, f"versioning.py must not depend on {token!r}"


def test_graph_route_archives_after_import():
    """routes/graph.py must invoke archive_bundle_background after a real import."""
    src = (_ROOT / "routes" / "graph.py").read_text()
    assert "from services.versioning import archive_bundle_background" in src, \
        "routes/graph.py missing lakeFS archival hook"
    assert "archive_bundle_background(" in src, \
        "archive_bundle_background must be called from import handler"
    # Must only fire on non-dry-runs — guard the hook.
    assert "if not dry_run" in src, \
        "archive hook must be guarded by `if not dry_run`"


def test_admin_versioning_endpoint_present():
    src = (_ROOT / "admin_ui" / "app.py").read_text()
    assert re.search(r'@app\.get\("/api/enterprise/versioning/log"', src), \
        "Admin UI missing /api/enterprise/versioning/log endpoint"
    assert "LAKEFS_ACCESS_KEY_ID" in src, "Admin endpoint must read LAKEFS_ACCESS_KEY_ID"


def test_enterprise_template_versioning_section():
    body = (_ROOT / "admin_ui" / "templates" / "enterprise.html").read_text()
    assert "enterprise-versioning-body" in body, \
        "enterprise.html missing lakeFS versioning section"
    assert "loadEnterpriseVersioning" in body, \
        "enterprise.html must wire loadEnterpriseVersioning into refresh"


def test_phase18_translation_keys_synced():
    required = [
        "section.bundle_versions",
        "lbl.commit",
        "lbl.message",
        "lbl.author",
        "lbl.committed_at",
        "lbl.lakefs_not_configured",
    ]
    for lang_file in ("en_EN.lang", "de_DE.lang", "fr_FR.lang", "zh_CN.lang"):
        body = (_ROOT / "admin_ui" / "lang" / lang_file).read_text()
        for key in required:
            assert f'"{key}"' in body, f"{lang_file} missing Phase 18 key {key!r}"


# ─── Behavioural tests on services.versioning ────────────────────────────────

@pytest.mark.asyncio
async def test_archive_is_noop_when_disabled(monkeypatch):
    """When LAKEFS_ENDPOINT is empty, archive_bundle returns None without HTTP."""
    monkeypatch.setattr("config.LAKEFS_ENDPOINT", "")
    # Re-import after monkey patch so the module-level constant is fresh.
    import importlib
    import services.versioning as versioning
    importlib.reload(versioning)
    result = await versioning.archive_bundle({"entities": []}, source_tag="x")
    assert result is None
    assert versioning._enabled() is False


@pytest.mark.asyncio
async def test_list_commits_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setattr("config.LAKEFS_ENDPOINT", "")
    import importlib
    import services.versioning as versioning
    importlib.reload(versioning)
    out = await versioning.list_commits()
    assert out == []


@pytest.mark.asyncio
async def test_archive_bundle_uploads_and_commits(monkeypatch):
    """When enabled, archive_bundle issues upload + commit and returns the id."""
    monkeypatch.setattr("config.LAKEFS_ENDPOINT", "http://lakefs.test")
    monkeypatch.setenv("LAKEFS_ACCESS_KEY_ID", "akia-test")
    monkeypatch.setenv("LAKEFS_SECRET_ACCESS_KEY", "secret-test")
    import importlib
    import services.versioning as versioning
    importlib.reload(versioning)

    sequence = [
        # ensure_repository GET → 200 (repo exists)
        type("R", (), {"status_code": 200, "text": "", "json": lambda self: {}})(),
        # upload object → 201
        type("R", (), {"status_code": 201, "text": "", "json": lambda self: {}})(),
        # commit → 201 with id
        type("R", (), {"status_code": 201, "text": "",
                       "json": lambda self: {"id": "abc123def456"}})(),
    ]
    call_count = {"i": 0}

    async def fake_request(self, method, url, **kw):
        i = call_count["i"]
        call_count["i"] += 1
        return sequence[i]

    with patch("httpx.AsyncClient.request", new=fake_request):
        commit_id = await versioning.archive_bundle(
            {"@context": "moe", "entities": [{"name": "x"}]},
            source_tag="unit-test",
        )
    assert commit_id == "abc123def456"
    assert call_count["i"] == 3  # GET repo, POST object, POST commit


@pytest.mark.asyncio
async def test_archive_safe_path_normalisation(monkeypatch):
    """source_tag with slashes/dots must be sanitised before going into the lakeFS path."""
    monkeypatch.setattr("config.LAKEFS_ENDPOINT", "http://lakefs.test")
    monkeypatch.setenv("LAKEFS_ACCESS_KEY_ID", "akia-test")
    monkeypatch.setenv("LAKEFS_SECRET_ACCESS_KEY", "secret-test")
    import importlib
    import services.versioning as versioning
    importlib.reload(versioning)

    captured: dict = {}

    async def fake_request(self, method, url, **kw):
        if "objects" in url and method == "POST":
            captured["params"] = kw.get("params") or {}
            return type("R", (), {"status_code": 201, "text": "", "json": lambda self: {}})()
        if "commits" in url:
            return type("R", (), {"status_code": 201, "text": "",
                                  "json": lambda self: {"id": "deadbeef"}})()
        # ensure_repository hit
        return type("R", (), {"status_code": 200, "text": "", "json": lambda self: {}})()

    with patch("httpx.AsyncClient.request", new=fake_request):
        cid = await versioning.archive_bundle(
            {"entities": []},
            source_tag="../etc/passwd/.././bundle name",
        )
    assert cid == "deadbeef"
    obj_path = captured["params"].get("path", "")
    assert obj_path.startswith("bundles/"), f"unexpected path: {obj_path}"
    assert ".." not in obj_path
    assert "/etc/" not in obj_path

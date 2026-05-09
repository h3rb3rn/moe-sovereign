"""Phase 17 — NiFi ETL pipeline integration tests."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).parents[2]


# ─── Source-scan contract ────────────────────────────────────────────────────

def test_etl_pipeline_module_exists():
    assert (_ROOT / "services" / "etl_pipeline.py").exists(), \
        "services/etl_pipeline.py must exist for Phase 17"


@pytest.mark.parametrize(
    "symbol",
    [
        "def _submit_enabled(",
        "async def submit_to_pipeline(",
        "def submit_to_pipeline_background(",
        "async def get_system_diagnostics(",
        "def summarise_diagnostics(",
        "NIFI_INGEST_URL",
    ],
)
def test_etl_pipeline_public_surface(symbol):
    src = (_ROOT / "services" / "etl_pipeline.py").read_text()
    assert symbol in src, f"services/etl_pipeline.py missing public symbol: {symbol!r}"


def test_etl_pipeline_no_app_dependencies():
    src = (_ROOT / "services" / "etl_pipeline.py").read_text()
    forbidden = ["from main import", "from routes", "from graph_rag", "import main"]
    for token in forbidden:
        assert token not in src, f"etl_pipeline.py must not depend on {token!r}"


def test_kafka_ingest_hook_present():
    """main.py must call submit_to_pipeline_background after a successful kafka ingest."""
    src = (_ROOT / "main.py").read_text()
    assert "from services.etl_pipeline import submit_to_pipeline_background" in src, \
        "kafka_ingest hook missing the etl_pipeline import"
    # Anchor on the actual await calls (not the import block) — `_ol_complete_ingest`
    # and `_ol_fail_ingest` both appear earlier as imports.
    success_idx = src.index("await _ol_complete_ingest(")
    submit_idx  = src.index("from services.etl_pipeline import submit_to_pipeline_background")
    fail_idx    = src.index("await _ol_fail_ingest(")
    assert success_idx < submit_idx < fail_idx, \
        "ETL submit must be between the await complete_ingest and the await fail_ingest"


def test_graph_route_etl_hook():
    src = (_ROOT / "routes" / "graph.py").read_text()
    assert "from services.etl_pipeline import submit_to_pipeline_background" in src
    assert 'source="knowledge_import"' in src


def test_admin_etl_endpoint_present():
    src = (_ROOT / "admin_ui" / "app.py").read_text()
    assert re.search(r'@app\.get\("/api/enterprise/etl/status"', src), \
        "Admin UI missing /api/enterprise/etl/status"
    assert "/nifi-api/access/token" in src
    assert "/nifi-api/system-diagnostics" in src


def test_enterprise_template_etl_section():
    body = (_ROOT / "admin_ui" / "templates" / "enterprise.html").read_text()
    assert "enterprise-etl-body" in body, "enterprise.html missing ETL section"
    assert "loadEnterpriseEtl" in body, "enterprise.html missing loadEnterpriseEtl"


def test_phase17_env_example_documents_ingest_url():
    body = (_ROOT / ".env.example").read_text()
    assert "NIFI_INGEST_URL=" in body
    assert "ListenHTTP" in body, ".env.example must document the ListenHTTP pattern"


def test_phase17_translation_keys_synced():
    required = [
        "section.etl_pipeline",
        "lbl.version",
        "lbl.uptime",
        "lbl.threads",
        "lbl.heap",
        "lbl.ingest",
    ]
    for lang_file in ("en_EN.lang", "de_DE.lang", "fr_FR.lang", "zh_CN.lang"):
        body = (_ROOT / "admin_ui" / "lang" / lang_file).read_text()
        for key in required:
            assert f'"{key}"' in body, f"{lang_file} missing Phase 17 key {key!r}"


# ─── Behavioural tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_is_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("NIFI_INGEST_URL", "")
    import importlib
    import services.etl_pipeline as etl
    importlib.reload(etl)
    out = await etl.submit_to_pipeline({"k": "v"}, source="t")
    assert out is False
    assert etl._submit_enabled() is False


@pytest.mark.asyncio
async def test_submit_posts_when_enabled(monkeypatch):
    monkeypatch.setenv("NIFI_INGEST_URL", "http://nifi.test/listen")
    import importlib
    import services.etl_pipeline as etl
    importlib.reload(etl)

    captured: dict = {}

    async def fake_post(self, url, content=None, headers=None):
        captured["url"]     = url
        captured["body"]    = content
        captured["headers"] = headers or {}
        return type("R", (), {"status_code": 200})()

    with patch("httpx.AsyncClient.post", new=fake_post):
        ok = await etl.submit_to_pipeline(
            {"event": "knowledge_import"},
            source="unit-test",
            metadata={"domain": "kg-physics"},
        )
    assert ok is True
    assert captured["url"] == "http://nifi.test/listen"
    assert b'"event":"knowledge_import"' in captured["body"]
    assert captured["headers"].get("X-MoE-Source") == "unit-test"
    # metadata becomes prefixed NiFi attribute headers
    assert any(k.startswith("X-MoE-domain") for k in captured["headers"].keys())


def test_summarise_diagnostics_handles_none():
    import services.etl_pipeline as etl
    assert etl.summarise_diagnostics(None) == {"available": False}
    assert etl.summarise_diagnostics({}) == {"available": False} or \
        etl.summarise_diagnostics({}) == {
            "available": True, "uptime": None, "free_heap": None, "max_heap": None,
            "used_heap_pct": None, "available_processors": None, "thread_count": None,
            "version": None,
        }


def test_summarise_diagnostics_distils_snapshot():
    import services.etl_pipeline as etl
    raw = {
        "systemDiagnostics": {
            "aggregateSnapshot": {
                "uptime":              "00:42:13.000",
                "freeHeap":            "512 MB",
                "maxHeap":             "2 GB",
                "heapUtilization":     "42 %",
                "availableProcessors": 16,
                "totalThreads":        128,
                "versionInfo":         {"niFiVersion": "1.27.0"},
            }
        }
    }
    out = etl.summarise_diagnostics(raw)
    assert out["available"] is True
    assert out["uptime"] == "00:42:13.000"
    assert out["version"] == "1.27.0"
    assert out["thread_count"] == 128

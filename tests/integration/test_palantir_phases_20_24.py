"""Phase 20-24 — Palantir-equivalent feature surface tests.

Source-scan based: verifies that the routes, page templates, translations,
and helper functions for the Catalog / Approval / Data-Health / Explorer /
Notebook surfaces are all present and don't accidentally regress in future
refactors.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]


# ─── Phase 20: Data Catalog ──────────────────────────────────────────────────

@pytest.mark.parametrize("pattern", [
    r'@app\.get\("/api/catalog/datasets"',
    r'@app\.get\("/catalog"',
    r'@router\.get\("/graph/domains"',
])
def test_phase20_catalog_endpoints(pattern):
    sources = [
        (_ROOT / "admin_ui" / "app.py").read_text(),
        (_ROOT / "routes"   / "graph.py").read_text(),
    ]
    assert any(re.search(pattern, s) for s in sources), \
        f"Phase 20 missing pattern: {pattern!r}"


def test_phase20_catalog_template_exists():
    body = (_ROOT / "admin_ui" / "templates" / "catalog.html").read_text()
    for required in ("loadCatalog", "/api/catalog/datasets", "catalog-source-filter"):
        assert required in body


def test_phase20_translations_synced():
    keys = ["nav.catalog", "page.catalog", "lbl.search_filter", "lbl.all_sources",
            "lbl.source", "lbl.name", "lbl.type", "lbl.size"]
    for f in ("en_EN.lang", "de_DE.lang", "fr_FR.lang", "zh_CN.lang"):
        body = (_ROOT / "admin_ui" / "lang" / f).read_text()
        for k in keys:
            assert f'"{k}"' in body, f"{f}: missing {k}"


# ─── Phase 21: Approval Workflow ─────────────────────────────────────────────

@pytest.mark.parametrize("pattern", [
    r'@router\.post\("/graph/knowledge/import/pending"',
    r'@router\.get\("/graph/knowledge/approval/list"',
    r'@router\.post\("/graph/knowledge/approval/\{branch:path\}/approve"',
    r'@router\.post\("/graph/knowledge/approval/\{branch:path\}/reject"',
    r'@app\.get\("/approval"',
    r'@app\.post\("/api/approval/approve"',
    r'@app\.post\("/api/approval/reject"',
])
def test_phase21_approval_endpoints(pattern):
    sources = [
        (_ROOT / "admin_ui" / "app.py").read_text(),
        (_ROOT / "routes"   / "graph.py").read_text(),
    ]
    assert any(re.search(pattern, s) for s in sources), \
        f"Phase 21 missing pattern: {pattern!r}"


def test_phase21_versioning_branch_helpers():
    src = (_ROOT / "services" / "versioning.py").read_text()
    for sym in ("archive_to_branch", "list_pending_branches",
                "get_bundle_from_branch", "approve_branch", "reject_branch",
                "PENDING_BRANCH_PREFIX"):
        assert sym in src, f"versioning.py missing helper: {sym}"


def test_phase21_approval_template_and_translations():
    body = (_ROOT / "admin_ui" / "templates" / "approval.html").read_text()
    for r in ("approveBranch", "rejectBranch", "/api/approval/list"):
        assert r in body
    for f in ("en_EN.lang", "de_DE.lang", "fr_FR.lang", "zh_CN.lang"):
        body = (_ROOT / "admin_ui" / "lang" / f).read_text()
        for k in ("nav.approval", "page.approval", "btn.approve", "btn.reject"):
            assert f'"{k}"' in body


# ─── Phase 23: Data Health drift detection ───────────────────────────────────

def test_phase23_data_health_module():
    src = (_ROOT / "services" / "data_health.py").read_text()
    for sym in ("def compute_drift(", "async def record_event(",
                "async def recent_events(", "DRIFT_THRESHOLD"):
        assert sym in src


def test_phase23_drift_detection_hook_in_import():
    src = (_ROOT / "routes" / "graph.py").read_text()
    assert "from services.data_health import compute_drift, record_event" in src
    assert "_before_stats" in src
    assert "@router.get(\"/graph/health/events\"" in src


def test_phase23_drift_classification():
    """Pure-function test on compute_drift severity ladder."""
    import importlib
    import services.data_health as dh
    importlib.reload(dh)

    # Normal import: declared 10, actual delta 9 → ratio 0.9 (within threshold 0.3)
    drift = dh.compute_drift(
        {"entities": 100}, {"entities": 109},
        declared_entities=10,
    )
    assert drift["severity"] in ("ok", "info")
    assert drift["delta_entities"] == 9

    # Suppressed: declared 10, actual delta 1 → ratio 0.1 (below 1-0.3=0.7)
    drift = dh.compute_drift(
        {"entities": 100}, {"entities": 101},
        declared_entities=10,
    )
    assert drift["severity"] == "warn"
    assert "entity_dedup_suppressed" in drift["flags"]

    # Critical: declared 10, actual delta 0
    drift = dh.compute_drift(
        {"entities": 100}, {"entities": 100},
        declared_entities=10,
    )
    assert drift["severity"] == "crit"
    assert "zero_entities_added" in drift["flags"]

    # Critical: graph shrank
    drift = dh.compute_drift(
        {"entities": 100}, {"entities": 90},
        declared_entities=5,
    )
    assert drift["severity"] == "crit"
    assert "entity_count_shrank" in drift["flags"]


# ─── Phase 22: Object Explorer ───────────────────────────────────────────────

@pytest.mark.parametrize("pattern", [
    r'@router\.post\("/graph/cypher/read"',
    r'@app\.post\("/api/explorer/cypher"',
    r'@app\.get\("/explorer"',
])
def test_phase22_explorer_endpoints(pattern):
    sources = [
        (_ROOT / "admin_ui" / "app.py").read_text(),
        (_ROOT / "routes"   / "graph.py").read_text(),
    ]
    assert any(re.search(pattern, s) for s in sources), \
        f"Phase 22 missing pattern: {pattern!r}"


def test_phase22_cypher_blocks_writes():
    """The whitelist regex must reject any write keyword."""
    src = (_ROOT / "routes" / "graph.py").read_text()
    assert "_FORBIDDEN_CYPHER" in src
    # Confirm all dangerous keywords are present in the blacklist.
    for keyword in ("CREATE", "DELETE", "SET", "MERGE", "REMOVE", "DROP",
                    "ALTER", "GRANT", "REVOKE", "FOREACH"):
        assert keyword in src.split("_FORBIDDEN_CYPHER", 1)[1].split(")", 1)[0], \
            f"Missing forbidden keyword: {keyword}"


def test_phase22_explorer_template():
    body = (_ROOT / "admin_ui" / "templates" / "explorer.html").read_text()
    for r in ("runQuery", "/api/explorer/cypher", "cypher-query"):
        assert r in body


# ─── Phase 24: JupyterLite ───────────────────────────────────────────────────

def test_phase24_notebook_endpoint_and_env():
    src = (_ROOT / "admin_ui" / "app.py").read_text()
    assert re.search(r'@app\.get\("/notebook"', src)
    assert "JUPYTERLITE_URL" in src
    env_example = (_ROOT / ".env.example").read_text()
    assert "JUPYTERLITE_URL=" in env_example


def test_phase24_notebook_template():
    body = (_ROOT / "admin_ui" / "templates" / "notebook.html").read_text()
    for r in ("jupyterlite_url", "snippet-panel", "iframe"):
        assert r in body


# ─── Cross-cutting: nav links exist for all 5 phases ─────────────────────────

@pytest.mark.parametrize("href", [
    "/catalog", "/approval", "/explorer", "/notebook",
])
def test_sidebar_has_phase_links(href):
    base = (_ROOT / "admin_ui" / "templates" / "base.html").read_text()
    assert f'href="{href}"' in base, f"Sidebar missing link to {href}"


# ─── Translation parity across phases ────────────────────────────────────────

def test_translation_parity():
    """All four lang files must define the same set of nav.* / page.* keys."""
    keys_per_file = {}
    for f in ("en_EN.lang", "de_DE.lang", "fr_FR.lang", "zh_CN.lang"):
        body = (_ROOT / "admin_ui" / "lang" / f).read_text()
        keys_per_file[f] = set(re.findall(r'"(nav\.\w+|page\.\w+)"', body))
    canonical = keys_per_file["en_EN.lang"]
    for f, ks in keys_per_file.items():
        assert ks == canonical, \
            f"{f} keys diverge: missing {canonical - ks}, extra {ks - canonical}"

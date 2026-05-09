"""Smoke tests for the Phase 19 enterprise dashboard endpoints.

These tests exercise the admin_ui module level helpers without booting the
FastAPI app — admin_ui has heavy import-time side effects (DB connection,
SMTP config, Authentik OIDC sync) that we don't want to spin up in unit tests.
"""

import os
import sys
import re
from pathlib import Path

import pytest


_ADMIN_UI_DIR = Path(__file__).parents[2] / "admin_ui"


# ─── Source scan: assert the new endpoints + helper survive future refactors ──

@pytest.mark.parametrize(
    "pattern",
    [
        r'@app\.get\("/api/enterprise/health"',
        r'@app\.get\("/api/enterprise/runs"',
        r'@app\.get\("/enterprise"',
        r"def _classify_service_status\(",
        r"_ENTERPRISE_SERVICES",
        r'"NIFI_URL"',
        r'"MARQUEZ_URL"',
        r'"LAKEFS_ENDPOINT"',
    ],
)
def test_admin_ui_phase19_route_contract(pattern):
    src = (_ADMIN_UI_DIR / "app.py").read_text()
    assert re.search(pattern, src), f"Phase 19 contract regression: missing {pattern!r}"


def test_admin_ui_phase19_template_exists():
    tpl = _ADMIN_UI_DIR / "templates" / "enterprise.html"
    assert tpl.exists(), "enterprise.html template missing"
    body = tpl.read_text()
    for required in (
        "loadEnterprise",
        "/api/enterprise/health",
        "/api/enterprise/runs",
        "ent-status-",
    ):
        assert required in body, f"enterprise.html missing {required!r}"


def test_admin_ui_phase19_translation_keys_synced():
    """All four language files must define the new Phase 19 keys."""
    required_keys = [
        "nav.enterprise",
        "page.enterprise",
        "section.lineage_runs",
        "status.healthy",
        "status.degraded",
        "status.down",
        "status.disabled",
        "lbl.enterprise_not_configured",
        "lbl.marquez_not_configured",
        "btn.open",
    ]
    for lang_file in ("en_EN.lang", "de_DE.lang", "fr_FR.lang", "zh_CN.lang"):
        path = _ADMIN_UI_DIR / "lang" / lang_file
        body = path.read_text()
        for key in required_keys:
            assert f'"{key}"' in body, f"{lang_file} missing key {key!r}"


def test_admin_ui_phase19_nav_link():
    base = (_ADMIN_UI_DIR / "templates" / "base.html").read_text()
    assert 'href="/enterprise"' in base, "Sidebar link to /enterprise missing in base.html"


def test_admin_ui_phase19_dashboard_widget():
    dashboard = (_ADMIN_UI_DIR / "templates" / "dashboard.html").read_text()
    assert "dashboard-enterprise-card" in dashboard, "Dashboard quick-status widget missing"
    assert "/api/enterprise/health" in dashboard, "Dashboard widget missing health fetch"


# ─── Pure-function tests for _classify_service_status ─────────────────────────
# We import the helper without booting the FastAPI app by stubbing the heavy
# top-level dependencies, then re-importing only the helper logic.

def _load_classifier():
    """Extract _classify_service_status from admin_ui/app.py via AST.

    Avoids importing admin_ui (which has heavy startup side-effects) while
    still asserting on the deployed source.
    """
    import ast as _ast
    import textwrap

    src  = (_ADMIN_UI_DIR / "app.py").read_text()
    tree = _ast.parse(src)
    fn_node = next(
        (n for n in tree.body
         if isinstance(n, _ast.FunctionDef) and n.name == "_classify_service_status"),
        None,
    )
    assert fn_node is not None, "Could not locate _classify_service_status in admin_ui/app.py"
    fn_src = textwrap.dedent(_ast.get_source_segment(src, fn_node))
    namespace: dict = {"_ENTERPRISE_DEGRADED_LATENCY_MS": 800}
    exec(fn_src, namespace)
    return namespace["_classify_service_status"]


def test_classify_service_status_disabled():
    fn = _load_classifier()
    assert fn(False, -1, False) == "disabled"
    assert fn(True, 50, False) == "disabled"  # configured wins


def test_classify_service_status_down():
    fn = _load_classifier()
    assert fn(False, -1, True) == "down"
    assert fn(False, 150, True) == "down"
    assert fn(True, -1, True) == "down"  # probe error counts as down


def test_classify_service_status_healthy():
    fn = _load_classifier()
    assert fn(True, 25, True) == "healthy"
    assert fn(True, 800, True) == "healthy"  # boundary inclusive


def test_classify_service_status_degraded():
    fn = _load_classifier()
    assert fn(True, 801, True) == "degraded"
    assert fn(True, 5000, True) == "degraded"

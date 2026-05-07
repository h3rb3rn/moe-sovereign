"""
Route contract tests — assert that all critical @app.* handlers survive refactoring.

Strategy: parse the source of main.py for @app.METHOD("path") decorator patterns.
This works even when FastAPI is stubbed as MagicMock in the test environment, and
correctly catches the case where a decorator is removed or a route path is changed.

When routes are moved to APIRouter files during refactoring, those files must be
added to SCANNED_FILES so the contract remains valid.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]

# Add more files here as routes are extracted from main.py into APIRouter modules.
SCANNED_FILES = [
    _ROOT / "main.py",
    _ROOT / "routes" / "health.py",
    _ROOT / "routes" / "watchdog.py",
    _ROOT / "routes" / "mission_context.py",
    _ROOT / "routes" / "graph.py",
    _ROOT / "routes" / "admin_benchmark.py",
    _ROOT / "routes" / "admin_ontology.py",
]

# Each entry: (HTTP_METHOD_LOWERCASE, "/path")
# A route is required when: losing it would silently break a client.
REQUIRED_ROUTES = [
    ("get",    "/health"),
    ("get",    "/metrics"),
    ("get",    "/v1/models"),
    ("post",   "/v1/chat/completions"),
    ("post",   "/v1/messages"),
    ("post",   "/v1/messages/count_tokens"),
    ("post",   "/v1/feedback"),
    ("post",   "/v1/memory/ingest"),
    ("get",    "/graph/stats"),
    ("get",    "/graph/search"),
    ("get",    "/graph/knowledge/export"),
    ("post",   "/graph/knowledge/import"),
    ("post",   "/graph/knowledge/import/validate"),
    ("get",    "/api/watchdog/alerts"),
    ("delete", "/api/watchdog/alerts"),
    ("get",    "/api/watchdog/node-status"),
    ("get",    "/api/watchdog/config"),
    ("post",   "/api/watchdog/config"),
    ("get",    "/api/starfleet/features"),
    ("get",    "/api/mission-context"),
    ("post",   "/api/mission-context"),
    ("patch",  "/api/mission-context"),
    ("post",   "/v1/admin/ontology/trigger"),
    ("delete", "/v1/admin/ontology/status"),
    ("post",   "/v1/admin/ontology/dedicated/start"),
    ("post",   "/v1/admin/ontology/dedicated/stop"),
    ("get",    "/v1/admin/ontology/dedicated/status"),
    ("get",    "/v1/admin/ontology/dedicated/verify"),
    ("post",   "/v1/admin/benchmark/lock"),
    ("get",    "/v1/provider-status"),
]


def _registered_routes() -> set[tuple[str, str]]:
    """Scan source files for @app.METHOD("path") patterns."""
    found = set()
    for src_file in SCANNED_FILES:
        if not src_file.exists():
            continue
        text = src_file.read_text(encoding="utf-8")
        # Match @app.get("/path") / @router.post("/path", ...) etc.
        for m in re.finditer(
            r'@(?:app|router)\.(get|post|put|patch|delete|head|options)'
            r'\s*\(\s*["\']([^"\']+)["\']',
            text,
        ):
            found.add((m.group(1).lower(), m.group(2)))
    return found


def test_all_required_routes_in_source():
    """No required route may disappear after a refactoring step."""
    actual = _registered_routes()
    missing = [
        f"{m.upper()} {p}"
        for m, p in REQUIRED_ROUTES
        if (m, p) not in actual
    ]
    assert not missing, (
        "Routes fehlen in main.py (oder einem APIRouter-Modul in SCANNED_FILES).\n"
        "Nach dem Refactoring SCANNED_FILES in test_routes_contract.py ergänzen:\n"
        + "\n".join(missing)
    )


def test_chat_completions_is_post():
    actual = _registered_routes()
    assert ("post", "/v1/chat/completions") in actual
    assert ("get",  "/v1/chat/completions") not in actual


def test_health_is_get():
    actual = _registered_routes()
    assert ("get", "/health") in actual


def test_critical_routes_not_duplicated():
    """
    The most critical routes must not be registered multiple times, which would
    cause the second registration to silently shadow the first in FastAPI.
    (Ollama-compat proxy routes may legitimately appear twice — excluded here.)
    """
    OLLAMA_COMPAT_PREFIX = "/api/"  # intentional duplicates for Ollama compat

    all_pairs: list[tuple[str, str]] = []
    for src_file in SCANNED_FILES:
        if not src_file.exists():
            continue
        text = src_file.read_text(encoding="utf-8")
        for m in re.finditer(
            r'@(?:app|router)\.(get|post|put|patch|delete|head|options)'
            r'\s*\(\s*["\']([^"\']+)["\']',
            text,
        ):
            path = m.group(2)
            if not path.startswith(OLLAMA_COMPAT_PREFIX):
                all_pairs.append((m.group(1).lower(), path))

    seen, dupes = set(), []
    for pair in all_pairs:
        if pair in seen:
            dupes.append(f"{pair[0].upper()} {pair[1]}")
        seen.add(pair)
    assert not dupes, f"Doppelt registrierte kritische Routen: {dupes}"

"""
test_mcp_access_kind.py — Completeness guard for the MCP tool access-kind
classification used by services/decision_log.py's MCP_TOOL_ACCESS telemetry
(see graph/tool_nodes.py). Prevents silent drift when new tools are added to
mcp_server/server.py without a matching _TOOL_ACCESS_KIND entry.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# mcp_server/server.py does `import pm_connector` (bare, not package-relative) —
# only resolvable when mcp_server/ itself is on sys.path, exactly like when the
# module runs as the actual container entrypoint. Add it here too so this test
# is collectible standalone.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

# conftest.py has already stubbed mcp/fastapi/uvicorn so the import is safe.
from mcp_server.server import _TOOL_DESCRIPTIONS, _TOOL_ACCESS_KIND, _TOOL_REGISTRY
from services.dynamic_router import _CATEGORY_MCP_TOOLS

_VALID_ACCESS_KINDS = {"read", "search", "write", "execute"}


def test_every_described_tool_has_access_kind():
    missing = [name for name in _TOOL_DESCRIPTIONS if name not in _TOOL_ACCESS_KIND]
    assert not missing, f"Tools missing an access_kind classification: {missing}"


def test_all_access_kinds_valid():
    invalid = {name: kind for name, kind in _TOOL_ACCESS_KIND.items() if kind not in _VALID_ACCESS_KINDS}
    assert not invalid, f"Tools with an invalid access_kind: {invalid}"


def test_every_described_tool_is_registered():
    missing = sorted(set(_TOOL_DESCRIPTIONS) - set(_TOOL_REGISTRY))
    assert not missing, f"Described tools missing from REST registry: {missing}"


def test_every_registered_tool_has_access_kind():
    missing = sorted(set(_TOOL_REGISTRY) - set(_TOOL_ACCESS_KIND))
    assert not missing, f"Registered tools missing access_kind: {missing}"


def test_dynamic_router_defaults_reference_registered_tools_only():
    unknown = {
        category: sorted(set(tool_names) - set(_TOOL_REGISTRY))
        for category, tool_names in _CATEGORY_MCP_TOOLS.items()
        if set(tool_names) - set(_TOOL_REGISTRY)
    }
    assert not unknown, f"Dynamic router references unknown MCP tools: {unknown}"

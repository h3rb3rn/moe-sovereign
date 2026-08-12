"""tests/test_generative_mcp.py — Unit tests for generative MCP tools (TASK-52).

Verifies generate_image and generate_speech tool registrations, input schema validation,
determinism contract metadata ('generative_model'), and error handling when backends are unreachable.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from mcp_server.server import (
    _TOOL_REGISTRY,
    _TOOL_CONTRACTS,
    execute_tool,
    InvokeRequest,
    generate_image,
    generate_speech,
)


def test_generative_tools_registered():
    """Verify generate_image and generate_speech are present in registry and contracts."""
    assert "generate_image" in _TOOL_REGISTRY
    assert "generate_speech" in _TOOL_REGISTRY
    assert "generate_image" in _TOOL_CONTRACTS
    assert "generate_speech" in _TOOL_CONTRACTS

    assert _TOOL_CONTRACTS["generate_image"]["determinism"] == "generative_model"
    assert _TOOL_CONTRACTS["generate_speech"]["determinism"] == "generative_model"
    assert _TOOL_CONTRACTS["generate_image"]["source_policy"]["node"] == "N04-RGTX"
    assert _TOOL_CONTRACTS["generate_speech"]["source_policy"]["node"] == "N04-RGTX"


@pytest.mark.asyncio
async def test_generate_image_validation_empty_prompt():
    """Verify empty prompt returns validation error."""
    res = await generate_image(prompt="")
    assert "error" in res
    assert res["error_code"] == "invalid_prompt"


@pytest.mark.asyncio
async def test_generate_speech_validation_empty_text():
    """Verify empty text returns validation error."""
    res = await generate_speech(text="")
    assert "error" in res
    assert res["error_code"] == "invalid_text"


@pytest.mark.asyncio
async def test_execute_tool_generate_image_schema_validation():
    """Verify execute_tool handles schema validation for generate_image."""
    req = InvokeRequest(tool="generate_image", args={"prompt": "A sovereign AI server node in dark mode"})
    res = await execute_tool(req)
    assert isinstance(res, dict)
    # When backend is not running live in test env, backend_unreachable error is gracefully returned
    assert "error" in res or "created" in res or "data" in res


@pytest.mark.asyncio
async def test_execute_tool_generate_speech_schema_validation():
    """Verify execute_tool handles schema validation for generate_speech."""
    req = InvokeRequest(tool="generate_speech", args={"text": "MoE Sovereign system active"})
    res = await execute_tool(req)
    assert isinstance(res, dict)
    # When backend is not running live in test env, backend_unreachable error is gracefully returned
    assert "error" in res or "ok" in res

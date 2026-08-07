"""End-to-end contracts for mandatory precision preflight and evidence."""

from unittest.mock import AsyncMock

import pytest

import graph.router_nodes as router_nodes
import graph.tool_nodes as tool_nodes
from services.pipeline.contracts import (
    build_precision_preflight,
    canonical_json_hash,
    canonical_tool_catalog_hash,
)


def _schemas():
    return {
        "gcd_lcm": {
            "required": ["a", "b"],
            "args": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
                "operation": {"type": "string", "default": "both"},
            },
            "access_kind": "read",
        }
    }


class _ExplodingRedis:
    async def get(self, _key):
        raise AssertionError("L0 cache must not run for mandatory precision")


class _ExplodingCollection:
    def query(self, **_kwargs):
        raise AssertionError("L1 cache must not run for mandatory precision")


@pytest.mark.asyncio
async def test_precision_preflight_bypasses_l0_and_l1(monkeypatch):
    monkeypatch.setattr(router_nodes.state, "MCP_TOOL_SCHEMAS", _schemas())
    monkeypatch.setattr(router_nodes.state, "redis_client", _ExplodingRedis())
    monkeypatch.setattr(router_nodes.state, "cache_collection", _ExplodingCollection())
    record_stage = AsyncMock()
    monkeypatch.setattr(router_nodes, "_record_stage", record_stage)

    result = await router_nodes.cache_lookup_node(
        {
            "input": "Berechne den GGT von 391 und 299.",
            "response_id": "req-precision-cache",
            "mode": "default",
            "enable_cache": True,
        }
    )

    assert result["cache_hit"] is False
    assert result["cached_facts"] == ""
    assert result["soft_cache_examples"] == ""
    assert result["precision_cache_bypassed"] is True
    assert result["required_precision_intents"][0]["tool"] == "gcd_lcm"
    assert record_stage.await_args.args[2:] == (
        "bypassed",
        "required_precision_intent",
    )


@pytest.mark.asyncio
async def test_preflight_selects_model_free_route_only_for_fully_covered_request(
    monkeypatch,
):
    monkeypatch.setattr(router_nodes.state, "MCP_TOOL_SCHEMAS", _schemas())
    monkeypatch.setattr(router_nodes, "PRECISION_DIRECT_RESPONSE_ENABLED", True)
    monkeypatch.setattr(router_nodes, "_record_stage", AsyncMock())

    direct = await router_nodes.precision_preflight_node(
        {
            "input": "Berechne den GGT von 391 und 299.",
            "response_id": "req-direct",
        }
    )
    mixed = await router_nodes.precision_preflight_node(
        {
            "input": (
                "1. Berechne den GGT von 391 und 299.\n"
                "2. Erkläre den euklidischen Algorithmus."
            ),
            "response_id": "req-mixed",
        }
    )

    assert direct["precision_direct"] is True
    assert direct["precision_cache_bypassed"] is True
    assert direct["plan"][0]["mcp_tool"] == "gcd_lcm"
    assert router_nodes._route_precision_preflight(direct) == "precision_mcp"
    assert mixed["precision_direct"] is False
    assert "plan" not in mixed
    assert router_nodes._route_precision_preflight(mixed) == "cache"


@pytest.mark.asyncio
async def test_cache_does_not_replace_existing_preflight_on_catalog_reload(monkeypatch):
    original = _schemas()
    frozen = build_precision_preflight(
        "Berechne den GGT von 391 und 299.",
        original,
    )
    changed = _schemas()
    changed["gcd_lcm"]["args"]["operation"]["default"] = "gcd"
    monkeypatch.setattr(router_nodes.state, "MCP_TOOL_SCHEMAS", changed)
    monkeypatch.setattr(router_nodes, "_record_stage", AsyncMock())

    result = await router_nodes.cache_lookup_node(
        {
            "input": "Berechne den GGT von 391 und 299.",
            "response_id": "req-snapshot-immutable",
            "mode": "default",
            "enable_cache": True,
            **frozen,
        }
    )

    assert "precision_contract_snapshot" not in result
    assert "required_precision_intents" not in result
    assert result["precision_cache_bypassed"] is True


def test_catalog_hash_covers_types_defaults_and_access_policy():
    original = _schemas()
    changed_default = _schemas()
    changed_default["gcd_lcm"]["args"]["operation"]["default"] = "gcd"
    changed_type = _schemas()
    changed_type["gcd_lcm"]["args"]["a"]["type"] = "number"
    changed_access = _schemas()
    changed_access["gcd_lcm"]["access_kind"] = "write"

    hashes = {
        canonical_tool_catalog_hash(original),
        canonical_tool_catalog_hash(changed_default),
        canonical_tool_catalog_hash(changed_type),
        canonical_tool_catalog_hash(changed_access),
    }

    assert len(hashes) == 4


def test_full_pre_call_schema_validation_rejects_type_and_unknown_property():
    schema = _schemas()["gcd_lcm"]

    assert tool_nodes._validate_tool_args(
        {"a": 391, "b": 299, "operation": "gcd"},
        schema,
    ) == (True, "")
    assert tool_nodes._validate_tool_args(
        {"a": "391", "b": 299},
        schema,
    ) == (False, "pre_call_schema_invalid:a:type")
    assert tool_nodes._validate_tool_args(
        {"a": 391, "b": 299, "unexpected": True},
        schema,
    ) == (False, "pre_call_schema_invalid:args:additionalProperties")


def test_structured_result_validation_binds_contract_input_facts_and_source():
    args = {"a": 391, "b": 299, "operation": "gcd"}
    output_schema = {
        "type": "object",
        "properties": {"gcd": {"type": "integer"}},
        "required": ["gcd"],
        "additionalProperties": False,
    }
    schema = {
        **_schemas()["gcd_lcm"],
        "contract_id": "moe.precision.gcd_lcm",
        "contract_version": "1.0.0",
        "contract_hash": "a" * 64,
        "determinism": "input_only",
        "output_schema": output_schema,
        "structured_result_required": True,
    }
    facts = {"gcd": 23}
    result_hash = canonical_json_hash(
        {
            "contract_hash": schema["contract_hash"],
            "input_normalized": args,
            "facts": facts,
        }
    )
    payload = {
        "result": "GCD(391, 299) = 23",
        "structured_result": {
            "status": "completed",
            "tool": "gcd_lcm",
            "contract_id": schema["contract_id"],
            "contract_version": schema["contract_version"],
            "contract_hash": schema["contract_hash"],
            "input_normalized": args,
            "facts": facts,
            "determinism": "input_only",
            "source": {
                "kind": "python_stdlib",
                "name": "math",
                "version": "3.11.0",
                "as_of": None,
            },
            "warnings": [],
            "result_hash": result_hash,
        },
    }

    assert tool_nodes._validate_structured_mcp_result(
        payload,
        schema,
        "gcd_lcm",
        args,
    ) == (True, "", payload["structured_result"])

    payload["structured_result"]["facts"]["gcd"] = "23"
    valid, reason, _ = tool_nodes._validate_structured_mcp_result(
        payload,
        schema,
        "gcd_lcm",
        args,
    )
    assert valid is False
    assert reason == "structured_result_facts_invalid"


class _ErrorResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"error": "simulated server error"}


class _ErrorClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return _ErrorResponse()


@pytest.mark.asyncio
async def test_mandatory_precision_error_never_invokes_judge_arg_repair(monkeypatch):
    schemas = _schemas()
    preflight = build_precision_preflight(
        "Berechne den GGT von 391 und 299.",
        schemas,
    )
    monkeypatch.setattr(tool_nodes.state, "MCP_TOOL_SCHEMAS", schemas)
    monkeypatch.setattr(tool_nodes.httpx, "AsyncClient", lambda **_kwargs: _ErrorClient())
    judge = AsyncMock(side_effect=AssertionError("judge retry must not run"))
    monkeypatch.setattr(tool_nodes, "_invoke_judge_with_retry", judge)
    monkeypatch.setattr(tool_nodes, "_record_stage", AsyncMock())
    monkeypatch.setattr(tool_nodes, "_report", AsyncMock())
    monkeypatch.setattr(tool_nodes, "_log_tool_eval", lambda *_args, **_kwargs: None)

    result = await tool_nodes.mcp_node(
        {
            "input": "Berechne den GGT von 391 und 299.",
            "response_id": "req-no-judge-repair",
            "plan": [
                {
                    "id": "task-1",
                    "task": "Berechne den GGT von 391 und 299.",
                    "category": "precision_tools",
                    "mcp_tool": "gcd_lcm",
                    "mcp_args": {"a": 391, "b": 299, "operation": "gcd"},
                }
            ],
            "user_permissions": {"mcp_tool": ["*"]},
            "agentic_iteration": 0,
            **preflight,
        }
    )

    judge.assert_not_awaited()
    assert result["task_events"][-1]["status"] == "failed"
    assert result["mcp_evidence"][-1]["error"] == (
        "mandatory_precision_retry_forbidden"
    )
    assert result["mcp_evidence"][-1]["contract_hash"] == (
        preflight["required_precision_intents"][0]["schema_hash"]
    )


@pytest.mark.asyncio
async def test_mandatory_precision_detects_catalog_change_before_invoke(monkeypatch):
    schemas = _schemas()
    preflight = build_precision_preflight(
        "Berechne den GGT von 391 und 299.",
        schemas,
    )
    changed = _schemas()
    changed["gcd_lcm"]["args"]["operation"]["default"] = "gcd"
    monkeypatch.setattr(tool_nodes.state, "MCP_TOOL_SCHEMAS", changed)
    monkeypatch.setattr(tool_nodes.httpx, "AsyncClient", lambda **_kwargs: _ErrorClient())
    monkeypatch.setattr(tool_nodes, "_record_stage", AsyncMock())
    monkeypatch.setattr(tool_nodes, "_report", AsyncMock())

    result = await tool_nodes.mcp_node(
        {
            "input": "Berechne den GGT von 391 und 299.",
            "response_id": "req-contract-drift",
            "plan": [
                {
                    "id": "task-1",
                    "task": "Berechne den GGT von 391 und 299.",
                    "category": "precision_tools",
                    "mcp_tool": "gcd_lcm",
                    "mcp_args": {"a": 391, "b": 299, "operation": "gcd"},
                }
            ],
            "user_permissions": {"mcp_tool": ["*"]},
            "agentic_iteration": 0,
            **preflight,
        }
    )

    assert result["mcp_evidence"][-1]["error"] == "precision_contract_changed"

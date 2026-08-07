"""TASK-49 safe network-free structured validation contract."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from mcp_server.server import (
    InvokeRequest,
    build_tools_catalog,
    execute_tool,
    structured_validate,
)
from services.pipeline.contracts import (
    build_direct_precision_plan,
    detect_required_precision_intents,
    is_fully_covered_precision_request,
)
from services.precision_response import render_precision_evidence


def _facts(raw: str) -> dict:
    return json.loads(raw)


def test_json_parse_and_schema_validation_are_reproducible():
    schema = json.dumps({
        "type": "object",
        "properties": {"age": {"type": "integer", "minimum": 0}},
        "required": ["age"],
        "additionalProperties": False,
    })
    valid = _facts(structured_validate("json", '{"age": 42}', schema, None))
    invalid = _facts(structured_validate("json", '{"age": -1}', schema, None))
    malformed = _facts(structured_validate("json", '{"age":}', None, None))
    assert valid["valid"] is True
    assert len(valid["payload_hash"]) == len(valid["schema_hash"]) == 64
    assert invalid["valid"] is False
    assert invalid["errors"][0]["code"] == "json_schema_error"
    assert malformed["errors"][0]["line"] == 1


def test_json_schema_refs_are_blocked_without_resolution():
    facts = _facts(structured_validate(
        "json", '{"age": 42}', '{"$ref":"https://attacker.invalid/schema.json"}', None
    ))
    assert facts["valid"] is False
    assert facts["errors"][0]["code"] == "schema_ref_not_allowed"


@pytest.mark.parametrize(
    "payload",
    [
        "a: &anchor [1, 2]\nb: *anchor",
        "!!python/object/apply:os.system ['id']",
    ],
)
def test_yaml_aliases_anchors_and_tags_are_rejected(payload):
    facts = _facts(structured_validate("yaml", payload, None, None))
    assert facts["valid"] is False
    assert facts["errors"][0]["code"] == "yaml_alias_anchor_or_tag_rejected"


def test_safe_yaml_and_xml_are_parsed_with_bounded_metadata_only():
    yaml_facts = _facts(structured_validate("yaml", "root:\n  enabled: true", None, None))
    xml_facts = _facts(structured_validate("xml", "<root><item>ok</item></root>", None, None))
    assert yaml_facts["valid"] is True
    assert xml_facts["valid"] is True
    assert yaml_facts["details"]["depth"] >= 2
    assert xml_facts["details"] == {"depth": 2, "nodes": 2}
    assert "enabled" not in json.dumps(yaml_facts)
    assert "item" not in json.dumps(xml_facts)


@pytest.mark.parametrize(
    "payload",
    [
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
        '<!DOCTYPE lolz [<!ENTITY lol "lol">]><lolz>&lol;</lolz>',
        '<root xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="file:///etc/passwd"/></root>',
    ],
)
def test_xml_dtd_entities_and_xinclude_are_rejected(payload):
    facts = _facts(structured_validate("xml", payload, None, None))
    assert facts["valid"] is False
    assert facts["errors"][0]["code"] == "xml_forbidden_construct"


def test_csv_requires_dialect_reports_shape_and_flags_formulas_as_data_risk():
    facts = _facts(structured_validate(
        "csv", "name,value\nalice,=2+2\nbob,3", None, "comma"
    ))
    mismatch = _facts(structured_validate(
        "csv", "name,value\nalice", None, "comma"
    ))
    assert facts["valid"] is True
    assert facts["details"] == {"rows": 3, "columns": 2}
    assert facts["warnings"] == [{"code": "csv_formula_prefix", "count": 1}]
    assert mismatch["valid"] is False
    assert mismatch["errors"][0]["code"] == "csv_column_count_mismatch"
    with pytest.raises(ValueError, match="csv_dialect_must_be_explicit"):
        structured_validate("csv", "a,b\n1,2", None, None)


def test_structured_depth_and_payload_limits_fail_closed():
    nested = "[" * 65 + "0" + "]" * 65
    with pytest.raises(ValueError, match="depth_limit"):
        structured_validate("json", nested, None, None)
    with pytest.raises(ValueError, match="payload_size_limit"):
        structured_validate("json", "x" * 65537, None, None)


@pytest.mark.asyncio
async def test_structured_invoke_returns_hash_bound_facts_not_raw_payload():
    secret_payload = '{"password":"do-not-log-this"}'
    response = await execute_tool(InvokeRequest(
        tool="structured_validate",
        args={"format_name": "json", "payload": secret_payload},
    ))
    structured = response["structured_result"]
    serialized = json.dumps(structured)
    assert structured["facts"]["valid"] is True
    assert "do-not-log-this" not in serialized
    assert structured["source"]["name"] == "jsonschema-pyyaml-defusedxml-csv"


@pytest.mark.parametrize(
    "query,format_name",
    [
        ('Validiere dieses JSON: {"a": 1}', "json"),
        ("Validate this YAML: root:\n  enabled: true", "yaml"),
        ("Validiere dieses XML: <root><a>1</a></root>", "xml"),
        ("Validate this CSV with delimiter comma: a,b\n1,2", "csv"),
    ],
)
def test_structured_intents_preserve_payload_and_are_direct(query, format_name):
    intents = detect_required_precision_intents(query)
    assert len(intents) == 1
    assert intents[0].tool == "structured_validate"
    assert intents[0].args["format_name"] == format_name
    assert is_fully_covered_precision_request(query)
    assert build_direct_precision_plan(query)[0]["mcp_tool"] == "structured_validate"


@pytest.mark.parametrize(
    "query",
    [
        "Ist diese Konfiguration korrekt? a: 1",
        "Repariere dieses ungültige JSON: {a:1}",
        "Validiere CSV: a,b\n1,2",
        "Validate this document: <root/>",
    ],
)
def test_structured_intent_does_not_guess_format_dialect_or_repair(query):
    assert detect_required_precision_intents(query) == []


def test_structured_renderer_states_structure_not_semantic_truth():
    facts = _facts(structured_validate("json", '{"claim": false}', None, None))
    rendered = render_precision_evidence(
        {"status": "completed", "tool": "structured_validate", "facts": facts},
        "Validiere dieses JSON.",
    )
    assert "JSON-Struktur ist gültig" in rendered
    assert facts["payload_hash"] in rendered
    assert "inhaltlich" not in rendered.casefold()


def test_structured_tool_catalog_exposes_security_limits():
    tool = {item["name"]: item for item in build_tools_catalog()["tools"]}["structured_validate"]
    assert tool["structured_result"] is True
    assert tool["limits"]["max_payload_bytes"] == 65536
    assert tool["limits"]["max_depth"] == 64
    assert tool["inputSchema"]["additionalProperties"] is False

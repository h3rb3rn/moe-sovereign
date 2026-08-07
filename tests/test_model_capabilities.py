"""tests/test_model_capabilities.py — Unit tests for TASK-31 model capability matrix."""

import pytest
from services.model_capabilities import (
    get_model_caps,
    model_supports_json_schema,
    model_supports_json_object,
    model_supports_streaming,
    load_capabilities,
    _DEFAULT_CAPS,
    apply_ollama_structured_capability,
    enforce_streaming_capability,
    openai_response_format,
)


def test_default_fallback_for_unknown_model():
    caps = get_model_caps("some-totally-unknown-model:latest")
    assert caps["json_schema"] == _DEFAULT_CAPS["json_schema"]
    assert caps["stream"] == _DEFAULT_CAPS["stream"]


def test_known_model_overrides_default():
    caps = get_model_caps("qwen3.6:35b")
    assert caps["json_schema"] is True
    assert caps["stream"] is True


def test_model_supports_json_schema_true():
    assert model_supports_json_schema("qwen3.6:35b") is True


def test_model_supports_json_schema_false():
    assert model_supports_json_schema("llama3.3-70b-ctx4k:latest") is False


def test_model_supports_streaming():
    assert model_supports_streaming("llama3.3:70b") is True


def test_stream_false_for_configured_model():
    assert model_supports_streaming("mistral:7b") is False


def test_no_key_error_on_caps_call():
    for model in ["", "unknown", "gpt-4o", "llama3.3-70b-ctx4k:latest", "qwen3:32b"]:
        caps = get_model_caps(model)
        assert "stream" in caps
        assert "json_schema" in caps


def test_load_capabilities_returns_dict():
    caps = load_capabilities()
    assert isinstance(caps, dict)
    assert "default" in caps or caps == {}


def test_ollama_schema_is_applied_only_when_supported():
    schema = {"type": "array", "items": {"type": "object"}}
    supported = apply_ollama_structured_capability(
        {"stream": True, "format": {"stale": True}},
        "qwen3.6:35b",
        schema,
    )
    unsupported = apply_ollama_structured_capability(
        {"stream": True, "format": {"stale": True}},
        "llama3.3-70b-ctx4k:latest",
        schema,
    )
    assert supported["format"] == schema
    assert "format" not in unsupported


def test_streaming_request_is_forced_off_for_non_stream_model():
    assert enforce_streaming_capability("mistral:7b", True) is False
    assert enforce_streaming_capability("qwen3.6:35b", True) is True


def test_openai_response_format_uses_json_schema_capability():
    response_format = openai_response_format(
        "qwen3.6:35b",
        {"type": "object", "properties": {}},
    )
    assert response_format["type"] == "json_schema"

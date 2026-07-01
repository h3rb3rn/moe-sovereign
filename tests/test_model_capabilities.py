"""tests/test_model_capabilities.py — Unit tests for TASK-31 model capability matrix."""

import pytest
from services.model_capabilities import (
    get_model_caps,
    model_supports_json_schema,
    model_supports_json_object,
    model_supports_streaming,
    model_hint_tokens,
    load_capabilities,
    _DEFAULT_CAPS,
)


def test_default_fallback_for_unknown_model():
    caps = get_model_caps("some-totally-unknown-model:latest")
    assert caps["json_schema"] == _DEFAULT_CAPS["json_schema"]
    assert caps["stream"] == _DEFAULT_CAPS["stream"]
    assert isinstance(caps["hints"], list)


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


def test_model_hint_tokens_non_empty():
    hints = model_hint_tokens("qwen3.6:35b")
    assert "schema+" in hints


def test_model_hint_tokens_empty_for_unknown():
    hints = model_hint_tokens("some-unknown-model:v99")
    assert hints == []


def test_no_key_error_on_caps_call():
    for model in ["", "unknown", "gpt-4o", "llama3.3-70b-ctx4k:latest", "qwen3:32b"]:
        caps = get_model_caps(model)
        assert "stream" in caps
        assert "json_schema" in caps


def test_load_capabilities_returns_dict():
    caps = load_capabilities()
    assert isinstance(caps, dict)
    assert "default" in caps or caps == {}

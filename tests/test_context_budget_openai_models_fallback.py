"""tests/test_context_budget_openai_models_fallback.py — Unit tests for
context_budget.py::fetch_openai_context_window / fetch_openai_max_output
falling back to the plain GET /v1/models LIST endpoint.

Regression for a live bug observed on Hetzner's Inference API: it implements
only GET /v1/models (collection) and 404s on GET /v1/models/{id} (the path
our code tried first) and on LiteLLM's /model/info (not LiteLLM-backed) —
so context-window detection silently collapsed to the conservative 32768
parameter-count heuristic even though the model's real limit is 262144
(vLLM's own "max_model_len" field, seen live in Hetzner's /v1/models
response). That, in turn, capped max_tokens down to ~1024 on any
non-trivial conversation, truncating tool-call JSON mid-stream.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_budget import fetch_openai_context_window, fetch_openai_max_output


def _resp(status_code: int, json_body: dict):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body
    return r


class TestFetchOpenaiContextWindowListFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_models_list_max_model_len(self):
        # /v1/models/{id} 404s (Hetzner-style: only the list endpoint exists).
        # /v1/models (list) returns the vLLM-style "max_model_len" field.
        per_model_404 = _resp(404, {})
        list_200 = _resp(200, {
            "object": "list",
            "data": [
                {"id": "Qwen3.8-27B", "max_model_len": 262144},
                {"id": "Qwen/Qwen3.6-35B-A3B-FP8", "max_model_len": 262144},
            ],
        })
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[per_model_404, list_200])
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=client):
            result = await fetch_openai_context_window(
                "Qwen/Qwen3.6-35B-A3B-FP8", "https://inference.hetzner.com/api/v1", "tok",
            )
        assert result == 262144

    @pytest.mark.asyncio
    async def test_per_model_endpoint_still_preferred_when_available(self):
        per_model_200 = _resp(200, {"context_length": 131072})
        client = AsyncMock()
        client.get = AsyncMock(return_value=per_model_200)
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=client):
            result = await fetch_openai_context_window("some-model", "https://api.example.com/v1", "tok")
        assert result == 131072
        assert client.get.call_count == 1  # never fell through to the list endpoint

    @pytest.mark.asyncio
    async def test_returns_zero_when_model_not_in_list_and_no_litellm(self):
        per_model_404 = _resp(404, {})
        list_200 = _resp(200, {"data": [{"id": "other-model", "max_model_len": 8192}]})
        litellm_404 = _resp(404, {})
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[per_model_404, list_200, litellm_404])
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=client):
            result = await fetch_openai_context_window("my-model", "https://api.example.com/v1", "tok")
        assert result == 0


class TestFetchOpenaiMaxOutputListFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_models_list(self):
        per_model_404 = _resp(404, {})
        list_200 = _resp(200, {"data": [{"id": "m", "max_output_tokens": 8192}]})
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[per_model_404, list_200])
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=client):
            result = await fetch_openai_max_output("m", "https://api.example.com/v1", "tok")
        assert result == 8192

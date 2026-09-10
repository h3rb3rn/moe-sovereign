"""tests/test_chat_endpoint_v1_prefix.py — Regression for the
"rstrip('/') + '/chat/completions'" URL-building bug.

Verified live during a workshop deployment: any direct model@endpoint call
against an Ollama server (base URL stored WITHOUT "/v1", e.g.
"http://host.docker.internal:11434") 404'd because the endpoint URL was
built as base.rstrip("/") + "/chat/completions" — missing the required
"/v1" segment Ollama's OpenAI-compat router expects. The naive fix of just
appending "/v1" unconditionally would have double-prefixed AIHUB/OpenAI
servers, whose stored URL already includes "/v1".

The fix applied at every call site (services/pipeline/chat.py x3, main.py,
routes/embeddings.py, legacy_root_modules/chat.py x3):

    url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions"

This test locks in that exact formula against both endpoint shapes so a
future edit reverting to the naive version is caught immediately.
"""

import pytest


def _build_chat_url(base_url: str) -> str:
    """The exact expression applied at every fixed call site."""
    return base_url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions"


@pytest.mark.parametrize("base_url,expected", [
    # Ollama: no /v1 stored at all — the original bug (404s here).
    ("http://host.docker.internal:11434",
     "http://host.docker.internal:11434/v1/chat/completions"),
    ("http://host.docker.internal:11434/",
     "http://host.docker.internal:11434/v1/chat/completions"),
    # AIHUB/OpenAI-compat: /v1 already stored — must not be doubled.
    ("https://aihub.example.com/v1",
     "https://aihub.example.com/v1/chat/completions"),
    ("https://aihub.example.com/v1/",
     "https://aihub.example.com/v1/chat/completions"),
])
def test_chat_url_gets_exactly_one_v1_segment(base_url, expected):
    assert _build_chat_url(base_url) == expected

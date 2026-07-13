"""tests/pipeline/test_chat_normalize_ollama_native.py — Unit tests for
services/pipeline/chat.py::_normalize_messages_for_ollama_native.

Confirmed live in production: the tool_choice=auto premature-stop retry
(_retry_tool_agent_fallback) and the Hermes3 tool-agent path both post to
Ollama's native /api/chat endpoint using the OpenAI-format messages list
_build_tool_messages produces — where tool_calls[].function.arguments is a
JSON-encoded string, per the OpenAI wire format. Ollama's native endpoint
requires arguments as a parsed object instead, and rejects the raw string
form immediately (~20ms, before any generation) with "Value looks like
object, but can't find closing '}' symbol". This left every retry attempt
on a session with prior tool-call history permanently failing, so a
detected premature-stop just returned the original broken response to the
client with no working fallback — see services/pipeline/anthropic.py's
_normalize_for_ollama, which already carried this fix for the Claude Code
path.
"""

import json

from services.pipeline.chat import _normalize_messages_for_ollama_native


class TestNormalizeMessagesForOllamaNative:
    def test_converts_string_arguments_to_dict(self):
        messages = [
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {"name": "Bash", "arguments": '{"command": "ls -la"}'},
                    }
                ],
            },
        ]
        out = _normalize_messages_for_ollama_native(messages)
        tc = out[1]["tool_calls"][0]
        assert tc["function"]["arguments"] == {"command": "ls -la"}
        # OpenAI-only fields must not leak into the native shape.
        assert "id" not in tc
        assert "type" not in tc

    def test_leaves_already_dict_arguments_untouched(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "x", "type": "function",
                     "function": {"name": "Read", "arguments": {"path": "a.py"}}},
                ],
            },
        ]
        out = _normalize_messages_for_ollama_native(messages)
        assert out[0]["tool_calls"][0]["function"]["arguments"] == {"path": "a.py"}

    def test_malformed_arguments_json_falls_back_to_empty_dict(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "x", "type": "function",
                     "function": {"name": "Bash", "arguments": "{not valid json"}},
                ],
            },
        ]
        out = _normalize_messages_for_ollama_native(messages)
        assert out[0]["tool_calls"][0]["function"]["arguments"] == {}

    def test_strips_tool_call_id_from_tool_messages(self):
        messages = [
            {"role": "tool", "tool_call_id": "call_abc123", "content": "ls output", "name": "Bash"},
        ]
        out = _normalize_messages_for_ollama_native(messages)
        assert "tool_call_id" not in out[0]
        assert out[0]["content"] == "ls output"

    def test_handles_many_tool_calls_across_long_history(self):
        # Reproduces the exact live shape that triggered the bug: a long
        # multi-turn OpenCode session with hundreds of accumulated tool
        # result/tool_call messages (tool_msgs=234 observed live).
        messages = []
        for i in range(200):
            messages.append({
                "role": "assistant", "content": "",
                "tool_calls": [{
                    "id": f"call_{i}", "type": "function",
                    "function": {"name": "Read", "arguments": json.dumps({"path": f"file_{i}.py"})},
                }],
            })
            messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": f"contents of file_{i}"})
        out = _normalize_messages_for_ollama_native(messages)
        assert len(out) == len(messages)
        for i in range(200):
            assert out[2 * i]["tool_calls"][0]["function"]["arguments"] == {"path": f"file_{i}.py"}
            assert "tool_call_id" not in out[2 * i + 1]

    def test_non_assistant_non_tool_messages_pass_through_unchanged(self):
        messages = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ]
        out = _normalize_messages_for_ollama_native(messages)
        assert out == messages

    def test_never_mutates_the_input_list(self):
        original_args = '{"command": "ls"}'
        messages = [
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "x", "type": "function",
                                 "function": {"name": "Bash", "arguments": original_args}}],
            },
        ]
        _normalize_messages_for_ollama_native(messages)
        assert messages[0]["tool_calls"][0]["function"]["arguments"] == original_args

    def test_skips_non_dict_tool_call_entries_defensively(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": ["not-a-dict", None]},
        ]
        out = _normalize_messages_for_ollama_native(messages)
        assert out[0]["tool_calls"] == []

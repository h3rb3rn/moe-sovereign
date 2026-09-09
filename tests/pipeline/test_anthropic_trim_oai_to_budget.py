"""tests/pipeline/test_anthropic_trim_oai_to_budget.py — Unit tests for
services/pipeline/anthropic.py::_trim_oai_to_budget_impl.

Regression for a live 400 observed on the Hetzner provider ("No user query
found in messages"): the old grouping started a new group at *every*
non-tool message, including an assistant tool_call message. Once history
trimming squeezed a tool-call-heavy conversation down to a single surviving
group, that group could be [assistant(tool_call), tool(result)] — no user
message at all — which a provider that validates for a user turn rejects.
Grouping must start a new group only at a "user" message so a tool-call
round-trip always stays bundled with the user message that triggered it.
"""

from services.pipeline.anthropic import _trim_oai_to_budget_impl


def _big(role: str, n: int = 2000) -> dict:
    return {"role": role, "content": "x" * n}


class TestTrimOaiToBudget:
    def test_no_trim_when_under_budget(self):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        kept, dropped, dropped_groups = _trim_oai_to_budget_impl(msgs, available_input_tokens=1000)
        assert kept == msgs
        assert dropped is False
        assert dropped_groups == []

    def test_tool_call_round_trip_stays_bundled_with_its_user_message(self):
        # One old, large user+tool exchange, then a second (current) turn
        # that is itself a large multi-round tool-call chain.
        msgs = [
            {"role": "system", "content": "sys"},
            _big("user"),
            _big("assistant"),
            {"role": "tool", "tool_call_id": "1", "content": "old result"},
            _big("user"),
            {"role": "assistant", "tool_calls": [{"id": "2", "type": "function",
             "function": {"name": "ls", "arguments": "{}"}}], "content": ""},
            {"role": "tool", "tool_call_id": "2", "content": "tool output " + "y" * 2000},
        ]
        # Budget only large enough for the system message plus one group.
        kept, dropped, dropped_groups = _trim_oai_to_budget_impl(msgs, available_input_tokens=700)

        assert dropped is True
        roles = [m.get("role") for m in kept]
        assert "user" in roles, f"trimmed history has no user message: {roles}"
        # The surviving group must be the second (most recent) user turn,
        # with its tool_call + tool result still attached — never split off
        # into a userless assistant/tool-only remainder.
        assert kept[0]["role"] == "system"
        assert kept[1]["content"] == msgs[4]["content"]  # the second user message
        assert kept[-1]["role"] == "tool"
        assert len(dropped_groups) == 1
        assert dropped_groups[0][0]["content"] == msgs[1]["content"]  # the first user message's group

    def test_never_drops_down_to_zero_groups(self):
        # Even a single, oversized final group is kept whole rather than split.
        msgs = [
            {"role": "system", "content": "sys"},
            _big("user", 50_000),
            {"role": "assistant", "tool_calls": [{"id": "1", "type": "function",
             "function": {"name": "read", "arguments": "{}"}}], "content": ""},
            {"role": "tool", "tool_call_id": "1", "content": "y" * 50_000},
        ]
        kept, dropped, _ = _trim_oai_to_budget_impl(msgs, available_input_tokens=10)
        assert dropped is False  # only one group exists — nothing to drop
        assert kept == msgs
        assert any(m.get("role") == "user" for m in kept)

    def test_drops_oldest_groups_first(self):
        msgs = [
            {"role": "system", "content": "sys"},
            _big("user"),  # oldest — should be dropped first
            {"role": "assistant", "content": "ack1"},
            _big("user"),  # middle
            {"role": "assistant", "content": "ack2"},
            _big("user"),  # newest — must survive
            {"role": "assistant", "content": "ack3"},
        ]
        kept, dropped, dropped_groups = _trim_oai_to_budget_impl(msgs, available_input_tokens=500)
        assert dropped is True
        assert len(dropped_groups) >= 1
        # The most recent user message must be present in what's kept.
        assert kept[-2]["content"] == msgs[5]["content"]

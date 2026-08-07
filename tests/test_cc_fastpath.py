"""Claude-compatible fast-path must preserve mandatory precision routing."""

from services.cc_fastpath import is_fastpath_request, requires_precision_pipeline


def test_short_precision_request_never_uses_direct_llm_fastpath(monkeypatch):
    monkeypatch.setenv("CC_FASTPATH", "1")

    assert is_fastpath_request(
        {
            "messages": [
                {"role": "user", "content": "Berechne den GGT von 391 und 299."}
            ]
        }
    ) == ""


def test_non_precision_trivial_request_keeps_existing_fastpath(monkeypatch):
    monkeypatch.setenv("CC_FASTPATH", "1")

    assert is_fastpath_request(
        {"messages": [{"role": "user", "content": "Antworte mit OK."}]}
    ) == "trivial"


def test_plain_precision_turn_forces_bound_pipeline_even_for_native_profile():
    body = {
        "messages": [
            {"role": "user", "content": "Berechne den GGT von 391 und 299."}
        ]
    }

    assert requires_precision_pipeline(body) is True


def test_precision_in_history_does_not_force_unrelated_current_turn():
    body = {
        "messages": [
            {"role": "user", "content": "Berechne den GGT von 391 und 299."},
            {"role": "assistant", "content": "23"},
            {"role": "user", "content": "Erkläre mir nun Euklids Algorithmus."},
        ]
    }

    assert requires_precision_pipeline(body) is False


def test_client_tool_contract_takes_precedence_over_precision_pipeline():
    body = {
        "tools": [{"name": "calculator", "input_schema": {"type": "object"}}],
        "messages": [
            {"role": "user", "content": "Berechne den GGT von 391 und 299."}
        ],
    }

    assert requires_precision_pipeline(body) is False


def test_tool_result_turn_stays_on_client_tool_path():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "GCD=23",
                    }
                ],
            }
        ]
    }

    assert requires_precision_pipeline(body) is False

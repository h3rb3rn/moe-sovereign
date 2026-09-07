"""tests/test_generate_diverse_training_seeds.py — Unit tests for the pure
parsing functions in scripts/generate_diverse_training_seeds.py (the
DeepSeek-V4-Flash offline batch generator for LUMI-G post-training data
diversity). vllm itself is a LUMI-G-only dependency, imported lazily inside
the generation functions, so this module imports cleanly without it -- only
the parsing logic (never the model-loading/generation calls) is unit-tested
here.
"""

from __future__ import annotations

import json

from scripts.generate_diverse_training_seeds import (
    _ROLE_SYSTEM_PROMPTS,
    parse_grounding_output,
    parse_loom_output,
    parse_role_sft_output,
    render_chatml,
)


class TestParseLoomOutput:
    def test_parses_well_formed_delimited_output(self):
        broken = "use loom::sync::atomic::AtomicUsize;\nfn a() { /* relaxed publish */ }"
        fixed = "use loom::sync::atomic::AtomicUsize;\nfn a() { /* release publish */ }"
        payload = (
            "===SCENARIO_NAME===\nseqlock_publish\n"
            f"===BROKEN_SOURCE===\n{broken}\n"
            f"===FIXED_SOURCE===\n{fixed}\n"
            "===END==="
        )
        result = parse_loom_output(payload)
        assert result == {"scenario_name": "seqlock_publish", "broken_source": broken, "fixed_source": fixed}

    def test_tolerates_preamble_and_multiline_code_with_braces_and_quotes(self):
        # Regression test for the live zai-org/GLM-4.5-Air failure (job
        # 21798250, role_sft/coder smoke test): models reliably produce
        # real, on-topic Rust code but do NOT reliably JSON-escape raw
        # newlines/quotes inside a string value -- json.loads() failed on
        # every one of 5 real completions despite genuinely correct
        # content. The delimited format needs no escaping at all, so
        # multi-line code with braces/quotes must pass straight through.
        code = 'fn f() {\n    let s = "a \\"quoted\\" value";\n    assert_eq!(1, 1);\n}'
        payload = (
            "Let me think about this {step: 1} first.\n"
            "===SCENARIO_NAME===\nseqlock_publish\n"
            f"===BROKEN_SOURCE===\n{code}\n"
            f"===FIXED_SOURCE===\n{code}\n"
            "===END==="
        )
        result = parse_loom_output(payload)
        assert result is not None
        assert result["broken_source"] == code
        assert result["fixed_source"] == code

    def test_returns_none_when_a_marker_is_missing(self):
        payload = "===SCENARIO_NAME===\nx\n===BROKEN_SOURCE===\na\n===END==="  # FIXED_SOURCE missing
        assert parse_loom_output(payload) is None

    def test_returns_none_when_a_field_is_empty(self):
        payload = "===SCENARIO_NAME===\nx\n===BROKEN_SOURCE===\n\n===FIXED_SOURCE===\nb\n===END==="
        assert parse_loom_output(payload) is None

    def test_returns_none_when_markers_are_out_of_order(self):
        payload = "===BROKEN_SOURCE===\na\n===SCENARIO_NAME===\nx\n===FIXED_SOURCE===\nb\n===END==="
        assert parse_loom_output(payload) is None

    def test_returns_none_on_plain_prose_with_no_markers(self):
        assert parse_loom_output("I cannot help with that request.") is None

    def test_returns_none_when_source_fields_are_implausibly_short(self):
        # Regression test for job 21798693's loom retest: one of 2 real
        # completions "parsed successfully" with all three fields equal to
        # the 4-character garbage string "`, `" -- non-empty, so it passed
        # the old bare-truthiness check, but obviously not a real Rust file.
        payload = "===SCENARIO_NAME===\n`, `\n===BROKEN_SOURCE===\n`, `\n===FIXED_SOURCE===\n`, `\n===END==="
        assert parse_loom_output(payload) is None


class TestParseGroundingOutput:
    def test_parses_well_formed_json_array(self):
        payload = json.dumps(["What is 2+2?", "Convert 10 miles to km"])
        assert parse_grounding_output(payload, expected_count=2) == ["What is 2+2?", "Convert 10 miles to km"]

    def test_truncates_to_expected_count(self):
        payload = json.dumps(["a", "b", "c", "d"])
        assert parse_grounding_output(payload, expected_count=2) == ["a", "b"]

    def test_drops_empty_and_non_string_entries(self):
        payload = json.dumps(["real request", "", "  ", 42, None])
        assert parse_grounding_output(payload, expected_count=5) == ["real request"]

    def test_returns_empty_list_on_malformed_json(self):
        assert parse_grounding_output("[not valid", expected_count=5) == []

    def test_returns_empty_list_when_not_a_json_array(self):
        payload = json.dumps({"not": "an array"})
        assert parse_grounding_output(payload, expected_count=5) == []

    def test_extracts_real_array_past_a_preamble_containing_brackets(self):
        # Regression test for the live zai-org/GLM-4.5-Air failure (job
        # 21701726, 2026-09-03): a single greedy `\[.*\]` regex spans from
        # the model's FIRST `[` to its LAST `]`, so any bracket characters
        # in reasoning/preamble text before the real answer array swallow
        # the whole span into one unparseable blob. 7 of 8 grounding
        # categories parsed to 0 usable requests this way despite real,
        # on-topic generations. The fixed parser must find the real array
        # regardless of what precedes it.
        payload = (
            "Let me brainstorm domains [data privacy, retention policy] first.\n"
            "Here is the array: " + json.dumps(["What is GDPR?", "How long can we keep customer emails?"])
        )
        assert parse_grounding_output(payload, expected_count=2) == [
            "What is GDPR?", "How long can we keep customer emails?",
        ]

    def test_ignores_a_decoy_array_and_stray_bracket_before_the_real_one(self):
        payload = '["decoy"] some text with a stray ] bracket then ' + json.dumps(["real1", "real2", "real3"])
        assert parse_grounding_output(payload, expected_count=3) == ["real1", "real2", "real3"]

    def test_tolerates_brackets_inside_a_string_value(self):
        payload = json.dumps(["Explain how a[i] indexing works in Python.", "Second item."])
        assert parse_grounding_output(payload, expected_count=2) == [
            "Explain how a[i] indexing works in Python.", "Second item.",
        ]


class TestParseRoleSftOutput:
    def test_parses_well_formed_delimited_output(self):
        payload = (
            "===USER_REQUEST===\nReview this SQL query.\n"
            "===ASSISTANT_RESPONSE===\nLooks fine, but add an index on user_id.\n"
            "===END==="
        )
        assert parse_role_sft_output(payload) == {
            "user_request": "Review this SQL query.",
            "assistant_response": "Looks fine, but add an index on user_id.",
        }

    def test_tolerates_a_preamble_and_multiline_code_with_braces_and_quotes(self):
        # Root cause of job 21798250 (role_sft/coder smoke test,
        # GLM-4.5-Air): 5/5 real, on-topic Rust code responses, 0/5 valid
        # JSON, because the model does not reliably escape raw newlines/
        # quotes inside a JSON string value. The delimited format needs no
        # escaping, so real code containing braces and quotes must survive
        # unmodified.
        code = 'fn f() {\n    let s = "quoted";\n    s\n}'
        payload = (
            "```json\nLet me draft this {plan: 1} first.\n"
            "===USER_REQUEST===\nWrite a function returning a quoted string.\n"
            f"===ASSISTANT_RESPONSE===\n{code}\n"
            "===END===\n```"
        )
        result = parse_role_sft_output(payload)
        assert result is not None
        assert result["user_request"] == "Write a function returning a quoted string."
        assert result["assistant_response"] == code

    def test_returns_none_when_a_marker_is_missing(self):
        payload = "===USER_REQUEST===\nOnly a request, no response.\n===END==="
        assert parse_role_sft_output(payload) is None

    def test_returns_none_on_plain_prose_with_no_markers(self):
        assert parse_role_sft_output("I cannot help with that request.") is None

    def test_picks_the_final_attempt_after_a_botched_placeholder_retry(self):
        # Root cause of the live comparison run (deepseek/deepseek-v4-pro-0813
        # vs z-ai/glm-5.3, --role research): one real completion wrote a
        # throwaway "..." placeholder block, then visibly reconsidered in
        # plain reasoning text, then produced a real second attempt.
        # Matching the FIRST occurrence of each marker (the original
        # implementation) captured the placeholder plus the leftover
        # reasoning text as one field's content instead of the real answer.
        real_response = "Cite arXiv:2205.14135 for FlashAttention; the paper does not report the exact number you asked for."
        payload = (
            "===USER_REQUEST===\n...\n"
            "===ASSISTANT_RESPONSE===\n...\n"
            "===END===\n\n"
            "Wait, that was a placeholder. Let me actually write a concrete, realistic example instead:\n\n"
            "===USER_REQUEST===\nWhat is the exact memory reduction FlashAttention reports for GPT-2?\n"
            f"===ASSISTANT_RESPONSE===\n{real_response}\n"
            "===END==="
        )
        result = parse_role_sft_output(payload)
        assert result is not None
        assert result["user_request"] == "What is the exact memory reduction FlashAttention reports for GPT-2?"
        assert result["assistant_response"] == real_response

    def test_returns_none_when_assistant_response_is_implausibly_short(self):
        payload = "===USER_REQUEST===\nWhat is 2+2?\n===ASSISTANT_RESPONSE===\nok\n===END==="
        assert parse_role_sft_output(payload) is None

    def test_all_ten_roles_have_a_system_prompt(self):
        assert set(_ROLE_SYSTEM_PROMPTS.keys()) == {
            "coder", "precision", "graphrag", "governance", "research",
            "security", "datainfra", "omni", "planner", "judge",
        }


class TestRenderChatml:
    def test_renders_system_user_assistant_in_order(self):
        text = render_chatml("SYS", "USER MSG", "ASSISTANT REPLY")
        assert text == (
            "<|im_start|>system\nSYS<|im_end|>\n"
            "<|im_start|>user\nUSER MSG<|im_end|>\n"
            "<|im_start|>assistant\nASSISTANT REPLY<|im_end|>\n"
        )

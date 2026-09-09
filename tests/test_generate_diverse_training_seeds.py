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
    _GENERIC_ROLE_ATTRACTOR_KEYWORDS,
    _GENERIC_ROLE_TOPIC_HINTS,
    _JUDGE_CRITIC_PATTERN_FOCUS,
    _PLANNER_PATTERN_FOCUS,
    _ROLE_SFT_GENERATION_TEMPLATE,
    _ROLE_SYSTEM_PROMPTS,
    _critic_response_is_noncompliant,
    _role_sft_output_violates_topic,
    _validate_planner_task_array,
    parse_grounding_output,
    parse_judge_critic_sft_output,
    parse_loom_output,
    parse_planner_sft_output,
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

    def test_rejects_unfilled_template_placeholder(self):
        # Found live, job 21829009 (GLM-4.5-Air): 10/577 "successfully
        # parsed" examples were the model echoing the template's own
        # placeholder text verbatim instead of generating real content.
        payload = (
            "===USER_REQUEST===\n<a specific, realistic user message, one or more lines>\n"
            "===ASSISTANT_RESPONSE===\nA real, on-topic response that is long enough to pass length checks.\n"
            "===END==="
        )
        assert parse_role_sft_output(payload) is None

    def test_does_not_reject_real_content_containing_angle_brackets(self):
        # A real Rust/C++ generic type (Vec<T>, HashMap<K, V>) must not be
        # mistaken for a placeholder -- only a field that is ENTIRELY one
        # bracket pair start-to-end is a template echo.
        payload = (
            "===USER_REQUEST===\nWrite a function that returns a Vec<T> of results.\n"
            "===ASSISTANT_RESPONSE===\nHere is an implementation using Vec<T> and HashMap<K, V> as requested.\n"
            "===END==="
        )
        result = parse_role_sft_output(payload)
        assert result is not None
        assert "Vec<T>" in result["user_request"]

    def test_rejects_degenerate_short_user_request_fragment(self):
        # Found live, job 21832982 (governance, GLM-4.5-Air): USER_REQUEST
        # fields of "and", "` and `" -- clearly leaked fragments, not real
        # requests -- slipped through because only ASSISTANT_RESPONSE had a
        # minimum-length floor.
        payload = (
            "===USER_REQUEST===\nand\n"
            "===ASSISTANT_RESPONSE===\nA real, on-topic response that is long enough to pass length checks.\n"
            "===END==="
        )
        assert parse_role_sft_output(payload) is None

    def test_rejects_bracketed_placeholder_fragment(self):
        payload = (
            "===USER_REQUEST===\n[User query]\n"
            "===ASSISTANT_RESPONSE===\nA real, on-topic response that is long enough to pass length checks.\n"
            "===END==="
        )
        assert parse_role_sft_output(payload) is None

    def test_does_not_reject_real_short_request(self):
        payload = (
            "===USER_REQUEST===\nWhat is 2+2?\n"
            "===ASSISTANT_RESPONSE===\nA real, on-topic response that is long enough to pass length checks.\n"
            "===END==="
        )
        assert parse_role_sft_output(payload) is not None

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


class TestValidatePlannerTaskArray:
    def test_accepts_a_real_simple_task(self):
        assert _validate_planner_task_array(json.dumps(
            [{"task": "Calculate 47+53", "category": "precision_tools", "mcp_tool": "calculate", "mcp_args": {"expression": "47+53"}}]
        )) is True

    def test_accepts_a_chained_task_array(self):
        assert _validate_planner_task_array(json.dumps([
            {"id": "year1", "task": "Year 1 tariff", "category": "precision_tools", "mcp_tool": "decimal_finance",
             "mcp_args": {"operation": "add", "operands": ["0.10", "0"], "currency": "EUR", "scale": 4, "rounding": "half_even"}},
            {"id": "year2", "task": "Year 2 tariff (+5%)", "category": "precision_tools", "mcp_tool": "decimal_finance",
             "mcp_args": {"operation": "percentage", "operands": [{"$task_result": "year1"}, "105"], "currency": "EUR", "scale": 4, "rounding": "half_even"}},
        ])) is True

    def test_rejects_malformed_json(self):
        assert _validate_planner_task_array("[not valid json") is False

    def test_rejects_a_json_object_instead_of_array(self):
        assert _validate_planner_task_array(json.dumps({"task": "x", "category": "research"})) is False

    def test_rejects_empty_array(self):
        assert _validate_planner_task_array("[]") is False

    def test_rejects_task_missing_category(self):
        assert _validate_planner_task_array(json.dumps([{"task": "Calculate 47+53"}])) is False

    def test_rejects_mcp_args_that_is_not_a_dict(self):
        # The exact real failure mode (Candidate 5): a plausible-looking but
        # wrong argument encoding should fail structural validation even
        # though the JSON itself parses.
        assert _validate_planner_task_array(json.dumps(
            [{"task": "x", "category": "precision_tools", "mcp_tool": "decimal_finance", "mcp_args": "operation=add"}]
        )) is False


class TestParsePlannerSftOutput:
    def test_parses_well_formed_output(self):
        payload = (
            "===USER_REQUEST===\nWhat is 47+53?\n"
            "===ASSISTANT_RESPONSE===\n"
            + json.dumps([{"task": "Calculate 47+53", "category": "precision_tools", "mcp_tool": "calculate", "mcp_args": {"expression": "47+53"}}])
            + "\n===END==="
        )
        result = parse_planner_sft_output(payload)
        assert result is not None
        assert result["user_request"] == "What is 47+53?"
        assert _validate_planner_task_array(result["assistant_response"])

    def test_strips_a_markdown_fence_around_the_json(self):
        arr = json.dumps([{"task": "x", "category": "research"}])
        payload = f"===USER_REQUEST===\nResearch x\n===ASSISTANT_RESPONSE===\n```json\n{arr}\n```\n===END==="
        result = parse_planner_sft_output(payload)
        assert result is not None
        assert json.loads(result["assistant_response"]) == [{"task": "x", "category": "research"}]

    def test_returns_none_when_response_is_prose_not_json(self):
        payload = "===USER_REQUEST===\nWhat is 47+53?\n===ASSISTANT_RESPONSE===\nThe answer is 100.\n===END==="
        assert parse_planner_sft_output(payload) is None

    def test_returns_none_when_a_marker_is_missing(self):
        payload = "===USER_REQUEST===\nWhat is 47+53?\n===END==="
        assert parse_planner_sft_output(payload) is None

    def test_all_pattern_focuses_are_nonempty_strings(self):
        assert len(_PLANNER_PATTERN_FOCUS) >= 6
        assert all(isinstance(v, str) and v.strip() for v in _PLANNER_PATTERN_FOCUS.values())


class TestCriticResponseIsNoncompliant:
    def test_bare_confirmed_is_compliant(self):
        assert _critic_response_is_noncompliant("CONFIRMED") is False

    def test_direct_correction_is_compliant(self):
        assert _critic_response_is_noncompliant("fn f() { Ordering::Release }") is False

    def test_preamble_before_confirmed_is_noncompliant(self):
        # Root cause example from graph/synthesis.py's own docstring: an
        # ~800-word deliberation ending in a bare CONFIRMED still fails,
        # because .startswith("CONFIRMED") in production would reject it.
        assert _critic_response_is_noncompliant("Let me think about whether this is unsupported... CONFIRMED") is True

    def test_answer_preamble_is_noncompliant(self):
        assert _critic_response_is_noncompliant('The provided "answer to check" is severely corrupted.') is True

    def test_empty_is_noncompliant(self):
        assert _critic_response_is_noncompliant("") is True


class TestParseJudgeCriticSftOutput:
    def test_parses_a_confirmed_example(self):
        payload = (
            "===QUESTION===\nWhat is 2+2?\n"
            "===ANSWER_TO_CHECK===\n4\n"
            "===CRITIC_RESPONSE===\nCONFIRMED\n===END==="
        )
        result = parse_judge_critic_sft_output(payload)
        assert result is not None
        assert result["assistant_response"] == "CONFIRMED"
        assert "QUESTION:" in result["user_request"]
        assert "ANSWER TO CHECK:" in result["user_request"]

    def test_parses_a_direct_correction_example(self):
        payload = (
            "===QUESTION===\nImplement a Rust SPSC ring buffer publish.\n"
            "===ANSWER_TO_CHECK===\npayload.store(x, Ordering::Relaxed);\n"
            "===CRITIC_RESPONSE===\npayload.store(x, Ordering::Release);\n===END==="
        )
        result = parse_judge_critic_sft_output(payload)
        assert result is not None
        assert result["assistant_response"] == "payload.store(x, Ordering::Release);"

    def test_rejects_a_response_with_preamble(self):
        payload = (
            "===QUESTION===\nWhat is 2+2?\n===ANSWER_TO_CHECK===\n5\n"
            "===CRITIC_RESPONSE===\nThe answer contains an error. It should be 4.\n===END==="
        )
        assert parse_judge_critic_sft_output(payload) is None

    def test_rejects_a_deliberation_ending_in_confirmed(self):
        payload = (
            "===QUESTION===\nWhat is 2+2?\n===ANSWER_TO_CHECK===\n4\n"
            "===CRITIC_RESPONSE===\nLet me carefully consider this claim... CONFIRMED\n===END==="
        )
        assert parse_judge_critic_sft_output(payload) is None

    def test_all_pattern_focuses_are_nonempty_strings(self):
        assert len(_JUDGE_CRITIC_PATTERN_FOCUS) >= 3
        assert all(isinstance(v, str) and v.strip() for v in _JUDGE_CRITIC_PATTERN_FOCUS.values())


class TestRenderChatml:
    def test_renders_system_user_assistant_in_order(self):
        text = render_chatml("SYS", "USER MSG", "ASSISTANT REPLY")
        assert text == (
            "<|im_start|>system\nSYS<|im_end|>\n"
            "<|im_start|>user\nUSER MSG<|im_end|>\n"
            "<|im_start|>assistant\nASSISTANT REPLY<|im_end|>\n"
        )


class TestGenericRoleTopicHints:
    """Regression coverage for the 2026-09-08 self-critique finding: a
    single static role_sft prompt with no topic scaffolding let teacher
    models collapse onto the one example named in their own system prompt
    (Mistral Large 3: 99/400 `coder` examples near-identical "lock-free MPSC
    queue"; Kimi K3: 86/258 near-identical SPSC-ring-buffer variants). Every
    generic (non-planner, non-judge) role must have a real, plural set of
    topic anchors, and the template must accept one.
    """

    def test_every_generic_role_has_topic_hints(self):
        generic_roles = set(_ROLE_SYSTEM_PROMPTS) - {"planner", "judge"}
        assert generic_roles == set(_GENERIC_ROLE_TOPIC_HINTS)

    def test_every_role_has_multiple_distinct_hints(self):
        for role, hints in _GENERIC_ROLE_TOPIC_HINTS.items():
            assert len(hints) >= 4, f"{role} has too few topic hints for real diversity"
            assert len(set(hints)) == len(hints), f"{role} has duplicate topic hints"
            assert all(isinstance(h, str) and h.strip() for h in hints)

    def test_template_formats_with_topic_hint(self):
        rendered = _ROLE_SFT_GENERATION_TEMPLATE.format(
            system_prompt="SYS", topic_hint="HINT", other_hints="OTHER1; OTHER2")
        assert "SYS" in rendered
        assert "HINT" in rendered
        assert "OTHER1; OTHER2" in rendered


class TestRoleSftOutputViolatesTopic:
    """Regression coverage for the 2026-09-08 mechanical post-filter: the
    {other_hints} negative-constraint prompt reduced but did not eliminate
    GLM-4.5-Air's bias toward `coder`'s "lock-free memory ordering" example
    (72% -> 54% on a 111-example sample, still far above the ~10% a uniform
    10-hint rotation would produce) -- this filter discards the residual
    off-topic examples rather than accepting the partial prompt-engineering
    fix as final.
    """

    def test_flags_offtopic_example_using_attractor_keyword(self):
        offtopic_hint = _GENERIC_ROLE_TOPIC_HINTS["coder"][1]  # python data pipeline
        assert _role_sft_output_violates_topic(
            "coder", offtopic_hint, "Implement a lock-free SPSC ring buffer in Rust."
        )

    def test_allows_ontopic_example_on_the_attractor_hint_itself(self):
        memory_ordering_hint = next(
            h for h in _GENERIC_ROLE_TOPIC_HINTS["coder"] if "lock-free" in h.lower())
        assert not _role_sft_output_violates_topic(
            "coder", memory_ordering_hint, "Implement a lock-free SPSC ring buffer in Rust.")

    def test_allows_offtopic_hint_with_clean_content(self):
        offtopic_hint = _GENERIC_ROLE_TOPIC_HINTS["coder"][1]
        assert not _role_sft_output_violates_topic(
            "coder", offtopic_hint, "Write a Python CLI tool that tails a log file.")

    def test_role_with_no_confirmed_attractor_keywords_never_flags(self):
        assert not _role_sft_output_violates_topic(
            "precision", _GENERIC_ROLE_TOPIC_HINTS["precision"][0],
            "This mentions lock-free and atomic just to check no false positive.")

    def test_every_attractor_keyword_role_is_a_real_generic_role(self):
        assert set(_GENERIC_ROLE_ATTRACTOR_KEYWORDS) <= set(_GENERIC_ROLE_TOPIC_HINTS)

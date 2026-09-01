"""tests/test_curate_coder_expert_dataset.py — Unit tests for
scripts/curate_coder_expert_dataset.py: grouping raw rust_loom_check
outcomes by request_id, selecting the sandbox-verified passing target plus
its immediately-preceding failed attempt (if any) as correction context,
and rendering the ChatML "text" field train_expert_slm_pipeline.py expects.
"""

from __future__ import annotations

from scripts.curate_coder_expert_dataset import (
    _group_by_request_id,
    _select_target_and_context,
    build_training_example,
    curate,
)


def _record(request_id, attempt, passed, source="SRC", output_tail="TAIL"):
    return {
        "request_id": request_id, "attempt": attempt, "passed": passed,
        "compiles": True, "source": source, "output_tail": output_tail,
        "duration_ms": 100,
    }


class TestGroupByRequestId:
    def test_groups_records_by_request_id(self):
        records = [_record("a", 1, False), _record("a", 2, True), _record("b", 1, True)]
        groups = _group_by_request_id(records)
        assert set(groups.keys()) == {"a", "b"}
        assert len(groups["a"]) == 2
        assert len(groups["b"]) == 1


class TestSelectTargetAndContext:
    def test_picks_passing_target_and_preceding_failure(self):
        records = [_record("a", 1, False, source="broken"), _record("a", 2, True, source="fixed")]
        target, context = _select_target_and_context(records)
        assert target["source"] == "fixed"
        assert context["source"] == "broken"

    def test_first_try_success_has_no_context(self):
        records = [_record("a", 1, True, source="fixed")]
        target, context = _select_target_and_context(records)
        assert target["source"] == "fixed"
        assert context is None

    def test_returns_none_when_no_passing_record_in_group(self):
        records = [_record("a", 1, False), _record("a", 2, False)]
        assert _select_target_and_context(records) is None

    def test_uses_closest_preceding_failure_when_several_exist(self):
        records = [
            _record("a", 1, False, output_tail="oldest"),
            _record("a", 2, False, output_tail="closest"),
            _record("a", 3, True, source="fixed"),
        ]
        target, context = _select_target_and_context(records)
        assert context["output_tail"] == "closest"

    def test_ignores_unordered_input(self):
        records = [_record("a", 2, True, source="fixed"), _record("a", 1, False, source="broken")]
        target, context = _select_target_and_context(records)
        assert target["source"] == "fixed"
        assert context["source"] == "broken"


class TestBuildTrainingExample:
    def test_renders_chatml_with_rust_fence_around_source(self):
        target = _record("a", 2, True, source="fn main() {}")
        text = build_training_example(target, context=None)
        assert "<|im_start|>system" in text
        assert "<|im_start|>user" in text
        assert "<|im_start|>assistant\n```rust\nfn main() {}\n```<|im_end|>" in text
        assert "Your previous answer" not in text  # no retry context when there was no prior failure

    def test_includes_retry_context_when_prior_failure_present(self):
        target = _record("a", 2, True, source="fn fixed() {}")
        context = _record("a", 1, False, output_tail="thread panicked: race detected")
        text = build_training_example(target, context)
        assert "Your previous answer's Rust code compiles but Loom found" in text
        assert "thread panicked: race detected" in text

    def test_truncates_long_output_tail_to_1500_chars(self):
        target = _record("a", 2, True, source="fn fixed() {}")
        context = _record("a", 1, False, output_tail="x" * 5000)
        text = build_training_example(target, context)
        # The rendered text contains other content besides the tail, so just
        # assert the *tail itself* was capped, not the whole message length.
        assert "x" * 1500 in text
        assert "x" * 1501 not in text


class TestCurate:
    def test_skips_groups_without_a_passing_record(self):
        records = [
            _record("a", 1, False), _record("a", 2, False),
            _record("b", 1, True, source="ok"),
        ]
        texts = curate(records)
        assert len(texts) == 1
        assert "ok" in texts[0]

    def test_empty_input_yields_empty_output(self):
        assert curate([]) == []

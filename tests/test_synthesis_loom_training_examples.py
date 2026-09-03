"""tests/test_synthesis_loom_training_examples.py — Unit tests for
graph/synthesis.py::_record_loom_training_example.

Collects real rust_loom_check outcomes from production/benchmark traffic as
raw material for a future LUMI-G SFT/DPO pass on the acquire-release
memory-ordering candidate (docs/experiments/lumig_posttraining_candidates.md,
Candidate 1). Must never raise and must never record an inconclusive
(sandbox-unreachable/timeout) result.
"""

from __future__ import annotations

import json

import graph.synthesis as synthesis


class TestRecordLoomTrainingExample:
    def test_writes_jsonl_record_for_determinate_pass(self, tmp_path, monkeypatch):
        target = tmp_path / "loom_training_examples.jsonl"
        monkeypatch.setattr(synthesis, "_LOOM_TRAINING_EXAMPLES_FILE", str(target))

        synthesis._record_loom_training_example(
            "req-1", "use loom::sync::Arc;\n#[test]\nfn t() {}",
            {"compiles": True, "passed": True, "output_tail": "test result: ok", "duration_ms": 1200},
            attempt=1, max_attempts=3,
        )

        lines = target.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["request_id"] == "req-1"
        assert record["compiles"] is True
        assert record["passed"] is True
        assert record["attempt"] == 1
        assert record["max_attempts"] == 3
        assert "timestamp_utc" in record

    def test_writes_record_for_determinate_violation(self, tmp_path, monkeypatch):
        target = tmp_path / "loom_training_examples.jsonl"
        monkeypatch.setattr(synthesis, "_LOOM_TRAINING_EXAMPLES_FILE", str(target))

        synthesis._record_loom_training_example(
            "req-2", "use loom::sync::Arc;\n#[test]\nfn t() {}",
            {"compiles": True, "passed": False, "output_tail": "test result: FAILED", "duration_ms": 1900},
            attempt=1, max_attempts=3,
        )

        record = json.loads(target.read_text().strip())
        assert record["passed"] is False

    def test_appends_multiple_records(self, tmp_path, monkeypatch):
        target = tmp_path / "loom_training_examples.jsonl"
        monkeypatch.setattr(synthesis, "_LOOM_TRAINING_EXAMPLES_FILE", str(target))

        for i in range(3):
            synthesis._record_loom_training_example(
                "req-3", "source", {"compiles": True, "passed": True, "output_tail": "", "duration_ms": 1},
                attempt=i + 1, max_attempts=3,
            )
        assert len(target.read_text().strip().splitlines()) == 3

    def test_skips_inconclusive_sandbox_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "loom_training_examples.jsonl"
        monkeypatch.setattr(synthesis, "_LOOM_TRAINING_EXAMPLES_FILE", str(target))

        synthesis._record_loom_training_example(
            "req-4", "source", {"compiles": None, "passed": None, "sandbox_error": "unreachable"},
            attempt=1, max_attempts=3,
        )
        assert not target.exists()

    def test_skips_timed_out_result(self, tmp_path, monkeypatch):
        target = tmp_path / "loom_training_examples.jsonl"
        monkeypatch.setattr(synthesis, "_LOOM_TRAINING_EXAMPLES_FILE", str(target))

        synthesis._record_loom_training_example(
            "req-5", "source", {"compiles": True, "passed": None, "timed_out": True},
            attempt=1, max_attempts=3,
        )
        assert not target.exists()

    def test_write_failure_is_swallowed_not_raised(self, tmp_path, monkeypatch):
        # Directory as "file" path guarantees an OSError on open(..., "a").
        monkeypatch.setattr(synthesis, "_LOOM_TRAINING_EXAMPLES_FILE", str(tmp_path))
        synthesis._record_loom_training_example(
            "req-6", "source", {"compiles": True, "passed": True}, attempt=1, max_attempts=3,
        )  # must not raise

    def test_creates_parent_directory_if_missing(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "loom_training_examples.jsonl"
        monkeypatch.setattr(synthesis, "_LOOM_TRAINING_EXAMPLES_FILE", str(target))

        synthesis._record_loom_training_example(
            "req-7", "source", {"compiles": True, "passed": True}, attempt=1, max_attempts=3,
        )
        assert target.exists()


class TestRustLoomCheckCategoryGate:
    """EXPERT_MODELS has no "systems_programming" key -- a planner-assigned
    category of that name falls back to the "general" expert at dispatch
    time, while "code_reviewer" is the category actually routed to
    moe-expert-coder-4b. _RUST_LOOM_CHECK_CATEGORIES must include
    "code_reviewer", or the loom gate (and this collector) never fires for
    real coder-expert responses.
    """

    def test_code_reviewer_is_gated_for_loom_check(self):
        assert "code_reviewer" in synthesis._RUST_LOOM_CHECK_CATEGORIES

    def test_loom_categories_are_a_subset_of_compile_check_categories(self):
        # merger_node only ever evaluates the loom gate against
        # _code_categories_present, which is itself already restricted to
        # _RUST_COMPILE_CHECK_CATEGORIES -- a loom category outside that set
        # could never be reached, so this invariant must hold.
        assert synthesis._RUST_LOOM_CHECK_CATEGORIES <= synthesis._RUST_COMPILE_CHECK_CATEGORIES

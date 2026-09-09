"""tests/test_merge_training_datasets.py — Unit tests for
scripts/merge_training_datasets.py's pure merge/dedup/validation logic.
"""

from __future__ import annotations

import json

from scripts.merge_training_datasets import load_and_validate, merge


class TestLoadAndValidate:
    def test_loads_well_formed_lines(self, tmp_path):
        p = tmp_path / "a.jsonl"
        p.write_text('{"text": "one"}\n{"text": "two"}\n', encoding="utf-8")
        texts, total, skipped = load_and_validate(str(p))
        assert texts == ["one", "two"]
        assert total == 2
        assert skipped == 0

    def test_skips_malformed_json_without_aborting(self, tmp_path):
        p = tmp_path / "a.jsonl"
        p.write_text('{"text": "good"}\nnot valid json\n{"text": "also good"}\n', encoding="utf-8")
        texts, total, skipped = load_and_validate(str(p))
        assert texts == ["good", "also good"]
        assert total == 3
        assert skipped == 1

    def test_skips_lines_missing_text_field(self, tmp_path):
        p = tmp_path / "a.jsonl"
        p.write_text('{"text": "good"}\n{"other_field": "x"}\n{"text": ""}\n', encoding="utf-8")
        texts, total, skipped = load_and_validate(str(p))
        assert texts == ["good"]
        assert skipped == 2

    def test_ignores_blank_lines(self, tmp_path):
        p = tmp_path / "a.jsonl"
        p.write_text('{"text": "one"}\n\n\n{"text": "two"}\n', encoding="utf-8")
        texts, total, skipped = load_and_validate(str(p))
        assert texts == ["one", "two"]
        assert total == 2


class TestMerge:
    def test_merges_multiple_sources(self, tmp_path):
        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        a.write_text('{"text": "from_a_1"}\n{"text": "from_a_2"}\n', encoding="utf-8")
        b.write_text('{"text": "from_b_1"}\n', encoding="utf-8")
        combined, stats = merge([str(a), str(b)], seed=42)
        assert sorted(combined) == ["from_a_1", "from_a_2", "from_b_1"]
        assert stats["final_count"] == 3
        assert stats["per_source"][str(a)]["kept"] == 2
        assert stats["per_source"][str(b)]["kept"] == 1

    def test_deduplicates_exact_text_across_sources(self, tmp_path):
        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        a.write_text('{"text": "duplicate"}\n{"text": "unique_a"}\n', encoding="utf-8")
        b.write_text('{"text": "duplicate"}\n{"text": "unique_b"}\n', encoding="utf-8")
        combined, stats = merge([str(a), str(b)], seed=42)
        assert sorted(combined) == ["duplicate", "unique_a", "unique_b"]
        assert stats["duplicates_removed"] == 1

    def test_missing_input_file_is_warned_not_fatal(self, tmp_path):
        a = tmp_path / "a.jsonl"
        a.write_text('{"text": "exists"}\n', encoding="utf-8")
        missing = tmp_path / "does_not_exist.jsonl"
        combined, stats = merge([str(a), str(missing)], seed=42)
        assert combined == ["exists"]
        assert "error" in stats["per_source"][str(missing)]

    def test_shuffle_is_reproducible_with_same_seed(self, tmp_path):
        a = tmp_path / "a.jsonl"
        a.write_text("".join(f'{{"text": "item_{i}"}}\n' for i in range(20)), encoding="utf-8")
        combined1, _ = merge([str(a)], seed=7)
        combined2, _ = merge([str(a)], seed=7)
        assert combined1 == combined2

    def test_different_seeds_can_produce_different_order(self, tmp_path):
        a = tmp_path / "a.jsonl"
        a.write_text("".join(f'{{"text": "item_{i}"}}\n' for i in range(20)), encoding="utf-8")
        combined1, _ = merge([str(a)], seed=1)
        combined2, _ = merge([str(a)], seed=2)
        assert combined1 != combined2
        assert sorted(combined1) == sorted(combined2)

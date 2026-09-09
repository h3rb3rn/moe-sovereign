"""tests/test_generate_role_sft_openrouter.py — Unit tests for the pure
logic in scripts/generate_role_sft_openrouter.py (budget tracking, resume
counting, preflight thresholds). No real OpenRouter network calls here --
those are exercised by a real smoke-test invocation instead, per
.claude/rules/tests.md ("Avoid real external network/model calls in unit
tests").
"""

from __future__ import annotations

import pytest

from scripts.generate_role_sft_openrouter import (
    BudgetTracker,
    _count_existing_lines,
    preflight_check,
)


class TestBudgetTracker:
    def test_records_cost_and_counts(self):
        t = BudgetTracker(max_cost_usd=None)
        t.record(0.01, parsed=True)
        t.record(0.02, parsed=False)
        assert t.total_cost_usd == pytest.approx(0.03)
        assert t.requests_made == 2
        assert t.examples_written == 1
        assert t.examples_failed_parse == 1

    def test_avg_cost_per_example_ignores_failed_parses_in_denominator(self):
        t = BudgetTracker(max_cost_usd=None)
        t.record(0.10, parsed=True)
        t.record(0.10, parsed=False)
        # 0.20 total spent, but only 1 usable example -- avg should reflect
        # the real cost-per-usable-output, not cost-per-request.
        assert t.avg_cost_per_example() == pytest.approx(0.20)

    def test_avg_cost_per_example_is_zero_before_any_success(self):
        t = BudgetTracker(max_cost_usd=None)
        assert t.avg_cost_per_example() == 0.0

    def test_over_budget_false_when_no_limit_set(self):
        t = BudgetTracker(max_cost_usd=None)
        t.record(1000.0, parsed=True)
        assert t.over_budget() is False

    def test_over_budget_true_once_limit_reached(self):
        t = BudgetTracker(max_cost_usd=5.0)
        t.record(3.0, parsed=True)
        assert t.over_budget() is False
        t.record(2.5, parsed=True)
        assert t.over_budget() is True


class TestCountExistingLines:
    def test_returns_zero_for_missing_file(self, tmp_path):
        assert _count_existing_lines(tmp_path / "does_not_exist.jsonl") == 0

    def test_counts_non_blank_lines(self, tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text('{"a": 1}\n{"b": 2}\n\n{"c": 3}\n', encoding="utf-8")
        assert _count_existing_lines(p) == 3

    def test_supports_resume_arithmetic(self, tmp_path):
        # This is exactly what run() uses to decide how many more examples
        # to generate -- verifies the arithmetic a crash-and-restart relies on.
        p = tmp_path / "out.jsonl"
        p.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
        already = _count_existing_lines(p)
        target = 10
        assert target - already == 8


class TestPreflightCheck:
    def test_passes_with_lenient_thresholds(self, capsys):
        preflight_check(min_free_ram_mb=1, min_free_disk_gb=0.001, max_load_per_core=1000.0)
        assert "Preflight OK" in capsys.readouterr().out

    def test_aborts_on_impossible_ram_threshold(self):
        with pytest.raises(SystemExit):
            preflight_check(min_free_ram_mb=10**9, min_free_disk_gb=0.001)

    def test_aborts_on_impossible_disk_threshold(self):
        with pytest.raises(SystemExit):
            preflight_check(min_free_ram_mb=1, min_free_disk_gb=10**9)

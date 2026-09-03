"""tests/test_lumig_preflight_check_dataset_format.py — Unit tests for
scripts/lumig_preflight_check.py::check_dataset's format detection.

train_planner_sft.py/train_judge_lora.py consume a "messages" chat array;
train_expert_slm_pipeline.py consumes pre-formatted "text" instead. Both are
valid LUMI-G training dataset shapes, so check_dataset must detect either
rather than hard-requiring "messages" alone (the bug this test guards
against: a coder-expert "text" dataset used to be rejected outright).

The real `datasets` package is a LUMI-G-only training dependency not
installed in this environment -- consistent with the "avoid real external
dependencies in unit tests" rule, `datasets.load_dataset` is faked via
sys.modules injection rather than skipped.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


class _FakeDataset(list):
    pass


def _install_fake_datasets_module(monkeypatch, records):
    fake_module = types.ModuleType("datasets")

    def fake_load_dataset(fmt, data_files, split):
        assert fmt == "json"
        return _FakeDataset(records)

    fake_module.load_dataset = fake_load_dataset
    monkeypatch.setitem(sys.modules, "datasets", fake_module)


@pytest.fixture
def check_dataset(monkeypatch):
    # Imported lazily so the sys.modules fake is only needed per-test, not
    # at collection time (the module itself has no top-level heavy imports).
    import importlib
    import scripts.lumig_preflight_check as preflight
    importlib.reload(preflight)
    return preflight.check_dataset


class TestCheckDatasetFormatDetection:
    def test_accepts_messages_format(self, tmp_path, monkeypatch, check_dataset):
        _install_fake_datasets_module(monkeypatch, [{"messages": [{"role": "user", "content": "hi"}]}])
        f = tmp_path / "ds.jsonl"
        f.write_text('{"messages": []}\n')
        assert check_dataset(str(f)) is True

    def test_accepts_text_format(self, tmp_path, monkeypatch, check_dataset):
        _install_fake_datasets_module(monkeypatch, [{"text": "<|im_start|>system\n...<|im_end|>\n"}])
        f = tmp_path / "ds.jsonl"
        f.write_text('{"text": "..."}\n')
        assert check_dataset(str(f)) is True

    def test_rejects_record_missing_both_fields(self, tmp_path, monkeypatch, check_dataset):
        _install_fake_datasets_module(monkeypatch, [{"instruction": "x", "output": "y"}])
        f = tmp_path / "ds.jsonl"
        f.write_text('{"instruction": "x", "output": "y"}\n')
        assert check_dataset(str(f)) is False

    def test_missing_file_fails_without_raising(self, tmp_path, check_dataset):
        assert check_dataset(str(tmp_path / "nope.jsonl")) is False

    def test_empty_dataset_fails(self, tmp_path, monkeypatch, check_dataset):
        _install_fake_datasets_module(monkeypatch, [])
        f = tmp_path / "ds.jsonl"
        f.write_text("")
        assert check_dataset(str(f)) is False

"""Static safety and versioning checks for the TASK-50 live harness."""

from __future__ import annotations

import json
from pathlib import Path

from services.pipeline.contracts import detect_required_precision_intents


ROOT = Path(__file__).resolve().parents[1]


def test_precision_corpus_is_versioned_and_expectations_are_enforced():
    corpus = json.loads((ROOT / "tests/fixtures/precision_contract_corpus_v1.json").read_text())
    assert corpus["corpus_id"] == "moe-precision-v1"
    assert corpus["version"] == "1.0.0"
    assert {case["tool"] for case in corpus["positive"]} == {
        "decimal_finance", "exact_probability", "structured_validate", "time_facts",
    }
    for case in corpus["positive"]:
        intents = detect_required_precision_intents(case["prompt"])
        assert [intent.tool for intent in intents] == [case["tool"]]
    for case in corpus["negative"]:
        assert len(detect_required_precision_intents(case["prompt"])) == case["expected_intents"]


def test_precision_benchmark_has_mandatory_finally_cleanup_and_no_embedded_key():
    source = (ROOT / "scripts/benchmark_precision_rollout.py").read_text()
    assert "finally:" in source
    assert "revoke_api_key" in source
    assert "invalidate_api_key_redis" in source
    assert "archive_api_key" in source
    assert "moe-sk-" not in source
    assert "default=900.0" in source

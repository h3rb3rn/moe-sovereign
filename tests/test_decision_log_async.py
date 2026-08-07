"""Decision logging must not create orphaned Kafka coroutines."""

import gc
import warnings

from services import decision_log


def test_sync_decision_log_has_no_unawaited_coroutine(tmp_path, monkeypatch):
    monkeypatch.setattr(
        decision_log, "_FALLBACK_LOG_PATH", str(tmp_path / "decisions.jsonl")
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        decision_log.log_decision(
            decision_log.DecisionType.REPLAN,
            "req-sync",
            "test rationale",
        )
        gc.collect()

    assert not any("was never awaited" in str(item.message) for item in caught)
    assert (tmp_path / "decisions.jsonl").exists()

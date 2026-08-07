from services.request_snapshot import (
    clear_request_snapshot,
    consume_request_snapshot,
    update_request_snapshot,
)


def test_request_snapshot_merges_and_consumes_only_operational_fields():
    request_id = "snapshot-test"
    clear_request_snapshot(request_id)

    update_request_snapshot(
        request_id,
        complexity_level="complex",
        cynefin_domain="COMPLICATED",
        trust_verdict="",
    )
    update_request_snapshot(
        request_id,
        trust_score=0.8,
        trust_verdict="PROCEED",
    )

    assert consume_request_snapshot(request_id) == {
        "complexity_level": "complex",
        "cynefin_domain": "COMPLICATED",
        "trust_score": 0.8,
        "trust_verdict": "PROCEED",
    }
    assert consume_request_snapshot(request_id) == {}


def test_request_snapshot_clear_is_idempotent():
    request_id = "snapshot-clear-test"
    update_request_snapshot(request_id, complexity_level="moderate")

    clear_request_snapshot(request_id)
    clear_request_snapshot(request_id)

    assert consume_request_snapshot(request_id) == {}

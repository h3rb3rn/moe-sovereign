"""TASK-50 central rollout policy and low-cardinality telemetry."""

from __future__ import annotations

from services.pipeline.contracts import (
    apply_precision_contract_mode,
    build_precision_preflight,
)
from services import precision_telemetry


def _schemas(*tools: str) -> dict:
    return {
        tool: {
            "contract_hash": f"hash-{tool}",
            "required": [],
            "args": {},
        }
        for tool in tools
    }


def test_enforce_mode_keeps_new_contract_mandatory():
    query = "Berechne 19 Prozent von 119.99 EUR mit Scale 2 und Rundung half_up."
    detected = build_precision_preflight(query, _schemas("decimal_finance"))
    rollout = apply_precision_contract_mode(detected, "enforce")

    assert [item["tool"] for item in rollout["required_precision_intents"]] == ["decimal_finance"]
    assert rollout["precision_shadow_intents"] == []
    assert rollout["precision_contract_mode"] == "enforce"
    assert rollout["precision_contract_hash"]


def test_shadow_mode_observes_new_contract_but_preserves_baseline_enforcement():
    finance = build_precision_preflight(
        "Berechne 19 Prozent von 119.99 EUR mit Scale 2 und Rundung half_up.",
        _schemas("decimal_finance"),
    )
    gcd = build_precision_preflight(
        "Berechne den GGT von 391 und 299.", _schemas("gcd_lcm")
    )

    finance_rollout = apply_precision_contract_mode(finance, "shadow")
    gcd_rollout = apply_precision_contract_mode(gcd, "shadow")

    assert finance_rollout["required_precision_intents"] == []
    assert finance_rollout["precision_shadow_intents"][0]["tool"] == "decimal_finance"
    assert finance_rollout["precision_contract_hash"] == ""
    assert gcd_rollout["required_precision_intents"][0]["tool"] == "gcd_lcm"
    assert gcd_rollout["precision_shadow_intents"] == []


def test_invalid_rollout_mode_fails_safe_to_shadow():
    detected = build_precision_preflight(
        "Berechne die Kombination C(10,3) exakt.", _schemas("exact_probability")
    )
    rollout = apply_precision_contract_mode(detected, "invalid")
    assert rollout["precision_contract_mode"] == "shadow"
    assert rollout["required_precision_intents"] == []


def test_precision_metric_labels_are_allowlisted(monkeypatch):
    captured = {}

    class Counter:
        def labels(self, **labels):
            captured.update(labels)
            return self

        def inc(self):
            captured["incremented"] = True

    monkeypatch.setattr(precision_telemetry, "PROM_PRECISION_EVENTS", Counter())
    precision_telemetry.record_precision_event(
        "user-controlled-stage", "secret-outcome",
        tool="prompt-with-sensitive-data", mode="invalid",
    )

    assert captured == {
        "contract": "none", "stage": "contract", "outcome": "failed",
        "mode": "shadow", "incremented": True,
    }

"""Unit tests for the two-tier escalation decision (graph.expert).

Verifies the cost-aware escalation rules deterministically — without depending on
a live LLM's (non-deterministic) self-reported confidence:

  • normal task      → escalate on anything below 'high'
  • cost-tier trivial → escalate only on a low/empty T1 answer; 'medium' keeps the
    cost saving (the TRIVIAL_LOW_CONF_RESCUE behaviour)
"""
import pytest

from graph.expert import _tier2_escalation_decision as decide


# (cost_tier_t1, t1_confs, has_tier2) -> expected decision
_CASES = [
    # normal routing: below-high escalates
    (False, ["high"],   True,  "t1_high_skip"),
    (False, ["medium"], True,  "t2_escalated"),
    (False, ["low"],    True,  "t2_escalated"),
    (False, [],         True,  "t2_escalated"),   # unparseable/empty
    # trivial cost-tier: only low/empty rescues; medium keeps the saving (t1_cost_kept)
    (True,  ["high"],   True,  "t1_high_skip"),
    (True,  ["medium"], True,  "t1_cost_kept"),     # kept — deliberate cost saving
    (True,  ["low"],    True,  "t2_escalated"),     # rescue
    (True,  [],         True,  "t2_escalated"),     # garbage → rescue
    # no T2 configured: never escalate regardless of confidence
    (False, ["low"],    False, "t1_only"),
    (True,  ["low"],    False, "t1_only"),
    (False, ["high"],   False, "t1_only"),
    # mixed T1 ensemble: any high wins; any low (without high) triggers weakness
    (False, ["high", "low"],   True, "t1_high_skip"),
    (True,  ["medium", "low"], True, "t2_escalated"),
    (True,  ["medium", "high"], True, "t1_high_skip"),
]


@pytest.mark.parametrize("cost_tier,confs,has_t2,expected", _CASES)
def test_tier2_escalation_decision(cost_tier, confs, has_t2, expected):
    assert _tier2_escalation_decision_label(cost_tier, confs, has_t2) == expected


def _tier2_escalation_decision_label(cost_tier, confs, has_t2):
    return decide(cost_tier, confs, has_t2)


def test_trivial_medium_keeps_cost_saving():
    """The core TRIVIAL_LOW_CONF_RESCUE contract: medium T1 on a trivial task is
    good enough — no expensive T2 escalation. Reported as a distinct decision so
    it is not conflated with the no-T2-available case."""
    assert decide(True, ["medium"], True) == "t1_cost_kept"


def test_trivial_low_triggers_rescue():
    """A low-confidence T1 answer on a trivial task escalates to T2 (the rescue)."""
    assert decide(True, ["low"], True) == "t2_escalated"

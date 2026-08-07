from __future__ import annotations

import pytest

from admin_ui.deliberation_policy import (
    legacy_deliberation_policy as admin_legacy_policy,
    validate_deliberation_policy as validate_admin_policy,
)
from services.deliberation.contracts import (
    legacy_deliberation_policy,
    parse_deliberation_policy,
)


def test_admin_and_runtime_policy_defaults_stay_identical():
    assert validate_admin_policy(None) == parse_deliberation_policy(None).model_dump(mode="json")
    assert admin_legacy_policy() == legacy_deliberation_policy(True).model_dump(mode="json")


def test_admin_and_runtime_normalize_same_explicit_policy():
    raw = {
        "activation": "adaptive",
        "mode": "moderated",
        "initial_agent_cap": 4,
        "reserve_agents": 1,
        "absolute_max_agents": 5,
        "initial_round_cap": 2,
        "reserve_rounds": 1,
        "absolute_max_rounds": 3,
        "max_model_calls": 20,
        "max_turn_tokens": 640,
    }
    assert validate_admin_policy(raw) == parse_deliberation_policy(raw).model_dump(mode="json")


@pytest.mark.parametrize(
    "raw",
    [
        {"activation": "sometimes"},
        {"activation": "adaptive", "unknown": True},
        {"min_agents": 5, "initial_agent_cap": 4},
        {"max_turn_tokens": 64},
    ],
)
def test_admin_and_runtime_both_reject_invalid_policy(raw):
    with pytest.raises(ValueError):
        validate_admin_policy(raw)
    with pytest.raises(ValueError):
        parse_deliberation_policy(raw)

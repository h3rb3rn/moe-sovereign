"""Admin-side validation mirror for the versioned deliberation policy.

The admin image has an isolated Docker build context and cannot import the
orchestrator's ``services`` package. Contract-parity tests in the repository
therefore verify this boundary model against the runtime model.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class AdminDeliberationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = "1.0"
    activation: Literal["disabled", "adaptive", "required"] = "disabled"
    mode: Literal["micro", "moderated", "auto"] = "auto"
    min_agents: int = Field(default=2, ge=2, le=12)
    initial_agent_cap: int = Field(default=6, ge=2, le=12)
    reserve_agents: int = Field(default=2, ge=0, le=6)
    absolute_max_agents: int = Field(default=8, ge=2, le=12)
    min_rounds: int = Field(default=1, ge=1, le=8)
    initial_round_cap: int = Field(default=3, ge=1, le=8)
    reserve_rounds: int = Field(default=2, ge=0, le=4)
    absolute_max_rounds: int = Field(default=5, ge=1, le=8)
    max_model_calls: int = Field(default=18, ge=1, le=96)
    max_turn_tokens: int = Field(default=768, ge=128, le=4096)
    moderator_interval: int = Field(default=1, ge=1, le=8)
    estimated_turn_seconds: float = Field(default=20.0, ge=0.1, le=900.0)
    synthesis_reserve_seconds: float = Field(default=30.0, ge=0.0, le=1800.0)
    convergence_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    repetition_threshold: float = Field(default=0.78, ge=0.0, le=1.0)
    fallback: Literal["standard", "fail"] = "standard"

    @model_validator(mode="after")
    def _validate_bounds(self) -> "AdminDeliberationPolicy":
        if self.initial_agent_cap < self.min_agents:
            raise ValueError("initial_agent_cap must be >= min_agents")
        if self.absolute_max_agents < self.initial_agent_cap:
            raise ValueError("absolute_max_agents must be >= initial_agent_cap")
        if self.initial_round_cap < self.min_rounds:
            raise ValueError("initial_round_cap must be >= min_rounds")
        if self.absolute_max_rounds < self.initial_round_cap:
            raise ValueError("absolute_max_rounds must be >= initial_round_cap")
        if self.mode == "micro" and self.max_model_calls < 3:
            raise ValueError("micro deliberation requires at least three model calls")
        return self


def validate_deliberation_policy(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    candidate: Mapping[str, Any] = raw if raw is not None else {"activation": "disabled"}
    if not isinstance(candidate, Mapping):
        raise ValueError("deliberation_policy must be an object")
    try:
        return AdminDeliberationPolicy.model_validate(dict(candidate)).model_dump(mode="json")
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_input=False)
        )
        raise ValueError(f"invalid deliberation_policy: {details}") from exc


def legacy_deliberation_policy() -> dict[str, Any]:
    return AdminDeliberationPolicy(
        activation="required",
        mode="micro",
        min_agents=2,
        initial_agent_cap=2,
        reserve_agents=0,
        absolute_max_agents=2,
        min_rounds=1,
        initial_round_cap=1,
        reserve_rounds=0,
        absolute_max_rounds=1,
        max_model_calls=3,
    ).model_dump(mode="json")

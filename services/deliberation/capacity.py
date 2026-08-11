"""Deterministic adaptive agent and round capacity planning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.deliberation.contracts import DeliberationPolicy


_NON_DOMAIN_CATEGORIES = {"", "general", "precision_tools", "research", "math"}


@dataclass(frozen=True)
class CapacityInputs:
    """Validated runtime signals used to freeze a deliberation budget."""

    complexity_level: str = "trivial"
    cynefin_domain: str = "CLEAR"
    trust_verdict: str = ""
    plan: Sequence[Mapping[str, Any]] = ()
    remaining_seconds: float | None = None
    available_models: int = 0


@dataclass(frozen=True)
class DeliberationCapacity:
    active: bool
    selected_mode: str
    initial_agents: int
    reserve_agents: int
    initial_rounds: int
    reserve_rounds: int
    max_agents: int
    max_rounds: int
    model_call_budget: int
    domain_count: int
    task_count: int
    dependency_depth: int
    distinct_models_available: int
    activation_reason: str
    budget_limited: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "selected_mode": self.selected_mode,
            "initial_agents": self.initial_agents,
            "reserve_agents": self.reserve_agents,
            "initial_rounds": self.initial_rounds,
            "reserve_rounds": self.reserve_rounds,
            "max_agents": self.max_agents,
            "max_rounds": self.max_rounds,
            "model_call_budget": self.model_call_budget,
            "domain_count": self.domain_count,
            "task_count": self.task_count,
            "dependency_depth": self.dependency_depth,
            "distinct_models_available": self.distinct_models_available,
            "activation_reason": self.activation_reason,
            "budget_limited": self.budget_limited,
        }


def _dependency_depth(plan: Sequence[Mapping[str, Any]]) -> int:
    """Longest dependency chain in a plan (1 = no resolvable dependencies).

    Dependencies are resolved against explicit ``id`` values first and, failing
    that, against another task's ``task`` description by prefix. Both forms are
    required because the planner's own trained contract documents ``depends_on``
    as ``"<prior task description prefix>"`` while ``id`` is only an optional
    extra. Measured over 2,000 real training samples: 15% of plans emit
    ``depends_on`` but only 3% emit ``id`` — so resolving ids alone left this
    function returning 1 for virtually every production plan.

    The consequence was round scaling, not activation: the ``dependency_depth
    >= 2`` disjunct in adaptive activation below is redundant anyway (a plan
    deep enough to reach 2 necessarily has ``task_count >= 2``, which already
    fires). What a pinned depth of 1 actually did was hold ``desired_rounds`` at
    its floor, so a three-stage dependent plan deliberated for exactly as many
    rounds as a flat one.

    Prefix resolution is deliberately strict: a candidate must be unique. An
    ambiguous prefix contributes no edge rather than inventing one, so an
    under-specified plan cannot inflate its way into deliberation.
    """

    tasks = [task for task in plan if isinstance(task, Mapping)]
    # Positional keys so every task is a graph node, not just those with an id.
    keys = [str(index) for index in range(len(tasks))]
    by_key = dict(zip(keys, tasks))

    by_id: dict[str, str] = {}
    descriptions: list[tuple[str, str]] = []
    for key, task in by_key.items():
        task_id = str(task.get("id") or "").strip()
        if task_id:
            by_id.setdefault(task_id, key)
        description = str(task.get("task") or "").strip()
        if description:
            descriptions.append((key, description.casefold()))

    def resolve(raw_dep: str, self_key: str) -> str | None:
        dep = raw_dep.strip()
        if not dep:
            return None
        target = by_id.get(dep)
        if target is not None and target != self_key:
            return target
        needle = dep.casefold()
        matches = [
            key
            for key, description in descriptions
            if key != self_key and description.startswith(needle)
        ]
        return matches[0] if len(matches) == 1 else None

    memo: dict[str, int] = {}

    def depth(key: str, visiting: frozenset[str]) -> int:
        if key in memo:
            return memo[key]
        if key in visiting:
            return 1
        raw_deps = by_key[key].get("depends_on") or []
        if isinstance(raw_deps, str):
            raw_deps = [raw_deps]
        resolved = [
            target
            for target in (resolve(str(dep), key) for dep in raw_deps)
            if target is not None
        ]
        value = 1 + max(
            (depth(target, visiting | {key}) for target in resolved),
            default=0,
        )
        memo[key] = value
        return value

    return max((depth(key, frozenset()) for key in keys), default=1)


def _empty_capacity(
    *,
    mode: str,
    reason: str,
    domains: int,
    tasks: int,
    depth: int,
    available_models: int,
    model_call_budget: int,
    budget_limited: bool = False,
) -> DeliberationCapacity:
    return DeliberationCapacity(
        active=False,
        selected_mode=mode,
        initial_agents=0,
        reserve_agents=0,
        initial_rounds=0,
        reserve_rounds=0,
        max_agents=0,
        max_rounds=0,
        model_call_budget=model_call_budget,
        domain_count=domains,
        task_count=tasks,
        dependency_depth=depth,
        distinct_models_available=max(0, available_models),
        activation_reason=reason,
        budget_limited=budget_limited,
    )


def plan_deliberation_capacity(
    policy: DeliberationPolicy,
    signals: CapacityInputs,
) -> DeliberationCapacity:
    """Freeze adaptive capacity without calling a model or external service."""

    complexity = (signals.complexity_level or "trivial").strip().lower()
    cynefin = (signals.cynefin_domain or "CLEAR").strip().upper()
    trust = (signals.trust_verdict or "").strip().upper()
    plan = [task for task in signals.plan if isinstance(task, Mapping)]
    task_count = len(plan)
    domains = {
        str(task.get("category") or "").strip()
        for task in plan
        if str(task.get("category") or "").strip() not in _NON_DOMAIN_CATEGORIES
    }
    domain_count = len(domains)
    dependency_depth = _dependency_depth(plan)

    selected_mode = policy.mode
    if selected_mode == "auto":
        selected_mode = (
            "moderated"
            if cynefin == "COMPLEX" or complexity == "complex" or domain_count >= 3
            else "micro"
        )

    if policy.activation == "disabled":
        return _empty_capacity(
            mode=selected_mode,
            reason="template_disabled",
            domains=domain_count,
            tasks=task_count,
            depth=dependency_depth,
            available_models=signals.available_models,
            model_call_budget=policy.max_model_calls,
        )
    if trust == "BLOCK" or cynefin == "CHAOTIC":
        return _empty_capacity(
            mode=selected_mode,
            reason="trust_blocked",
            domains=domain_count,
            tasks=task_count,
            depth=dependency_depth,
            available_models=signals.available_models,
            model_call_budget=policy.max_model_calls,
        )

    if policy.activation == "adaptive":
        adaptive_match = (
            complexity == "complex"
            or cynefin == "COMPLEX"
            or (
                cynefin == "COMPLICATED"
                and (domain_count >= 2 or task_count >= 2 or dependency_depth >= 2)
            )
        )
        if not adaptive_match:
            return _empty_capacity(
                mode=selected_mode,
                reason="complexity_below_threshold",
                domains=domain_count,
                tasks=task_count,
                depth=dependency_depth,
                available_models=signals.available_models,
                model_call_budget=policy.max_model_calls,
            )

    model_call_budget = policy.max_model_calls
    if signals.remaining_seconds is not None:
        usable = max(0.0, float(signals.remaining_seconds) - policy.synthesis_reserve_seconds)
        time_call_budget = int(usable // policy.estimated_turn_seconds)
        model_call_budget = min(model_call_budget, time_call_budget)

    if selected_mode == "micro":
        if model_call_budget < 3:
            return _empty_capacity(
                mode="micro",
                reason="insufficient_budget",
                domains=domain_count,
                tasks=task_count,
                depth=dependency_depth,
                available_models=signals.available_models,
                model_call_budget=model_call_budget,
                budget_limited=True,
            )
        return DeliberationCapacity(
            active=True,
            selected_mode="micro",
            initial_agents=2,
            reserve_agents=0,
            initial_rounds=1,
            reserve_rounds=0,
            max_agents=2,
            max_rounds=1,
            # The frozen budget is request-wide. Each micro debate reserves
            # three calls at execution time, so a multi-task plan may debate
            # more than one task only when the template/time budget permits it.
            model_call_budget=model_call_budget,
            domain_count=domain_count,
            task_count=task_count,
            dependency_depth=dependency_depth,
            distinct_models_available=max(0, signals.available_models),
            activation_reason=(
                "template_required"
                if policy.activation == "required"
                else "adaptive_complexity"
            ),
        )

    if cynefin == "COMPLEX" or complexity == "complex":
        desired_agents = max(4, domain_count + 2, min(6, task_count + 2))
        desired_rounds = max(3, min(4, dependency_depth + 1))
        desired_agent_reserve = policy.reserve_agents
        desired_round_reserve = policy.reserve_rounds
    elif cynefin == "COMPLICATED" or complexity == "moderate":
        desired_agents = max(3, domain_count + 1)
        desired_rounds = max(2, min(3, dependency_depth + 1))
        desired_agent_reserve = min(policy.reserve_agents, 1)
        desired_round_reserve = min(policy.reserve_rounds, 1)
    else:
        desired_agents = 2
        desired_rounds = 1
        desired_agent_reserve = min(policy.reserve_agents, 1)
        desired_round_reserve = min(policy.reserve_rounds, 1)

    initial_agents = min(
        policy.initial_agent_cap,
        policy.absolute_max_agents,
        max(policy.min_agents, desired_agents),
    )
    initial_rounds = min(
        policy.initial_round_cap,
        policy.absolute_max_rounds,
        max(policy.min_rounds, desired_rounds),
    )

    def calls_for(agents: int, rounds: int) -> int:
        moderator_calls = math.ceil(rounds / policy.moderator_interval)
        return agents * rounds + moderator_calls

    budget_limited = False
    while initial_rounds > policy.min_rounds and calls_for(initial_agents, initial_rounds) > model_call_budget:
        initial_rounds -= 1
        budget_limited = True
    while initial_agents > policy.min_agents and calls_for(initial_agents, initial_rounds) > model_call_budget:
        initial_agents -= 1
        budget_limited = True

    base_calls = calls_for(initial_agents, initial_rounds)
    if base_calls > model_call_budget:
        return _empty_capacity(
            mode="moderated",
            reason="insufficient_budget",
            domains=domain_count,
            tasks=task_count,
            depth=dependency_depth,
            available_models=signals.available_models,
            model_call_budget=model_call_budget,
            budget_limited=True,
        )

    remaining_calls = model_call_budget - base_calls
    reserve_agents = min(
        desired_agent_reserve,
        policy.absolute_max_agents - initial_agents,
        remaining_calls,
    )
    remaining_calls -= reserve_agents

    reserve_rounds = 0
    max_round_reserve = min(
        desired_round_reserve,
        policy.absolute_max_rounds - initial_rounds,
    )
    per_reserve_round = initial_agents + reserve_agents + 1
    while reserve_rounds < max_round_reserve and remaining_calls >= per_reserve_round:
        reserve_rounds += 1
        remaining_calls -= per_reserve_round

    if reserve_agents < desired_agent_reserve or reserve_rounds < desired_round_reserve:
        budget_limited = True

    return DeliberationCapacity(
        active=True,
        selected_mode="moderated",
        initial_agents=initial_agents,
        reserve_agents=reserve_agents,
        initial_rounds=initial_rounds,
        reserve_rounds=reserve_rounds,
        max_agents=initial_agents + reserve_agents,
        max_rounds=initial_rounds + reserve_rounds,
        model_call_budget=model_call_budget,
        domain_count=domain_count,
        task_count=task_count,
        dependency_depth=dependency_depth,
        distinct_models_available=max(0, signals.available_models),
        activation_reason=(
            "template_required"
            if policy.activation == "required"
            else "adaptive_complexity"
        ),
        budget_limited=budget_limited,
    )

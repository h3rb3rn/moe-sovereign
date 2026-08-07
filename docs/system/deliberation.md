# Adaptive Deliberation

Status: **implemented in the repository; deployment-dependent**. The feature is
available after the orchestrator and Admin UI images containing this version
have been built and deployed. It does not update model weights.

Adaptive deliberation is a bounded execution policy inside the existing
compound-AI pipeline. It is not a second orchestrator and it does not replace
authentication, expert routing, retrieval, precision tools, synthesis or the
quality gate.

## Activation

Every new or updated Expert Template stores a strict, versioned
`deliberation_policy`. The Admin and User Portal template editors expose three
activation choices:

| Activation | Runtime behavior |
|---|---|
| `disabled` | Always use the standard expert-worker path. This is the default for new templates. |
| `adaptive` | Use deterministic complexity, Cynefin, plan and budget signals to decide whether deliberation is useful. |
| `required` | Request deliberation whenever trust, safety and hard resource limits permit it. |

The execution mode can be `micro`, `moderated`, or `auto`:

- `micro` runs the existing proponent, skeptic and rebuttal sequence for a
  planner task. Each sequence reserves exactly three calls from one shared
  request-wide budget.
- `moderated` creates deterministic logical roles across the request's planned
  domains, runs bounded rounds, and asks the configured judge for a strict JSON
  convergence decision.
- `auto` selects moderated deliberation for complex or sufficiently
  multi-domain work and micro deliberation for smaller eligible work.

Templates that predate policy schema `1.0` retain the legacy micro-debate
behavior controlled by `JMOE_DEBATE_ENABLED`. Exporting or copying such a
template materializes that compatibility policy explicitly. This prevents a
round trip through import/export from silently changing its behavior.

## Deterministic capacity planning

The planner LLM does not choose the agent or round count. After the execution
plan has been frozen, the orchestrator calculates capacity from:

- request complexity and Cynefin domain;
- planned task count, specialist-domain count and dependency depth;
- the number of distinct configured models;
- the template's initial, reserve and absolute caps;
- the remaining request deadline after preserving a synthesis reserve; and
- the template's hard model-call budget.

Initial agents and rounds are allocated separately from reserve agents and
rounds. Reserve capacity is activated only for failed turns, a moderator
correction, an unresolved conflict or a missing perspective. When a time or
call budget is tight, additional rounds are removed before minimum perspective
coverage. If even the minimum execution cannot fit, the workflow records an
explicit `insufficient_budget` result and follows the configured fallback.

`CHAOTIC` requests and a Trust verdict of `BLOCK` never add autonomous debate
calls. The downstream response and quality-gate policy still determines
whether the standard result can be released.

## Policy contract

The current `1.0` contract rejects unknown fields and invalid types:

```json
{
  "schema_version": "1.0",
  "activation": "adaptive",
  "mode": "auto",
  "min_agents": 2,
  "initial_agent_cap": 6,
  "reserve_agents": 2,
  "absolute_max_agents": 8,
  "min_rounds": 1,
  "initial_round_cap": 3,
  "reserve_rounds": 2,
  "absolute_max_rounds": 5,
  "max_model_calls": 18,
  "max_turn_tokens": 768,
  "moderator_interval": 1,
  "estimated_turn_seconds": 20.0,
  "synthesis_reserve_seconds": 30.0,
  "convergence_threshold": 0.82,
  "repetition_threshold": 0.78,
  "fallback": "standard"
}
```

`fallback: standard` returns to the normal expert path if a debate cannot be
started. `fallback: fail` stops a required deliberation instead of silently
pretending that it ran. Invalid persisted policies are rejected before graph
execution by the OpenAI Chat, OpenAI Responses, Ollama and Anthropic API
facades.

`max_turn_tokens` is a deliberation-only output ceiling. It bounds every
proponent, skeptic, rebuttal and moderated-role turn independently of the
larger request output budget, while leaving the final synthesis enough room
for a complete answer. This prevents one verbose role from consuming the
request deadline before the remaining perspectives can run.

## `moe-auto`

Generated `moe-auto` templates receive a policy snapshot outside the planner
LLM. The default is configured with:

```dotenv
MOE_AUTO_DELIBERATION_ACTIVATION=adaptive
```

An administrator can grant `moe-auto:no-deliberation` or
`moe-auto:deliberation-required` to override that setting for a user's API
keys. If both are granted, `no-deliberation` wins. The dynamic-template cache
is not allowed to reuse a stale activation value: the policy is overwritten
for the current request after a cache hit.

The current planner model therefore does **not** need retraining to use this
release. Retraining is only useful later if the planner contract is extended
to emit new deliberation-specific evidence or dependency signals; it is not a
prerequisite for activation.

## Runtime safety and observability

- All turns share the request deadline and a hard model-call counter.
- A failed or schema-invalid moderator call consumes capacity before a single
  repair attempt is considered.
- Client cancellation propagates to the active graph task; deliberation does
  not continue in the background.
- Peer turns are labelled as untrusted content in subsequent prompts.
- Transcript compaction, repetition detection and convergence checks bound the
  loop.
- Request history records whether deliberation was active, selected mode,
  activation reason, calls, stop reason and reserve usage. Prompts, model
  outputs, credentials and full transcripts are not copied into this summary.

The Admin **Live Monitoring → Process History** table shows the selected mode
and deliberation call count for completed requests.

## Current limitations

- Deliberation is request-scoped, not an interactive long-lived debate room.
- Role selection is deterministic and automatic; there is no manual role
  editor in policy version `1.0`.
- If fewer distinct models than logical roles are configured, one model can
  fill multiple roles. The request records `model_diversity_degraded`.
- Moderated turns use the validated request plan and any context already in
  state. Retrieval and research branches continue to feed final synthesis;
  they are not paused to inject newly fetched evidence into every debate turn.
- A repository implementation is not evidence of a successful production
  rollout. Container rebuild, deployment and a live model benchmark remain
  separate operational steps.

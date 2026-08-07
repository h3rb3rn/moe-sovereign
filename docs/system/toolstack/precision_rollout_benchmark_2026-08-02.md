# Precision Rollout Benchmark — 2026-08-02

Owner: Platform Engineering  
Corpus: `moe-precision-v1` / 1.0.0  
Deployment: local MoE Sovereign instance on `fix/codex-responses-template-routing`  
Status: Validated system test; not a public performance claim

## Method

The versioned source corpus is
`tests/fixtures/precision_contract_corpus_v1.json`; the reusable runner is
`scripts/benchmark_precision_rollout.py`. Requests used temperature 0,
`no_cache=true` on the MoE facade and a 900-second timeout, three times the
normal cold-start allowance. A temporary API key bound to the active
`horndev` user and private template
`moe-n04-rtx-qwen3.6:35b-256k` was created in memory and revoked, invalidated
and archived in `finally` after each run.

The native baseline called the configured N04-RTX OpenAI-compatible backend
directly with `qwen3.6:35b`. This is necessary because the public MoE facade
now correctly intercepts mandatory precision intent even when a caller asks
for a native model. `/api/ps` returned an empty model list immediately before
the first native request, so the first observation is a measured cold start.
No model was forcibly unloaded. The second request is the warm observation.

## Native versus evidence-bound direct execution

Identical prompt:

> Berechne 19 Prozent von 119.99 EUR mit Scale 2 und Rundung half_up.

| Path | Condition | HTTP | Latency | Tokens | Checked result |
|---|---|---:|---:|---:|---|
| Native `qwen3.6:35b` | observed cold | 200 | 151.918 s | 880 | `22.80 EUR` present |
| Native `qwen3.6:35b` | warm | 200 | 21.323 s | 880 | `22.80 EUR` present |
| Expert-template request, deterministic direct route | first observation | 200 | 0.174 s | 0 | `22.80 EUR`, `half_up` |
| Expert-template request, deterministic direct route | warm | 200 | 0.163 s | 0 | `22.80 EUR`, `half_up` |

Both paths produced the expected numeric value in this one prompt. The native
response did not expose the requested scale/rounding evidence contract; the
orchestrated response was rendered from typed MCP evidence and explicitly
included both. Token usage reported by the native backend includes hidden
reasoning despite the very short visible output.

These four observations have sample size one prompt and two executions per
path. They demonstrate the expected architectural difference for a supported
input-only contract; they do not establish general latency, accuracy, cost or
model-quality superiority.

## Cross-facade API matrix

Four pure prompts covered Decimal finance, exact binomial probability,
structured JSON validation and IANA time facts. Each was sent through Chat,
Responses and Anthropic, producing 12/12 HTTP 200 results with all expected
facts and zero model tokens. The successful post-fix run had p50 0.285 s,
linearly interpolated p95 approximately 0.444 s, and maximum 0.532 s.

Representative IDs:

- Decimal: `chatcmpl-f42df3fc-5a88-4355-ae15-01696e378d5a`,
  `resp_20c09c7c49df4a9bb334b43853d7201d`,
  `msg_596f20aff9c0479da8b1862e`
- Probability: `chatcmpl-685a9e49-6d55-4b20-b617-2159f6ee398c`,
  `resp_818270bfeb8b4d1eaeddd233756d1538`,
  `msg_24ce974388a644d1807bba97`
- Structured JSON: `chatcmpl-a577ae92-126b-4e27-a732-75f6991ec823`,
  `resp_91f79ec404b6443db1ed4878399090eb`,
  `msg_75291b05542040f7b0a78ea5`

The mixed request combined Decimal finance, binomial probability and a SQL
injection review. Final request
`chatcmpl-fca4acb9-1512-4aec-b353-d28ddaf76e70` passed in 201.555 seconds with
15,028 prompt and 471 completion tokens. Its trace proves:

```text
precision_preflight(required: decimal_finance, exact_probability)
  -> cache(bypassed)
  -> planner(2 precision tasks + 1 code_reviewer)
  -> MCP(2 completed) + expert(1 completed)
  -> precision_slots(2 prepared)
  -> precision_hybrid(one scoped expert)
  -> critic(confirmed scoped body)
  -> precision_bind(bound)
  -> quality_gate(passed)
  -> response_commit(skipped because no_cache=true)
```

## Defects found by the rollout

1. The first structured-validation envelope echoed sensitive payload data in
   `input_normalized` and in operational tool logs. A contract-level evidence
   policy now replaces `payload` and `schema_json` with SHA-256/byte-count
   records at the server response and telemetry boundaries. The live malicious
   matrix confirmed no test secret was echoed.
2. The first mixed benchmark returned HTTP 500 because AdviceTaker injected a
   legacy `calculate` task with no required `expression` after deterministic
   recovery had already built the correct two typed tasks. Advice rules now
   skip MCP injection unless every discovered required argument is extracted.
   The full repeated matrix then passed.

## Security and adversarial matrix

- Binary-float Decimal input and oversized combinatorics fail input schema.
- Division by zero and cost/iteration bounds return typed tool failures.
- YAML aliases/anchors/tags, XML DTD/entities/XInclude and remote JSON Schema
  references return invalid results without resolution or execution.
- CSV requires an explicit dialect and reports formula prefixes as bounded
  warnings; no formula is executed.
- Temporary key audit after all runs: zero active and zero unarchived records.

## Deployment and rollback proof

- Active MCP image:
  `sha256:7e28eeab4a5b05e56eb713cfbab834a6c9dc4ebfea9ae3594eb0f46c77c5564a`
- Active orchestrator image:
  `sha256:4320ca67eaaeaf5168d4c4c251427f99305e04bfbdbf1e60e3b4368f2e8d402f`
- Both containers: healthy, RestartCount 0; `/ready` passed every critical
  check; `pip check` passed in both images.
- Flag rollback was exercised as
  `shadow / direct=false / structured=false / cache=bypass`, healthy with
  RestartCount 0, before restoring
  `enforce / direct=true / structured=true / cache=bypass`.
- Image rollback to
  `sha256:8c90f1e3654c525ad3f41fffb237acaa3d37322c2e866ee521e14239315d54c8`
  was healthy/restart-0; the final image was then restored and smoke-tested.

The known Authentik Compose-variable warnings were unchanged and are not part
of this precision release.

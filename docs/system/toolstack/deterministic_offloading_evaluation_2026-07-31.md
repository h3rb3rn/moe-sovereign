# Deterministic MCP Offloading Evaluation

Owner: Platform Engineering  
Version: 2.0  
Last verified: 2026-08-02  
Scope: local source revision on `fix/codex-responses-template-routing`

## Executive result

MoE Sovereign has typed, fail-closed deterministic coverage for explicit
calendar, time-zone, Decimal-finance, exact-probability, unit and GCD
requests. Safe structured JSON/YAML/XML/CSV validation is also available.
TASK-42 through TASK-50 now bind recognized intent, active contract,
normalized input, execution evidence and final output across every API
facade. Pure covered prompts bypass model nodes; mixed prompts preserve typed
facts while a bounded expert handles only the non-deterministic part.

The remaining risk is breadth, not activation of these P0 contracts. Only
requests with unambiguous typed extractors are intercepted. Business
calendars, version comparison, identifier validation, advanced statistics,
geospatial calculations and model-specific token counting remain candidates;
none should be added to enforcement before its arguments, limits, provenance
and negative cases are specified.

MCP is also a transport, not a truth guarantee. The server contains both pure
local computations and connectors to mutable external sources. Pure
computations can be reproducible from input alone. Current facts require an
authoritative source, provenance, source version or retrieval timestamp and a
freshness policy. They must never be labelled deterministic merely because
they were returned by an MCP endpoint.

## Method and evidence

This evaluation inspected:

- the source registry, descriptions and access classifications in
  `mcp_server/server.py`;
- the runtime catalogue contract exposed by `GET /tools`;
- planner injection in `main.py` and `graph/planner.py`;
- expert-template fallback lists in `services/dynamic_router.py`;
- fail-closed precision-plan validation and deterministic recovery in
  `services/pipeline/contracts.py`;
- result validation in `graph/tool_nodes.py`;
- focused direct-function and planner-contract tests.

The current source contains 68 REST-invokable registry entries. Of these, 64
are included in the `/tools` catalogue. Four operational functions
(`node_status`, `active_requests`, `mission_context_get`, `watchdog_alerts`)
are registered and access-classified but have no `_TOOL_DESCRIPTIONS` entry;
because `/tools` iterates the description map, they are not discoverable by
the orchestrator catalogue loader. Their intended visibility should be made
explicit instead of relying on this incidental dictionary difference.

Status terms follow `PROJECT_COMPLIANCE.md`: **Implemented** means reachable
in source with a focused contract test; **Validated** additionally requires a
live end-to-end invocation in the stated deployment.

## Calendar contract implemented in TASK-40

`calendar_facts(date_str, locale="de")` accepts only an absolute ISO date and
the explicit locale `de` or `en`. It returns stable JSON containing:

- canonical date and proleptic-Gregorian calendar identifier;
- localized weekday and month names;
- ISO weekday, ISO week and the separate ISO week-year;
- day of year, month length and year length;
- quarter, leap-year status and weekend status.

Relative values such as `today`, `tomorrow` or `next Friday` are rejected.
They require an explicit clock instant and IANA time zone to be reproducible.
The legacy `day_of_week` tool remains registered for compatibility and uses
the same internal calendar calculation.

TASK-40 also corrects two adjacent precision defects:

1. `date_diff` no longer approximates years as 365 days and months as 30
   days. It reports exact total days plus a real calendar delta from
   `dateutil.relativedelta`.
2. MCP results beginning with `Error:`, `Fehler:`, a bracketed error marker or
   a JSON `error` field are rejected before they can enter working memory as
   successful precision evidence.

## Validation evidence

**Validated locally on 2026-07-31 UTC.** This is deployment evidence for the
current dirty development checkout, not a release or production-readiness
claim.

- Governance validation passed for 27 required files and nine declared
  runtime entry points; `mkdocs build --strict` completed successfully.
- The complete local regression passed: 737 tests in 4.67 seconds.
- MCP image
  `sha256:94e99b8f7480c353631f28594cc294c1c219a364e858f91615fdf906f19360e6`
  and orchestrator image
  `sha256:3ea4d1822c2857cd9b74b82bbfbb9a9878e049ed9f406bbd871eec222c65946f`
  ran healthy with restart count zero after final recreation.
- Live `GET /tools` returned 59 catalogued schemas. `calendar_facts` exposed
  required `date_str`, optional `locale="de"` and `access_kind="read"`.
- Live `/invoke` checks covered German leap day `2024-02-29`, the ISO
  week-year boundary `2021-01-01` (week 53 of ISO year 2020), invalid date,
  invalid locale, legacy `day_of_week` and exact calendar `date_diff`.
- The running orchestrator loaded all 59 schemas. Container-side contract
  checks proved German and English weekday repair plus empty-plan recovery
  dispatch `calendar_facts` with normalized ISO dates and locale.

The first MCP rebuild also provided a failure-path check: the unbounded
`mcp[cli]>=1.0.0` requirement selected MCP 2.0, which removed
`mcp.server.fastmcp`. The final image uses an exact transitive dependency lock
with the proven MCP 1.28.1 API and a digest-pinned Python base; `pip check`
passed. Authentik variable warnings emitted by Compose remain a pre-existing,
unrelated deployment gap.

## Precision-intent enforcement implemented in TASK-41

The planner handoff now derives mandatory precision intents independently of
the planner's category choice. Before any plan is dispatched, it compares the
recognized input operation with the active MCP catalogue and requires a
matching `precision_tools` task, tool name and semantic arguments. Missing or
altered work produces a structured `precision_intent_downgraded` error. A
missing active tool produces `required_precision_tool_unavailable`. Both use
the existing single bounded planner-repair attempt and fail closed after
recovery is exhausted; they never fall back to a general expert.

This invariant runs in the common `_prepare_handoff_plan` seam used by normal,
cached, semantic-direct, trivial, agent and memory plans. The existing quality
gate separately prevents any planned task, including precision work, from
being released without a terminal successful execution event. Together these
contracts cover selection before dispatch and execution closure before
release.

The recognizer remains deliberately small:

- unnumbered prose must contain exactly one fully parameterized, direct
  operation;
- contiguous numbered prompts are inspected item by item, preserving a
  deterministic item inside a mixed plan;
- code-generation requests, incidental examples, malformed numbering,
  invalid dates and multi-intent prose are not decomposed or guessed;
- optional arguments are accepted only when their discovered schema default
  has the same semantics, for example German `locale="de"`.

Successful MCP catalogue reloads now atomically replace the executable schema
set and omit tools whose live `enabled` flag is false. Failed reloads clear the
executable catalogue, preventing removed or unavailable tools from surviving
as stale planner contracts. Static fallback descriptions remain hints only and
cannot satisfy executable plan validation without a discovered schema.

The local regression on 2026-08-01 passed 756 tests, including German/English
positives, wrong tool and wrong-argument negatives, unavailable and disabled
tools, mixed prompts, false-positive controls and a direct-route integration
test. Orchestrator image
`sha256:f4751f7c8090a8c1a1b673e26f4d8687cc983e5f66a17956a6e81000fbda2a51`
then ran healthy with restart count zero and loaded 59/59 active schemas. In
that running container, a correct `calendar_facts` plan passed; a general
downgrade and wrong date were blocked before dispatch; an incidental date
example remained an allowed general task. This is local deployment evidence,
not a release claim: the checkout still contains 176 dirty entries.

## Precision-platform completion evidence

**Validated locally on 2026-08-02.** TASK-42 through TASK-50 are implemented
and exercised in the running deployment:

- `time_facts` and `timezone_convert` use explicit ISO values, IANA zones,
  pinned `tzdata==2026.3` and fail-closed DST gap/fold handling.
- `decimal_finance` accepts canonical Decimal strings, explicit scale,
  rounding and currency; it never uses binary float for the calculation.
- `exact_probability` returns bounded exact fractions and only produces a
  rounded Decimal projection when requested explicitly.
- `structured_validate` uses locked safe parsers and bounded input. Raw
  payload/schema data is hash-redacted in structured evidence, telemetry,
  logs and working keys.
- A versioned 13-case API corpus passed 13/13 after the rollout-found advice
  injection defect was fixed. Twelve pure requests across Chat, Responses and
  Anthropic used zero model tokens; the mixed request executed two MCP tools
  and one scoped code-review expert before evidence binding and quality pass.
- The full regression passed **908 tests in 5.04 seconds**. Governance 27/9,
  `mkdocs build --strict`, Compose config, both image `pip check` runs and the
  focused diff check passed.
- Active MCP image
  `sha256:7e28eeab4a5b05e56eb713cfbab834a6c9dc4ebfea9ae3594eb0f46c77c5564a`
  and orchestrator image
  `sha256:4320ca67eaaeaf5168d4c4c251427f99305e04bfbdbf1e60e3b4368f2e8d402f`
  are healthy with restart count zero. Both feature-flag and previous-image
  rollback were executed successfully before the final image was restored.

The complete fixed-corpus methodology, request IDs, timings, defects and
limitations are documented in
[`precision_rollout_benchmark_2026-08-02.md`](precision_rollout_benchmark_2026-08-02.md).

## Current deterministic coverage

| Domain | Current mechanism | Assessment | Remaining boundary |
|---|---|---|---|
| Arithmetic and algebra | `calculate`, `solve_equation`, `prime_factorize`, `gcd_lcm`, `decimal_finance`, `exact_probability` | Strong local coverage for bounded expressions and typed P0 finance/probability forms | Tax, exchange-rate and jurisdiction rules require versioned authoritative data; unsupported free-form formulae remain outside the guard |
| Dates and time | `date_diff`, `date_add`, `calendar_facts`, `time_facts`, `timezone_convert`, legacy `day_of_week` | Exact for explicit dates/instants and IANA-zone conversion with pinned tzdata | Holidays and business days require a versioned jurisdictional dataset |
| Units | `unit_convert` | Good typed-library coverage | Results should record unit-registry/library version for strict reproducibility |
| Basic statistics | `statistics_calc` | Deterministic on supplied data | Quantile convention, confidence intervals, regression and missing-value policy are not defined |
| Network calculation | `subnet_calc` | Good for supplied IP/CIDR | URL normalization, address/port policy and DNS are separate concerns; DNS is mutable external state |
| Hashes and encoding | `hash_text`, `base64_codec` | Good local coverage | Checksummed business identifiers and Unicode normalization are absent |
| Structured input | `json_query`, `regex_extract`, `text_analyze`, `structured_validate` | Bounded safe parsing plus local JSON Schema validation with redacted evidence | Canonicalization and explicitly registered local schema references are not implemented; remote references remain prohibited |
| Graph/file/code inspection | Graph, repository and attachment tools | Deterministic only relative to the supplied snapshot | Snapshot/content hash and parser version must accompany strong reproducibility claims |
| Legal and public knowledge | Legal, Wikidata, PubMed, Crossref, OpenAlex and related connectors | Grounded retrieval reduces fabrication | Output is mutable source data, not deterministic truth; provenance and `as_of` are mandatory |
| Web and browser search | Search/browser connectors | Useful research evidence | Ranking and page contents are non-deterministic and must never receive precision-tool trust merely because transport is MCP |

## Prioritized gaps

| ID | Priority | Proposed contract | Why it should intercept the LLM | Required guard or authority |
|---|---:|---|---|---|
| DET-01 | P0, expanded and validated | Precision-intent guard before/after planning | TASK-41 began with explicit GCD, km/h→m/s and weekday requests; TASK-46 through TASK-49 add typed time, Decimal-finance, probability and structured-validation forms. Other arithmetic, units, hashes, statistics, CIDR and version intents still need extractors before joining the allowlist. | Deterministic classifier rules, active tool-schema lookup, negative tests and no silent downgrade to an LLM-only answer; never broaden with guessed arguments |
| DET-02 | P0, implemented and validated | `time_facts` / `timezone_convert` | LLMs frequently guess UTC offsets and DST transitions. | Implemented with IANA zone, explicit instant/local value, `fold`, pinned tzdata and source version; holidays remain separate |
| DET-03 | P0, implemented and validated | `decimal_finance` | Percentages and compound interest are error-prone and rounding-sensitive. | Implemented with `decimal.Decimal`, explicit scale, rounding and currency; exchange rate/tax/jurisdiction data is deliberately excluded |
| DET-04 | P0, implemented and validated | `exact_probability` | Combinations and binomial probabilities are commonly approximated incorrectly. | Implemented bounded fraction/combination/permutation/binomial operations with optional explicit Decimal projection; broader conditional/hypergeometric contracts remain future work |
| DET-05 | P0, implemented and validated | `structured_validate` | LLMs often claim JSON/YAML/XML/config is valid without parsing it. | Implemented safe bounded JSON/YAML/XML/CSV parsing, local no-ref JSON Schema checks and redacted evidence; schema registries are not yet supported |
| DET-06 | P1 | `business_calendar` | Working-day and deadline answers depend on weekends, holidays and jurisdiction. | Country/subdivision, inclusive/exclusive rule, versioned holiday dataset and `as_of`; never infer jurisdiction |
| DET-07 | P1 | `version_compare` | SemVer, PEP 440 and dependency ranges are easy to compare incorrectly as strings. | Explicit scheme (`semver`, `pep440`, Debian where supported), parser version and range-intersection result |
| DET-08 | P1 | `identifier_validate` | IBAN, ISBN/ISSN, EAN/GTIN, Luhn and UUID checksums are exact and cheap. | Identifier type allowlist; distinguish checksum/format validity from real-world account or registration existence |
| DET-09 | P1 | `advanced_statistics` | Quantiles, confidence intervals and regression vary by convention and are error-prone in prose. | Operation enum, algorithm/convention, missing-value policy, confidence level and machine-readable result |
| DET-10 | P1 | `geospatial_calculate` | Distances, bearings, bounding boxes and CRS transforms are routinely guessed. | Explicit CRS/EPSG, coordinate order, ellipsoid/library version and bounded input count |
| DET-11 | P1 | `tokenizer_metrics` | Context-fit and token-count claims are model/tokenizer specific. | Exact tokenizer artifact and hash/version, role/template serialization and no heuristic labelled exact |
| DET-12 | P1 | Versioned constants/rules | Scientific constants, tax brackets, tariffs and statutory thresholds look numeric but change by edition or effective date. | Named authoritative dataset, edition/effective date, jurisdiction and provenance; interpretation remains with a domain expert |
| DET-13 | P2 | `schedule_expand` | Cron/RRULE next-run calculations and recurrence boundaries are easy to get wrong. | Explicit syntax dialect, start instant, IANA zone, DST policy and maximum occurrence count |

## Required result contract for future tools

New precision tools should converge on a common envelope rather than
unstructured success/error strings:

```json
{
  "status": "ok",
  "contract_version": "1",
  "input_normalized": {},
  "facts": {},
  "determinism": "input_only",
  "source": {
    "kind": "stdlib_or_versioned_dataset",
    "version": "explicit-version-or-hash",
    "as_of": null
  },
  "warnings": []
}
```

For external or mutable data, `determinism` must instead state
`source_snapshot`, and `source.version` or `source.as_of` must be populated.
Failures should use a typed top-level error response that the orchestrator
cannot mistake for evidence.

## What should remain an LLM task

Do not replace interpretation with a calculator. Creative writing,
requirements trade-offs, ambiguous intent resolution, ethical analysis,
clinical or legal judgment and synthesis across conflicting evidence remain
model/expert tasks. MCP should supply exact sub-results and sourced facts.

Likewise, weather, prices, exchange rates, office holders, schedules,
software releases and current legal text cannot be made timelessly
deterministic. They should be retrieved through authoritative connectors and
returned with provenance, retrieval time and freshness limits. The LLM may
explain the retrieved facts but must not invent a value when the connector
fails.

## Recommended execution order

1. Keep DET-01 narrow and extend it only with a typed, unambiguous extractor,
   adversarial negatives and an active versioned contract.
2. Apply the structured envelope and evidence-binding model to additional
   existing high-risk tools before granting them mandatory intent status.
3. Select the next P1 contract from DET-06 through DET-11 using observed
   error/usage telemetry, not catalogue size as the goal.
4. Add versioned-data candidates only after provenance, dataset lifecycle and
   freshness semantics exist.
5. Continue measuring activation, tool failure, LLM-only escape and exact
   answer accuracy with the fixed corpus during each rollout.

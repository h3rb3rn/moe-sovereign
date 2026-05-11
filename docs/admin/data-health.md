# Data Health & Drift Detection

Every successful knowledge-bundle import is wrapped in a stats snapshot.
The differences between *before* and *after* are run through
`services/data_health.compute_drift()`, which classifies the import on a
four-step severity ladder (`ok` · `info` · `warn` · `crit`) and tags it
with structured **flags**. Flagged events surface on the
[Enterprise Dashboard](../system/intelligence/enterprise_features.md) and
are retrievable via API.

> **Foundry equivalence.** Drift detection is the open-source counterpart
> to Palantir Foundry's data-health monitors and Health Checks — a
> synthetic gate around dataset writes that flags accidental no-ops, dedup
> regressions, and schema explosions.

## Flags

| Flag | Trigger | Severity |
|------|---------|----------|
| `entity_count_shrank` | Post-import entity count is **less** than pre-import | `crit` |
| `zero_entities_added` | Bundle declared > 0 entities but actual delta is 0 | `crit` |
| `entity_dedup_suppressed` | Actual delta is below `(1 − threshold) × declared` (default threshold 0.3) | `warn` |
| `entity_overshoot` | Actual delta exceeds `(1 + threshold) × declared` | `warn` |
| `relation_overshoot` | Same as `entity_overshoot` for relations | `warn` |
| `relation_to_entity_explosion` | Relations grew by ≥ 10× more than entities | `warn` |

The threshold is tunable via `DATA_HEALTH_DRIFT_THRESHOLD` (default `0.3`).

## Severity ladder

| Severity | Meaning | UI styling |
|----------|---------|-----------|
| `ok` | No flags raised | green pill |
| `info` | Routine import within thresholds | blue pill |
| `warn` | One or more soft anomalies | amber pill |
| `crit` | Hard regression — graph shrank or zero growth despite declared entities | red pill |

## API

### `GET /v1/graph/health/events?limit=50`

Returns the most recent drift events, newest first.

```json
{
  "events": [
    {
      "ts": "2026-05-10T08:14:22.117Z",
      "source_tag": "wikipedia-physics-2026",
      "trust_floor": 0.4,
      "drift": {
        "severity": "warn",
        "flags": ["entity_dedup_suppressed"],
        "delta_entities": 17,
        "delta_relations": 41,
        "declared_entities": 60,
        "declared_relations": 120,
        "ratio_entities": 0.28,
        "ratio_relations": 0.34
      }
    }
  ]
}
```

### `GET /api/health/events`

Admin-UI proxy used by the dashboard widget.

## Storage

Events are stored in Redis under the list key `moe:data_health:events`,
capped at `MAX_EVENTS = 500` via `LPUSH` + `LTRIM`. This avoids any
cleanup job and keeps the most recent window in fast memory. Long-term
retention is provided by the OpenLineage events on Marquez, which carry
the same source tag in the run facets.

## Hook in the import path

The import endpoint in `routes/graph.py` records before/after stats:

```python
_before_stats = (await state.graph_manager.get_stats() if not dry_run else {})
# ... import ...
if not dry_run:
    after_stats = await state.graph_manager.get_stats()
    drift = compute_drift(_before_stats, after_stats,
                          declared_entities=len(bundle.get("entities", [])),
                          declared_relations=len(bundle.get("relations", [])) or None)
    await record_event(state.redis_client, source_tag=source_tag,
                       drift=drift, trust_floor=trust_floor)
    stats["drift"] = drift
```

The hook is wrapped in a try/except so a Redis hiccup never blocks an
otherwise-successful import.

## Tuning the threshold

The default `0.3` permits ±30 % delta from the declared bundle counts
before a `warn` is raised. Larger deployments with heavy deduplication
should raise the floor (e.g. `DATA_HEALTH_DRIFT_THRESHOLD=0.5`); strict
deployments where every bundle should yield close to its declared counts
can lower it (`0.1` or `0.05`).

# Object Explorer

> **Migration notice (2026-05-11):** The Cypher endpoint `/v1/graph/cypher/read` stays in moe-sovereign (it is a graph operation). The Explorer **UI** migrates to [`moe-codex`](https://github.com/h3rb3rn/moe-codex) where it lives alongside Catalog/Approval/Drift/Notebook.

The Object Explorer (`/explorer`) is a read-only Cypher console for the
Neo4j knowledge graph. It is intended for ad-hoc analysis without leaving
the admin UI and without exposing the database to network reachability.

> **Foundry equivalence.** Object Explorer matches Palantir Gotham's object
> graph drill-down — a power-user surface that complements the Catalog and
> Approvals pages. Where Foundry uses a proprietary query DSL, MoE
> Sovereign uses standard Cypher.

## Two-layer write protection

Cypher is a powerful query language; the Explorer must never accept a
mutating statement, even from an admin. Two independent guards:

### 1. Regex blacklist (`routes/graph.py`)

```python
_FORBIDDEN_CYPHER = re.compile(
    r"\b(CREATE|DELETE|SET|MERGE|REMOVE|DROP|ALTER|GRANT|REVOKE|FOREACH)\b",
    re.IGNORECASE,
)
```

The blacklist runs **before** the query reaches Neo4j. Any match returns
HTTP 400 with the offending keyword.

### 2. Driver-side `READ_ACCESS` mode

Even if the regex is bypassed by an unforeseen Cypher syntax change, the
session is opened in `neo4j.READ_ACCESS` mode, which the database itself
enforces. Write statements raise a Neo4j error before any state change.

The combination ensures that a regression in one guard cannot turn the
Explorer into a write surface.

## API

### `POST /v1/graph/cypher/read`

```json
{
  "query": "MATCH (e:Entity) RETURN e.domain AS domain, count(*) AS n ORDER BY n DESC LIMIT 10",
  "parameters": {}
}
```

Returns the first 200 records as a list of dicts. Larger result sets are
truncated with a `truncated: true` flag.

### `POST /api/explorer/cypher`

Admin-only proxy from the UI. Same payload as the orchestrator endpoint.

## UI

- Code editor with Cypher syntax highlighting (Prism.js)
- Preset dropdown:
  - **Top entities** — `MATCH (e:Entity) RETURN e.name, count{(e)-[]-()} AS deg ORDER BY deg DESC LIMIT 25`
  - **Domain breakdown** — entity counts per domain
  - **Schema** — labels and relationship types
  - **Recent additions** — entities created in the last 24 hours
- "Open in Neo4j Browser" button — deep-links to a pre-filled tab on the
  configurable Neo4j Browser instance

## Translations

`nav.explorer`, `page.explorer`, `lbl.cypher_query`, `lbl.cypher_presets`,
`btn.run_query`, `btn.open_neo4j_browser` — synchronised across all
language files.

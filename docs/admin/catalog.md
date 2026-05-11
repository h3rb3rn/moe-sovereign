# Data Catalog

The Data Catalog (`/catalog`) is a single searchable view across all three
data back-ends in a Sovereign MoE deployment. It removes the need to switch
between Marquez, Neo4j Browser, and the lakeFS UI when looking up where a
dataset lives.

> **Foundry equivalence.** The Catalog is the open-source counterpart to
> Palantir Foundry's central dataset browser: one table, three sources,
> source-filterable, free-text search.

## Sources

| Source | Origin | What is listed |
|--------|--------|----------------|
| `marquez` | OpenLineage namespaces | Every dataset registered through a lineage event (input or output) |
| `neo4j` | `MATCH (e:Entity)` aggregated by `domain` | One row per knowledge domain — entity / relation / synthesis counts |
| `lakefs` | `GET /api/v1/repositories` | Each lakeFS repository with branch and commit counts |

The aggregation runs in `admin_ui.app:get_catalog_datasets()` and falls back
gracefully when individual back-ends are unreachable — a missing Marquez
does not hide Neo4j domains, etc.

## API

### `GET /api/catalog/datasets`

Returns a flat list of dataset records. Login is required; all roles can read.

```json
{
  "items": [
    {
      "source": "neo4j",
      "namespace": "knowledge_graph",
      "name": "biology",
      "type": "domain",
      "size": 1284,
      "metadata": { "relations": 4116, "synthesis_nodes": 12 }
    },
    {
      "source": "marquez",
      "namespace": "moe",
      "name": "knowledge_imports",
      "type": "dataset",
      "size": null,
      "metadata": { "fields": 7 }
    },
    {
      "source": "lakefs",
      "namespace": "knowledge",
      "name": "knowledge",
      "type": "repository",
      "size": 42,
      "metadata": { "branches": 6, "default_branch": "main" }
    }
  ],
  "totals": { "marquez": 14, "neo4j": 9, "lakefs": 1 }
}
```

### `GET /v1/graph/domains`

The Neo4j source data is exposed independently for use in dashboards and
external tooling. Returns domain rows ordered by entity count.

## UI

- Search box: substring match across `namespace`, `name`, and `metadata`
- Source filter: dropdown limiting the result set to one back-end
- Click a `neo4j` row → links to `/knowledge?domain=…`
- Click a `marquez` row → opens the dataset detail in the Marquez UI
- Click a `lakefs` row → opens the repository in the lakeFS UI

## Translations

The Catalog page is fully translated in `en_EN`, `de_DE`, `fr_FR`, `zh_CN`.
Strings live under `nav.catalog`, `page.catalog`, `lbl.search_filter`,
`lbl.all_sources`, `lbl.source`, `lbl.name`, `lbl.type`, `lbl.size`.

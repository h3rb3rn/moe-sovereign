# Palantir Comparison

> **This page has moved.**
>
> The detailed EU-Palantir-Alternative comparison, feature coverage matrix, and use-case scenarios are maintained in the **[moe-codex](https://github.com/h3rb3rn/moe-codex)** repository — the dedicated compliance and data intelligence platform for regulated industries.
>
> - Feature coverage: `moe-codex/docs/system/palantir_comparison.md`
> - Use cases: `moe-codex/docs/use-cases/`
> - Compliance mapping: `moe-codex/docs/system/eu_ai_act_mapping.md`, `bsi_grundschutz_mapping.md`, `nis2_readiness.md`

## MoE Sovereign Core

MoE Sovereign provides the sovereign LLM infrastructure layer: multi-model routing, GraphRAG, MCP tools, caching, and Admin UI. It covers the Palantir AIP / Foundry "query layer" as an open-source, EU-deployable alternative for the 95 % of operators who need intelligent LLM routing without compliance-grade data governance.

For the 5 % who need catalog, lineage, versioning, approval workflows, and investigation tools — deploy **moe-codex** alongside.

| Stack | Covers |
|-------|--------|
| moe-sovereign | AIP query API, expert routing, GraphRAG, MCP tools |
| moe-codex (optional) | Catalog, Approval, OpenLineage, lakeFS, Drift Detection, NiFi ETL |
| moe-libris (optional) | Federation hub between sovereign instances |

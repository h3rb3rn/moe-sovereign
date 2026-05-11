# Palantir vs. MoE Sovereign — Capability Comparison

> **2026-05-11 — Canonical location moved:** The Palantir-equivalent feature
> set (Catalog, Approval, Explorer, Drift, Notebook + Marquez/lakeFS/NiFi)
> now lives in the dedicated [`moe-codex`](https://github.com/h3rb3rn/moe-codex)
> repository. The authoritative version of this comparison page is at
> `moe-codex/docs/system/palantir_comparison.md`. The copy here remains for
> reference until the next moe-sovereign release; new entries should be made
> in moe-codex.

This page tracks how MoE Sovereign maps onto the Palantir product portfolio
(Foundry · Gotham · AIP · Apollo). It is the canonical reference for product
positioning, gap analysis, and roadmap discussions.

> **Maintenance contract.** This page must be updated whenever a Key Capability
> is added in `README.md`, a new admin-UI surface ships, or a Foundry/AIP
> module shifts category. See [Maintenance](#maintenance) at the bottom.

**Status legend:**

- ✅ fully covered
- 🟢 partially covered
- 🟡 nachrüstbar with a defined open-source tool
- 🔴 not practical to cover
- ⚪ out of scope for sovereignty-first deployments

---

## 1. Palantir Foundry — Data Platform

| # | Module / Feature | What Palantir delivers | MoE Sovereign — current state | Open-source path / gap |
|---|---|---|---|---|
| 1.1 | **Ontology** | Typed object model with properties, relations, methods, Action Types — single source of truth for business objects | 🟢 Neo4j knowledge graph with Entity/Relation, GraphRAG manager + domain mapping | **Erweiterbar** with Pydantic schemas for object types + Apache Atlas (formal ontology layer) or LinkML |
| 1.2 | **Data Connection** | ~200 prebuilt connectors (SAP, Oracle, Salesforce, REST, file, JDBC) | 🟢 NiFi `ListenHTTP` + knowledge bundle import (Phase 17) | **Erweiterbar** with Apache NiFi (300+ processors), Airbyte (350+ connectors), Meltano |
| 1.3 | **Pipeline Builder (visual ETL)** | Drag-and-drop ETL DSL with Spark backend | 🟡 NiFi UI (visual) but no code generator | **Ergänzbar** with Apache Hop, Kestra, Dagster (semi-visual), Mage AI |
| 1.4 | **Code Repositories** | Git-backed transform code (PySpark, Java) with built-in CI | ⚪ Not relevant — orchestrator itself is git-versioned | — |
| 1.5 | **Code Workbook** | Jupyter-equivalent exploratory workbook with direct dataset access | ✅ JupyterLite embedded (Phase 24) + 5 API snippets | Fully covered |
| 1.6 | **Datasets (typed)** | Versioned typed tables | 🟢 lakeFS repos + knowledge bundles as schema-validated JSON | **Erweiterbar** with Apache Iceberg / Delta Lake for tabular datasets |
| 1.7 | **Branching & Versioning** | Proposal branches with merge approval | ✅ lakeFS `pending/<tag>-<ts>` + approval UI (Phase 18+21) | Fully covered |
| 1.8 | **Health Checks** | Automated data-quality monitors (schema, freshness, volume, custom rules) | 🟢 Drift detection (Phase 23) — 6 flag types, severity ladder | **Ergänzbar** with Great Expectations, Soda Core, Apache Griffin for deeper schema/volume checks |
| 1.9 | **Data Lineage** | End-to-end dataset → transform → output graph with drilldown | ✅ Marquez/OpenLineage (Phase 16) | Fully covered |
| 1.10 | **Catalog** | Searchable dataset browser with metadata | ✅ `/catalog` cross-source browser (Phase 20) | **Optional erweiterbar** with DataHub, Amundsen, OpenMetadata for tagging/glossary |
| 1.11 | **Foundry SQL** | Federated SQL across all datasets | 🟡 Cypher Explorer (Phase 22) for graph; no SQL federation | **Ergänzbar** with Trino (Presto), Apache Drill for SQL federation |
| 1.12 | **Object Explorer** | UI drilldown over ontology objects | ✅ `/explorer` with Cypher editor + Neo4j Browser deep-link (Phase 22) | Fully covered for the graph layer |
| 1.13 | **Workshop (Low-Code Apps)** | Drag-and-drop UI builder for internal applications | 🔴 Not covered | **Ergänzbar** with Appsmith, ToolJet, Budibase, Retool OSS — no direct match in workflow depth |
| 1.14 | **Slate (custom JS UIs)** | JS framework for full custom apps | ⚪ Out of scope — admin UI itself fills this role | — |
| 1.15 | **Quiver (Pivot/Charts)** | Excel-equivalent pivots + visualisations | 🟡 Grafana for metrics; no pivot builder | **Ergänzbar** with Apache Superset, Metabase, Redash |
| 1.16 | **Forms** | Schema-driven data-entry forms | 🔴 Not covered | **Ergänzbar** with Form.io, JSONForms, NocoDB |
| 1.17 | **Action Types (Writeback)** | Structured mutations into the ontology with validation | 🟢 `/v1/graph/knowledge/import` with trust-floor + approval gate | Conceptually equivalent |
| 1.18 | **Webhooks / Functions** | Event handlers for ontology mutations | 🟢 Kafka event bus + LangGraph pipeline nodes | Present but no UI builder for them |
| 1.19 | **Permissions / Markings / Restrictions** | Fine-grained access control with markings (classification labels) | 🟢 Authentik OIDC + RBAC + `graph_tenant` permission | **Erweiterbar** with OPA (Open Policy Agent), Apache Ranger for markings/ABAC |
| 1.20 | **Resource Management** | Compute quotas per team/project | 🟢 Token budget per user; no compute quotas | **Ergänzbar** with Kubernetes ResourceQuotas, KubeFlow |
| 1.21 | **Geospatial / Map App** | Fully integrated map layers for objects | 🔴 Not covered | **Ergänzbar** with PostGIS, KeplerGL, deck.gl, Mapbox-OSS — but ontology integration is missing |
| 1.22 | **Time Series Catalog** | Specialised model for time series | 🔴 Not covered | **Ergänzbar** with TimescaleDB, InfluxDB, VictoriaMetrics |
| 1.23 | **Notepad / Reports** | Collaborative documents with live data | 🔴 Not covered | **Ergänzbar** with Outline, HedgeDoc; deep live-data integration is missing |
| 1.24 | **Telemetry / Monitoring** | Platform telemetry | ✅ Prometheus + Grafana + pipeline log + Starfleet | Fully covered |

---

## 2. Palantir Gotham — Investigation / Intelligence

| # | Module / Feature | What Palantir delivers | MoE Sovereign — current state | Open-source path / gap |
|---|---|---|---|---|
| 2.1 | **Object/Link Explorer** | Investigative drilldown over persons/objects/relations | 🟢 `/explorer` (Cypher) + Neo4j Browser | Present, but no investigative UI workflow |
| 2.2 | **Graph (Link Analysis)** | Visual link analysis of relationship networks | 🟢 Neo4j Browser (embedded) | **Erweiterbar** with Linkurious OSS, Cytoscape.js, Gephi |
| 2.3 | **Timeline / Temporal Analysis** | Cross-object event timeline | 🔴 Not covered | **Ergänzbar** with vis-timeline.js, Apache Pinot for event stores |
| 2.4 | **Geospatial Investigation** | Map with live object overlay | 🔴 Not covered | **Ergänzbar** with OpenStreetMap stack, KeplerGL — investigation tooling is missing |
| 2.5 | **Document Intelligence** | OCR + entity extraction from PDFs/scans | 🟢 Existing bundle import pipeline + MCP tools | **Erweiterbar** with Unstructured.io, Apache Tika, ColPali, Tesseract |
| 2.6 | **Helix (Entity Resolution)** | Duplicate resolution across data sources | ✅ Fuzzy entity deduplication (Ratcliff/Obershelp, Capability #28) | Fully covered |
| 2.7 | **Dossier (Case File)** | Structured case file with live updates | 🔴 Not covered | **Ergänzbar** with Mattermost, Outline; deep ontology binding is missing |
| 2.8 | **Mobile / Tactical Edge** | Hardened mobile clients for field deployment | 🔴 Not practical without a dedicated mobile team | Theoretically React Native, but Gotham-grade not realistic |
| 2.9 | **Federated Search** | Full-text search across all data sources | 🟢 Vector search (ChromaDB) + GraphRAG hybrid | **Erweiterbar** with Elasticsearch/OpenSearch for classic full-text search |

---

## 3. Palantir AIP — AI Platform

| # | Module / Feature | What Palantir delivers | MoE Sovereign — current state | Open-source path / gap |
|---|---|---|---|---|
| 3.1 | **Multi-Model Routing** | LLM selection across providers | ✅ 15 expert specialists + deterministic complexity routing | **Übertrifft** AIP — proprietary 7B-ensemble innovation |
| 3.2 | **AIP Logic (Decision Automation)** | LLM-backed decision nodes in workflows | ✅ LangGraph pipeline + Agentic Re-Planning Loop | Fully covered |
| 3.3 | **AIP Tools (Function Calling)** | Structured tool calling | ✅ 51 MCP Precision Tools (deterministic) | **Übertrifft** — AST whitelist for safety |
| 3.4 | **AIP Assist (In-Product Chat)** | Built-in chat in the platform | 🟢 Open WebUI integrated + chat endpoint | Fully covered |
| 3.5 | **AIP Threads (Multi-Turn)** | Persistent conversations with context | ✅ ChromaDB Tier-2 memory (1M-token context) | **Übertrifft** — Tier-2 vector memory |
| 3.6 | **AIP Studio (Agent Builder)** | Low-code agent builder | 🟢 Templates + CC profiles via admin UI | Conceptually equivalent |
| 3.7 | **AIP Evaluations** | Structured evaluation framework | 🟢 GAIA + LongMemEval benchmarks under `/benchmarks` | **Erweiterbar** with Promptfoo, Ragas, DeepEval |
| 3.8 | **AIP Guardrails** | Policy layer (topic, PII, safety) | 🟢 Quarantine + self-correction journal | **Erweiterbar** with NeMo Guardrails, Llama-Guard |
| 3.9 | **AIP Functions** | Python/TS functions callable from the LLM | ✅ MCP tools are exactly that | Fully covered |
| 3.10 | **AIP Hub (Model Registry)** | Model metadata catalog | 🟢 Inference-server configuration in admin UI | **Erweiterbar** with MLflow, Hugging Face Hub (self-hosted) |
| 3.11 | **AIP Logic-LLM Decision Trees** | Audited decision pipelines | 🟢 LangGraph state + pipeline log | Conceptually equivalent |
| 3.12 | **Federated Knowledge Sync** | (not present in AIP) | ✅ MoE Libris (differentiator!) | **Übertrifft Palantir** |
| 3.13 | **Confidence Decay / Trust Score** | Limited — manually configured | ✅ Trust score with decay (Capability #25–28) | **Übertrifft Palantir** |

---

## 4. Palantir Apollo — Continuous Deployment

| # | Module / Feature | What Palantir delivers | MoE Sovereign — current state | Open-source path / gap |
|---|---|---|---|---|
| 4.1 | **Multi-Env Continuous Delivery** | Software rollout across cloud/on-prem/air-gap | 🟢 `install.sh` + Docker Compose + LXC/Kubernetes charts | **Erweiterbar** with ArgoCD, Flux, Tekton |
| 4.2 | **Constraint Solver** | Automatic computation of which release goes to which environment | 🔴 Not covered | **Theoretisch erweiterbar** with OPA constraint templates; Apollo grade not realistic |
| 4.3 | **Compliance Posture** | Real-time compliance monitoring per environment | 🔴 Not covered | **Erweiterbar** with OpenSCAP, Inspec, Falco |
| 4.4 | **Air-Gap Releases** | Sealed bundles for isolated environments | 🟢 Federation bundle format + air-gap mode | Conceptually equivalent |

---

## 5. Use Cases / Application Domains

| Industry / Domain | Palantir deployment (real, documented) | MoE Sovereign suitability | Additional pieces required |
|---|---|---|---|
| **Defense & Intelligence** | Gotham at US Army, NATO, AFRICOM — target identification, signals-intel fusion | 🟡 GraphRAG + Federation for knowledge consolidation; **no** sensor fusion or tactical-edge tooling | Geospatial layer, sensor adapters, hardened-edge hardware |
| **Healthcare (clinical/pharma)** | NHS COVID response, hospital operations, pharmacovigilance at Merck | 🟢 Medicine expert (`meditron`), knowledge graph for symptom-to-diagnosis mapping, privacy-by-design | HL7/FHIR connector, GxP/HIPAA audit logging |
| **Manufacturing** | Airbus (A350 ops), Ferrari, BMW — production optimisation, quality control | 🟢 Knowledge graph for BOMs / supply chains, drift detection for quality metrics | OPC-UA connector, MES integration, time-series DB |
| **Energy** | BP, Shell, Edison — exploration, predictive maintenance, trading | 🟢 Multi-domain routing, time-series integration possible | SCADA integration, specialised time-series models |
| **Banking / Insurance** | BNP Paribas, Credit Suisse — fraud, compliance, underwriting | 🟢 Causal learning, trust score, audit log via Kafka | KYC/AML schemas, regulatory audit trails |
| **Government / Public Services** | UK NHS, US HHS, German agencies — case management, service desk | 🟢 Self-hosted, GDPR-compliant, multi-tenant | Forms builder, case-management UI |
| **Crisis Response** | FEMA, COVID tracing — real-time situational awareness | 🟡 Kafka event streaming + Starfleet dashboard, **but** no geo layer | Geospatial stack, real-time map sync |
| **Supply Chain** | Airbus suppliers, pandemic vaccine logistics | 🟢 Knowledge graph for supplier relationships, NiFi for heterogeneous data integration | EDI/EDIFACT connectors, trade-compliance rules |
| **Cybersecurity / SecOps** | Cyber-Mesh, SOAR workflows | 🟢 MCP tools, audit log, self-correction | SIEM integration (e.g. Wazuh), threat-intel feeds |
| **Pharma / Drug Discovery** | Merck, Novartis — research knowledge graph | ✅ ChEMBL/PubChem MCP tools + knowledge graph + 7B ensemble | **Already deployable** — strength of the sovereign approach |
| **Telecommunications** | Network operations, customer churn | 🟡 Trust-score system suitable | NetCDF/NetFlow adapter |
| **Research & Science** | (Palantir rarely present here) | ✅ ORCID/PubMed/PubChem MCP tools + Federated Knowledge Sync | **Strength of the sovereign approach** |

---

## 6. Structural Coverage Balance

| Area | Palantir modules | MoE-covered | OS-erweiterbar | Not coverable |
|---|---|---|---|---|
| **Foundry** | 24 | 13 | 8 | 3 (Workshop depth, tactical geo, Apollo constraint solver) |
| **Gotham** | 9 | 3 | 5 | 1 (Mobile / tactical edge) |
| **AIP** | 13 | 12 | 1 | 0 — **MoE übertrifft AIP** in 3 dimensions (Federation, trust decay, 7B ensemble) |
| **Apollo** | 4 | 1 | 2 | 1 (Constraint solver) |
| **Σ** | **50** | **29 (58 %)** | **16 (32 %)** | **5 (10 %)** |

### Summary

- **MoE Sovereign already covers ~58 %** of the full Palantir feature space — strongest in **AIP (the AI layer) + Foundry catalog/lineage/versioning**.
- **A further ~32 %** is reachable with defined open-source tools — primarily Pipeline Builder, Forms, geospatial, SQL federation, permission markings, classic full-text search.
- **~10 % is structurally not practical** — tactical-edge hardware, Apollo constraint solver, Slate depth, Workshop depth (no comparable integrated workflow engine in the OS ecosystem).

### Three areas where MoE Sovereign already exceeds Palantir

1. **Federated Knowledge Sync (MoE Libris)** — inter-cluster knowledge exchange does not exist in Palantir.
2. **Trust score with decay** — automatic self-healing of the knowledge graph; in AIP only manually configured.
3. **Domain-specialist 7B ensemble** — GPT-4o-mini-class performance on Tesla M10 hardware with **zero** cloud dependency.

---

## Maintenance

This page is the canonical source for product positioning vs. Palantir.

**Update triggers:**

- Any new entry in the Key Capabilities table in `README.md` → update the relevant row's "MoE Sovereign — current state" cell here.
- Any new admin-UI surface or `/route` exposed → add or update the matching Foundry/AIP row.
- Any new MCP tool category → re-evaluate row 3.3 (AIP Tools) and row 3.9 (AIP Functions).
- Any change to the federation protocol → re-evaluate row 3.12 (Federated Knowledge Sync).
- Any structural Palantir release that adds a new product module → add a section.

**Coverage-balance section:** the totals in section 6 must be recomputed whenever the row-level statuses change. Keep the tally aligned with the per-section tables; mismatches are bugs.

**Symbol discipline:** the legend at the top of this file is the single source for status symbols. Do not introduce new symbols without updating the legend.

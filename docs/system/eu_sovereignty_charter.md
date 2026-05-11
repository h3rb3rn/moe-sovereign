# EU Sovereignty Charter

MoE Sovereign is built on the conviction that critical AI infrastructure
in Europe must be **operable without dependency on US-controlled cloud
platforms or proprietary AI services**. This page is the explicit
positioning statement — used in operator briefings, procurement
processes, and public communication.

---

## 1. The three layers of sovereignty

Sovereignty is not a single property but a layered claim. MoE Sovereign
secures all three:

| Layer | What it means | How MoE Sovereign delivers |
|---|---|---|
| **Data sovereignty** | Operator data never leaves jurisdictions or systems the operator controls | All inference local; SearXNG private search; no telemetry to external services; air-gap deployment supported |
| **Model sovereignty** | The LLMs themselves run on operator hardware, with weights inspectable and replaceable | Ollama-hosted open-weight models (Mistral, Llama, Qwen, Mathstral); 15 expert templates; no API key dependency on US LLM providers |
| **Infrastructure sovereignty** | The runtime, networking, and dependencies are operator-controllable | Apache-2.0 codebase; Docker / Kubernetes / LXC deployment paths; only OSI-compatible OS dependencies (see `license_compliance.md`) |

---

## 2. Why this matters now

Three concrete drivers in 2024–2026:

1. **Hessendata ruling 2023 (BVerfG)** — the German Federal
   Constitutional Court ruled that the Palantir-based "Hessendata"
   policing platform violates fundamental rights as deployed. The
   judgment created an acute regulatory need for sovereign alternatives
   across the EU.
2. **EU AI Act (Regulation 2024/1689)** — in force since 2024-08-01;
   high-risk system obligations apply from 2026-08-02. Operators of AI
   in regulated sectors must document risk class, training-data
   provenance, and audit trails — much easier on a transparent stack.
3. **NIS2 transposition** — national laws transposing NIS2 (in Germany:
   NIS2UmsuCG, draft 2024) require risk management, incident reporting,
   and supply-chain accountability for essential and important entities.
   Closed US-cloud stacks complicate compliance; sovereign stacks
   simplify it.

The combined effect: between 2025 and 2027, every EU-based operator of
AI infrastructure in regulated sectors needs a documented strategy for
(a) data residency, (b) model transparency, (c) supply-chain audit.
MoE Sovereign provides all three by construction.

---

## 3. Recommended hosting providers

Operators looking to deploy MoE Sovereign in line with EU sovereignty
should primarily use one of the following EU-based hosting providers.
All of them offer bare-metal or GPU instances under EU jurisdiction.

### Germany

- **Hetzner Online** (Falkenstein, Nürnberg, Helsinki/EU) — GPU servers
  from GTX 1080 up to RTX 4000 SFF; the lowest-cost GPU bare-metal option
  in the EU.
- **IONOS** (Karlsruhe, Berlin, Frankfurt) — Enterprise Cloud + bare-metal;
  BSI C5 certified; partner of the SovS initiative.
- **STACKIT** (Berlin, Frankfurt) — Schwarz Group; sovereign federal-cloud
  status in Germany; C5 certified; functional peer of Open Telekom Cloud.
- **Open Telekom Cloud (OTC)** — Telekom-driven; OpenStack-based; C5 and
  ISO 27001 certified.
- **plusserver** (Cologne, Hamburg, Berlin) — member of the GDPR Cloud
  Initiative.

### France

- **OVHcloud** (Roubaix, Strasbourg, Gravelines) — the largest EU cloud
  provider; SecNumCloud certified; GPU instances available.
- **Scaleway** (Paris, Amsterdam, Warsaw) — H100/H200 GPU instances,
  ARM clusters, bare-metal.

### Other EU regions

- **Exoscale** (Switzerland / EU) — Swiss Cloud; clear GDPR positioning.
- **UpCloud** (Helsinki) — Finnish provider; fully EU jurisdiction.
- **Hostinger Cloud** (Lithuania) — EU jurisdiction; low-cost VPS option.

### Explicitly not recommended for sovereignty-critical deployments

- **AWS, Azure, GCP** — subject to the US CLOUD Act; no complete data
  residency guarantee even when using EU regions.
- **AWS Outposts** and Azure Local — they provide local compute but the
  control plane remains US-controlled.
- **Cloudflare** as reverse proxy for sensitive data — same caveat.

For air-gap deployments (e.g. authorities under KritIS classification)
**on-premises** with own hardware is recommended in addition — MoE
Sovereign is explicitly designed for this case (`INSTALL_*=false` for
external services, no outbound connections when idle).

---

## 4. The four-licence-layer guarantee

For every component in our stack we guarantee:

1. **Source code is publicly available** (no obfuscated binaries)
2. **Licence is OSI-approved** (no BSL, no SSPL, no CCL, no ELv2 — see
   `license_compliance.md` for the full blocklist)
3. **The runtime is operable without external network calls** for the
   core inference path; explicit opt-in for federation and web search
4. **No telemetry beacons** — neither in the containers we ship nor in
   their default configurations

Operators can verify this with `scripts/audit-licenses.sh` (licence
hygiene) and `docs/PRIVACY.md` (data-flow inventory).

---

## 5. Comparison to closed alternatives

| Concern | MoE Sovereign | Palantir Foundry/AIP | US-Cloud LLM APIs |
|---|---|---|---|
| Data leaves EU jurisdiction | ❌ never (operator choice) | 🟡 depends on contract | ✅ always |
| Subject to US CLOUD Act | ❌ no (when on EU host) | ✅ yes | ✅ yes |
| Training / model-weight inspection | ✅ open weights, public licence | ❌ proprietary | ❌ proprietary |
| Source code auditable | ✅ Apache 2.0, public | ❌ no | ❌ no |
| Air-gap deployment | ✅ documented | 🟡 contract option | ❌ not possible |
| BSI-C5-certified EU host available | ✅ Hetzner / IONOS / STACKIT / OVH | 🟡 possible but with vendor lock-in | ❌ not without dedicated hosting |
| Vendor lock-in risk | ❌ none (Apache 2.0, fork-able) | ✅ high | ✅ high |
| Per-token operator cost | €0 (own hardware) | metered + licence | metered |

---

## 6. Repository topology — what runs where

The MoE Sovereign family follows a deliberate split that reflects how
operators actually adopt sovereign AI:

| Repo | Role | Who deploys it |
|---|---|---|
| **moe-sovereign** | Core: API gateway, multi-model routing, caching, GraphRAG basis, 15 expert specialists | Everyone running sovereign LLM infrastructure |
| **moe-libris** | Federation hub: knowledge-bundle exchange between sovereign instances | Consortia / federated research networks |
| **moe-codex** | EU-Palantir alternative: data catalog, approval workflow, lineage, versioning, investigation, drift detection | Compliance-driven deployments (authorities, KritIS, pharma audit, banks) |

The core (`moe-sovereign`) is the **broad-market product**. `moe-codex`
is **opt-in** — only deployed where Foundry- / Gotham-equivalent
functionality is genuinely needed. This split is intentional: ~95 % of
operators want a sovereign LLM gateway; only the regulated minority
needs the full data-platform stack.

See `docs/system/license_compliance.md` for tool selection per repo and
`moe-codex/docs/system/palantir_comparison.md` (when present) for the
detailed feature-by-feature mapping.

---

## 7. Maintenance contract

This page is the canonical EU-sovereignty positioning statement.

**Update triggers:**

- A new EU regulation enters force that affects sovereignty positioning
  (e.g. national transposition of NIS2 in a member state) → update
  section 2.
- A new EU-jurisdiction-conformant hosting provider matches our criteria
  → add a row in section 3.
- A component drops out of OSI compatibility → reflect in section 4 and
  `license_compliance.md`.
- Repo topology changes → update section 6.

The comparison table in section 5 may **not** be tweaked into marketing
puffery — every row must remain factually defensible under § 6 UWG and
Art. 4 Comparative-Advertising Directive 2006/114/EC.

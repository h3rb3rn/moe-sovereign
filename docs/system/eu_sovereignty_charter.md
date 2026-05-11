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

1. **Hessendata-Urteil 2023 (BVerfG)** — German Federal Constitutional
   Court ruled that the Palantir-based "Hessendata" policing platform
   violates fundamental rights as deployed. The judgment created an
   acute regulatory need for sovereign alternatives across the EU.
2. **EU AI Act (Verordnung 2024/1689)** — In force since 01.08.2024;
   high-risk system obligations apply from 02.08.2026. Operators of
   AI in regulated sectors must document risk class, training data
   provenance, and audit trails — much easier on a transparent stack.
3. **NIS2 transposition** — National laws transposing NIS2 (DE: NIS2UmsuCG,
   draft 2024) require risk management, incident reporting, and supply-chain
   accountability for essential and important entities. Closed
   US-Cloud stacks complicate compliance; sovereign stacks simplify it.

The combined effect: between 2025 and 2027, every EU-based operator of
KI-Infrastruktur in regulierten Sektoren needs a documented strategy
für (a) Datenresidenz, (b) Modell-Transparenz, (c) Lieferketten-Audit.
MoE Sovereign provides all three by construction.

---

## 3. Recommended hosting providers

Operators looking to deploy MoE Sovereign in line with EU-Souveränität
sollten primär einen der folgenden EU-basierten Hosting-Anbieter
einsetzen. Alle bieten Bare-Metal- oder GPU-Instanzen unter EU-Rechtsraum:

### Deutschland

- **Hetzner Online** (Falkenstein, Nürnberg, Helsinki/EU) — GPU-Server
  ab GTX 1080 bis RTX 4000 SFF; günstigste GPU-Bare-Metal-Option in der EU
- **IONOS** (Karlsruhe, Berlin, Frankfurt) — Enterprise Cloud + Bare-Metal;
  BSI-C5-zertifiziert; SovS-Initiative-Partner
- **STACKIT** (Berlin, Frankfurt) — Schwarz-Gruppe; deutsche
  Bundes-Cloud-Provider-Status; C5-zertifiziert; Open-Telekom-Cloud-Pendant
- **Open Telekom Cloud (OTC)** — Telekom-getrieben; OpenStack-basiert;
  C5- und ISO-27001-zertifiziert
- **plusserver** (Köln, Hamburg, Berlin) — DSGVO-Cloud-Initiative-Mitglied

### Frankreich

- **OVHcloud** (Roubaix, Strasbourg, Gravelines) — größter EU-Cloud-Anbieter;
  SecNumCloud-zertifiziert; GPU-Instanzen verfügbar
- **Scaleway** (Paris, Amsterdam, Warschau) — GPU-H100/H200-Instanzen,
  ARM-Cluster, Bare-Metal

### Andere EU

- **Exoscale** (Schweiz/EU) — Swiss Cloud; klare GDPR-Positionierung
- **UpCloud** (Helsinki) — finnischer Anbieter; voll EU-Rechtsraum
- **Hostinger Cloud** (Litauen) — EU-Rechtsraum, günstige VPS-Option

### Explizit nicht empfohlen für sovereign-kritische Deployments

- **AWS, Azure, GCP** — CLOUD-Act-Unterworfenheit, keine vollständige
  Datenresidenz-Garantie selbst bei EU-Regionen
- **AWS Outposts** und Azure-Local — bietet zwar lokale Compute, aber
  Control-Plane bleibt US-controlled
- **Cloudflare** als Reverse-Proxy für sensible Daten — gleicher Vorbehalt

Für Air-Gap-Deployments (z.B. Behörden mit KritIS-Einstufung) wird
zusätzlich **On-Premises** mit eigener Hardware empfohlen — MoE
Sovereign ist explizit dafür ausgelegt (`INSTALL_*=false` für externe
Services, keine Outbound-Connections im Idle).

---

## 4. The four-license layer guarantee

For every component in our stack we guarantee:

1. **Source code is publicly available** (no obfuscated binaries)
2. **License is OSI-approved** (no BSL, no SSPL, no CCL, no ELv2 — see
   `license_compliance.md` for the full blocklist)
3. **The runtime is operable without external network calls** for the
   core inference path; explicit opt-in for federation and web search
4. **No telemetry beacons** — neither in containers we ship nor in
   their default configurations

Operators can verify this with `scripts/audit-licenses.sh` (license
hygiene) and `docs/PRIVACY.md` (data flow inventory).

---

## 5. Comparison to closed alternatives

| Concern | MoE Sovereign | Palantir Foundry/AIP | US-Cloud LLM APIs |
|---|---|---|---|
| Data leaves EU jurisdiction | ❌ never (operator choice) | 🟡 depends on contract | ✅ always |
| Subject to US CLOUD Act | ❌ no (when on EU host) | ✅ yes | ✅ yes |
| Trainings-/Model-Weight-Inspection | ✅ open weights, public licence | ❌ proprietary | ❌ proprietary |
| Source code auditable | ✅ Apache 2.0, public | ❌ no | ❌ no |
| Air-gap deployment | ✅ documented | 🟡 contract option | ❌ not possible |
| BSI-C5-konformer EU-Host wählbar | ✅ Hetzner / IONOS / STACKIT / OVH | 🟡 möglich, aber Vendor-Lock-In | ❌ nicht ohne dediziertes Hosting |
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
| **moe-codex** | EU-Palantir-Alternative: data catalog, approval workflow, lineage, versioning, investigation, drift detection | Compliance-driven deployments (authorities, KritIS, pharma audit, banks) |

The core (`moe-sovereign`) is the **broad-market product**. `moe-codex`
is **opt-in** — only deployed where Foundry-/Gotham-equivalent
functionality is genuinely needed. This split is intentional: 95 % of
operators want a sovereign LLM gateway; only the regulated minority
needs the full data-platform stack.

See `docs/system/license_compliance.md` for tool selection per repo and
`moe-codex/docs/system/palantir_comparison.md` (when present) for the
detailed feature-by-feature mapping.

---

## 7. Maintenance contract

This page is the canonical EU-Souveränität-Statement.

**Update triggers:**

- New EU regulation enters force that affects sovereignty positioning
  (e.g. NIS2-Umsetzung in einzelnen Mitgliedstaaten) → update section 2.
- New EU-rechtsraum-konformer Hosting-Provider passt unsere Kriterien
  → add row in section 3.
- Component drops out of OSI-compatibility → reflect in section 4 and
  `license_compliance.md`.
- Repo topology changes → update section 6.

The Comparison table in section 5 may **not** be tweaked into marketing
puffery — every row must remain factually defensible under § 6 UWG.

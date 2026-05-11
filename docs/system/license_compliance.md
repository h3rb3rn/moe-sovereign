# License Compliance & Open-Source Hygiene

MoE Sovereign is published under **Apache 2.0**. Every component we ship,
embed, or recommend must be compatible with that licence and — more
strictly — must remain OSI-approved Open Source. This page is the
canonical reference for tool selection, dependency vetting, and the
operator-facing legal posture.

> **Scope.** This page applies to `moe-sovereign`, `moe-libris`, and
> `moe-codex` equally. Differences (if any) are called out explicitly.

---

## 1. The Linux-Foundation-Fork principle

> **Mandatory rule:** Whenever an open-source tool relicenses to a
> non-OSI-compatible form (BSL, SSPL, Confluent Community Licence,
> Elastic License v2, RSALv2, "Source-Available" without OSI approval),
> we evaluate the Linux-Foundation- or community-driven OSI-compatible
> fork and prefer it — even if it lags the original by a few features.

Why: Vendor relicensing weaponises lock-in. Linux-Foundation forks
exist precisely because the community pre-empted that risk. Their
governance is transparent, their roadmap public, and their long-term
licence stability is structurally protected.

Already applied in the stack:

| Original | Relicense | We use |
|---|---|---|
| Redis 7.4+ | RSALv2/SSPL | **Valkey** (BSD-3-Clause, Linux Foundation) |
| Confluent Kafka | Confluent Community Licence | **Apache Kafka** (Apache 2.0) |

Pre-approved replacements for the `moe-codex` stack expansion:

| Original | Relicense | We will use |
|---|---|---|
| Elasticsearch ≥ 7.11 | SSPL/Elastic 2.0 | **OpenSearch** (Apache 2.0) |
| Terraform ≥ 1.6 | BSL 1.1 | **OpenTofu** (MPL-2.0) |
| Vault ≥ 1.15 | BSL 1.1 | **OpenBao** (MPL-2.0) |
| Airbyte | Elastic License v2 | **Meltano** (MIT) |
| Outline | BSL 1.1 | **HedgeDoc** (AGPL-3.0) |
| Unstructured (Cloud) | Mixed proprietary | **DocLing** (MIT) for OSS path |
| CockroachDB ≥ 23.1 | BSL 1.1 | **Postgres** + Citus extension |

---

## 2. Allowlist — unrestricted use

Components published under any of these licences may be embedded,
imported, or bundled without restriction:

- **Apache 2.0**
- **MIT**
- **BSD-2-Clause / BSD-3-Clause / 0BSD / ISC**
- **PostgreSQL License**
- **Unlicense / CC0-1.0**
- **Python Software Foundation License** (for stdlib-adjacent code)

These are the default target for new dependencies.

---

## 3. Allowlist with aggregation pattern (separate container)

The following Copyleft licences are OSI-compatible but introduce
source-disclosure obligations *if* the licensed code is statically or
dynamically linked into our own code. We meet the licence by treating
each such component as a **separate Docker service** with a clear
network boundary — what the FSF calls "mere aggregation" — and never
importing its source into MoE codebases.

| Licence | Components in our stack | Aggregation strategy |
|---|---|---|
| **AGPL-3.0** | Grafana, MinIO, SearXNG, HedgeDoc (planned) | Own container, talks via HTTP only |
| **GPL-3.0** | Neo4j Community | Own container, talks via Bolt protocol |
| **LGPL** | (no current dependency) | Linking allowed; document if introduced |
| **MPL-2.0** | OpenTofu, OpenBao (planned) | File-level copyleft; OK to embed |
| **OSL-3.0** | Form.io (if selected) | Treat like AGPL — separate container |

**Documentation requirement:** Every AGPL/GPL component must be listed
in this file plus in the per-repo `THIRD_PARTY_NOTICES.md` with the
exact container name and the upstream source URL.

---

## 4. Blocklist — never use

These licences are non-OSI-compatible. Components under them are
explicitly disallowed in any MoE repository:

- **Server Side Public License (SSPL)** — Elasticsearch ≥ 7.11, MongoDB, Redis Stack
- **Business Source License (BSL / BUSL 1.1)** — Redpanda, HashiCorp tooling ≥ 2023, Cockroach ≥ 23.1, Outline, MariaDB MaxScale
- **Elastic License v2 (ELv2)** — Airbyte ETL platform, post-2021 Logstash variants
- **Confluent Community License (CCL)** — `confluentinc/cp-*` images
- **Redis Source Available License v2 (RSALv2)** — Redis ≥ 7.4 (until 8.0)
- **Llama Community License** (only for MoE Sovereign itself; allowed inside operator deployments under operator responsibility)
- Any **"Source-Available"** licence without OSI approval

A component on this list must either be replaced with an OSI-compatible
fork, removed, or — if absolutely irreplaceable — explicitly exempted
in this page with a documented business reason and a sunset deadline.

---

## 5. Automated enforcement

`scripts/audit-licenses.sh` parses all `docker-compose*.yml` files and
`requirements*.txt` against this allow-/blocklist. Exit code 1 on any
hit. The script is wired into:

- Pre-commit gate (developer workflow)
- CI pipeline (every PR)
- Monthly drift report (cron — proposed for `moe-codex` Phase 2)

Run locally:

```bash
./scripts/audit-licenses.sh
```

---

## 6. Comparative-Advertising guidance

When positioning MoE Sovereign against proprietary platforms (Palantir
Foundry / Gotham / AIP / Apollo), the following constraints apply under
§ 6 of the German UWG (Act against Unfair Competition) and Art. 4 of EU
Comparative-Advertising Directive 2006/114/EC:

| ✅ Allowed | ❌ Forbidden |
|---|---|
| "Foundry-equivalent functionality" | "Foundry clone" |
| "Palantir-coverage table" (factual, verifiable) | Copy of Palantir UI screenshots / icons |
| "Open-source alternative" | "Better than Palantir" without measurable evidence |
| Naming our own products in Latin (`moe-libris`, `moe-codex`) | Using "Foundry", "Gotham", "AIP" or "Apollo" as repo or product names |
| Listing Palantir features in a comparison matrix | Asserting that Palantir does not work without evidence |

Comparative claims must be:
- Objectively verifiable (✅ — our comparison page does this)
- Not denigrating to the competitor (✅ — we name Palantir's strengths)
- Not creating likelihood of confusion (✅ — "alternative", not "clone")

---

## 7. EU AI Act readiness (Verordnung 2024/1689)

MoE Sovereign benefits from the **Open-Source-Privileg gemäß Art. 2(12) AIA** —
Free/Open-Source AI components under Apache/MIT licences are excluded from
many obligations *unless* they are placed on the market as a high-risk
system (Art. 6).

**Operator obligations** (not platform obligations):
- Risk-class assessment per use case
- DPIA under GDPR Art. 35 if personal data is processed
- Logging and traceability for high-risk deployments

These obligations are covered in dedicated pages:
- `docs/system/eu_sovereignty_charter.md` (sovereignty positioning)
- `moe-codex/docs/system/eu_ai_act_mapping.md` (use-case risk mapping — planned)
- `moe-codex/docs/system/dsgvo_dpia_template.md` (DPIA template — planned)

---

## 8. Maintenance contract

This page is the **single source of truth** for licence allow-/blocklist.

**Update triggers:**

- Any new container image added to `docker-compose*.yml` → add row to
  the per-licence section, run `audit-licenses.sh` to verify clean exit.
- Any upstream relicense announcement (e.g. Apache project moving to
  BSL) → add a Linux-Foundation-Fork entry and a deprecation path.
- Any new Python dependency in `requirements*.txt` → verify licence,
  add to allowlist coverage or extend the script.
- Any new repository under the `moe-*` family → update Scope section.

**Coverage discipline:** The lists in sections 2–4 must remain in sync
with the `BLOCK_IMAGES` / `BLOCK_PIP_PACKAGES` arrays in
`scripts/audit-licenses.sh`. A mismatch means the audit is silently
missing cases — treat as a bug.

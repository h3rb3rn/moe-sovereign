# E-2.5 Multi-Tenant Data Isolation

Owner: Platform Engineering / Security
Version: 2.0
Last verified: 2026-07-30
Level: Epic
Status: Planned

Parent: [I-2 Pipeline Quality-Gate Stack](../initiative.md)

## Outcome

Every data-plane read/write/tool action is scoped to an authenticated
principal and tenant. Missing or mismatched scope fails closed.

## Current evidence boundary

The runtime carries user-derived `tenant_ids` into selected GraphRAG and
memory paths, and gate ownership is enforced. This is useful preparatory
work, but it is not proof of full multi-tenant isolation. No complete
fail-closed contract is demonstrated across API auth, PostgreSQL, Valkey,
ChromaDB, Neo4j, caches, handovers, artifacts, feedback, and MCP tools.

## Required proof package

- authenticated principal and tenant context at API entry;
- no anonymous/default/global namespace fallback;
- storage/cache/retrieval/tool wrappers that require the scope;
- control-plane and data-plane authorization separation;
- audit records for rejected cross-tenant attempts without leaking object
  existence;
- migration plan for existing single-tenant data;
- negative E2E matrix covering two tenants and every persistence/tool layer.

## Exit criteria

- missing tenant context is rejected before pipeline entry;
- cross-tenant reads and writes fail closed at every named layer;
- parallel tenants cannot collide in keys, collections, graph scope,
  handovers, artifacts, or feedback;
- operator/admin access is explicit, least-privileged, and audited;
- backup/restore and migration preserve tenant boundaries.

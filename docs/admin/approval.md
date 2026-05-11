# Knowledge Approvals

> **Migration notice (2026-05-11):** Migrating to [`moe-codex`](https://github.com/h3rb3rn/moe-codex). New deployments use that repo.

The Approvals page (`/approval`) is a gated import workflow for external
knowledge bundles. Instead of writing extracted entities and relations
directly into Neo4j, a bundle is first **staged on a lakeFS branch** named
`pending/<tag>-<unix-timestamp>`. An administrator then reviews the bundle
and either **approves** (Neo4j import + lakeFS merge to `main`) or
**rejects** (branch delete, no Neo4j writes).

> **Foundry equivalence.** Branch-based approval mirrors Palantir Foundry's
> dataset-versioning workflow, where edits are scoped to a branch and merged
> after review. The implementation here uses lakeFS's native branch + merge
> semantics — no custom approval state machine.

## Lifecycle

```
1. POST /v1/graph/knowledge/import/pending
        │
        │  bundle JSON           Neo4j: untouched
        ▼
   pending/<tag>-<ts>            ◄─ lakeFS branch, head metadata holds the bundle
        │
        ├── /approval (UI) ──────────────────────────────────────────────────┐
        │                                                                    │
        ▼                                                                    │
   approve_branch()                                            reject_branch()
        │                                                                    │
        ├── Neo4j MERGE entities + relations                                  │
        ├── lakeFS merge into main                                            │
        ├── delete pending branch                              ◄── delete pending branch
        └── (Drift event recorded — see Data Health page)
```

## API

| Endpoint | Method | Auth | Behaviour |
|----------|--------|------|-----------|
| `/v1/graph/knowledge/import/pending` | POST | login | Stages bundle on `pending/<tag>-<ts>`. **No Neo4j writes.** |
| `/v1/graph/knowledge/approval/list` | GET | login | Returns pending branches with head metadata + commit timestamp |
| `/v1/graph/knowledge/approval/{branch:path}/approve` | POST | login (admin via `/api/approval/approve`) | Reads bundle from branch metadata → Neo4j MERGE → lakeFS merge → delete branch |
| `/v1/graph/knowledge/approval/{branch:path}/reject` | POST | login (admin via `/api/approval/reject`) | Deletes the branch. No Neo4j writes |

The admin-UI proxy endpoints `/api/approval/list`, `/api/approval/approve`,
and `/api/approval/reject` enforce `require_admin` before forwarding to the
orchestrator.

## Branch metadata

When `archive_to_branch()` commits a bundle, it stores the JSON payload as
commit metadata under the key `bundle`. The retrieval helper
`get_bundle_from_branch()` reads the head commit and parses this field, so
the bundle never has to be re-uploaded. The on-disk file in lakeFS object
storage acts as the persistent audit copy.

## Why a branch and not a Redis key?

| Concern | lakeFS branch | Redis key |
|---------|--------------|-----------|
| Audit trail | Commit history per branch + merge into main | None — overwritten on each save |
| Multi-bundle review | One branch per bundle, parallel | Race conditions across imports |
| Rollback | `lakefs revert` | Manual restore from backup |
| Storage | Object store (MinIO) | RAM |

## UI

- Pending branches are listed in a sortable table (newest first)
- Each row shows: tag · entity count · relation count · created-at · commit author
- **Approve** and **Reject** open a confirmation dialog; both actions require admin role
- After approval, the row disappears and a drift event for the import shows up on `/enterprise`

## Translations

`nav.approval`, `page.approval`, `btn.approve`, `btn.reject`,
`msg.approval_confirm_approve`, `msg.approval_confirm_reject` — present in
all four language files.

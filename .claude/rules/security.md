---
paths:
  - "services/auth.py"
  - "services/tenant.py"
  - "services/routing.py"
  - "services/dynamic_router.py"
  - "services/pipeline/**/*.py"
  - "routes/**/*.py"
  - "graph/**/*.py"
  - "mcp_server/**/*.py"
  - "admin_ui/**/*.py"
---

# Security boundary rules

- Re-read `PROJECT_COMPLIANCE.md` before changing a trust boundary.
- Add negative tests for missing identity, wrong owner/tenant, untrusted tool
  arguments, `local_only`, and dependency failure where applicable.
- Keep authorization server-side. UI visibility is not an authorization
  control.
- Never place a secret, raw prompt, or unrelated tenant payload in logs,
  exceptions, model context, audit metadata, or fixtures.
- Treat model, retrieval, MCP, and web output as tainted data until schema,
  scope, and provenance validation succeeds.

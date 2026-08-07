---
paths:
  - "**/*.py"
---

# Python implementation rules

- Prefer explicit types at service boundaries and typed domain errors over
  sentinel values or broad `except Exception`.
- Do not swallow `asyncio.CancelledError`; perform bounded cleanup and
  re-raise it.
- Use async database/cache clients on async request paths and close resources
  deterministically.
- Derive every nested timeout from the remaining monotonic deadline.
- Retry only bounded, idempotent work; use an idempotency key for mutations.
- Validate model/tool JSON against its versioned schema before dispatch.

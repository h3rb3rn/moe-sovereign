---
paths:
  - "tests/**/*.py"
  - "test_*.py"
  - "pytest.ini"
---

# Test rules

- Test observable contracts and failure semantics, not implementation trivia.
- Include negative cases for auth, tenant scope, schema, injection, timeout,
  cancellation, cleanup, and optional dependency degradation when relevant.
- Avoid real external network/model calls in unit tests; mark deliberate
  integration/E2E tests clearly.
- Tests must terminate cleanly without leaked tasks, threads, clients, or
  subprocesses.
- A function definition or mocked import is not reachability proof. Exercise
  the production entry point for end-to-end claims.

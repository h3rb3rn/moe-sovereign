---
paths:
  - "Dockerfile"
  - "docker-compose*.yml"
  - ".env.example"
  - "requirements*.txt"
  - "config.py"
  - "configs/**/*.yaml"
  - "configs/**/*.json"
---

# Deployment and configuration rules

- Keep base images and runtime dependencies exactly pinned and reproducible.
- Never add environment-specific server, model, token, or tenant defaults to
  source.
- Validate Compose/config/schema before rebuild. Recreate a service after an
  `env_file` change; restart is insufficient.
- Record source commit/snapshot and image digest before claiming deployment
  parity.
- Verify readiness and rollback after a deployment-affecting change.
- Do not deploy, publish, migrate, or push without explicit user
  authorization for that external state change.

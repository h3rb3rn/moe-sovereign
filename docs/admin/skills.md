# Skills Management

Skills are server-side slash commands (`/skill-name`) that expand into prompt snippets
before the request reaches the LLM. They are managed under **Admin → Tools → Skills**.

## Skill Sources (Three Tabs)

### Meine Skills (Local)

Admin-created or imported skills stored in `/app/skills/`. These are immediately available
for invocation once enabled.

- **Create**: Click **New Skill**, enter name (lowercase, hyphens only), description, and body
- **Edit / Delete**: Via the tile actions
- **Enable / Disable**: Toggle button on each tile

### Upstream (anthropics/skills)

Official Anthropic skills from [github.com/anthropics/skills](https://github.com/anthropics/skills).

- **Git Pull / Clone**: The **Git Pull** button clones the repository on a fresh installation
  (no `.git` present) or performs `git pull --ff-only` on subsequent calls
- **Audit required**: Every upstream skill must pass an internal security audit before
  the **Import** button becomes active
- **Audit**: Click the **Audit** button on a tile; the LLM security check runs and the tile
  updates with a verdict badge (Safe / Warning / Blocked)
- **Import**: Copies the audited skill to `/app/skills/` — blocked skills cannot be imported

### Community (skills.sh)

305+ community skills from [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills).

- **Pull Community Skills**: Clones the repository and copies all valid `SKILL.md` files
  to `/app/skills/community/`
- **Activate**: Copies an audited community skill to `/app/skills/` — requires a passed audit

## Audit System

### Internal LLM Audit

Applies to **both** upstream and community skills. An LLM (default: `phi4:14b`) checks for:

1. Shell command execution (`subprocess`, `os.system`, `exec`, `eval`)
2. Network access (HTTP, sockets, `curl`, `wget`)
3. File system writes outside the working directory
4. Prompt injection (system prompt overrides, role confusion)
5. Data exfiltration (sending data to external URLs)
6. Credential access (environment variables, config files)
7. Privilege escalation (`sudo`, `chmod`, `chown`)

**Verdicts:**

| Badge | Meaning |
|-------|---------|
| `Audited: Safe` (green) | No findings — can be imported/activated |
| `Audited: Warning` (yellow) | Minor findings — can still be imported, use judgment |
| `Blocked` (red) | Critical findings — import/activation is blocked |
| `Unaudited` (grey) | No audit run yet — import/activation not possible |

Audit reports are saved as `{name}.audit.json` alongside each skill file.

### External Audit (skills.sh)

Community skill tiles additionally show ratings scraped from [skills.sh/audits](https://skills.sh/audits):

| Badge | Source | Meaning |
|-------|--------|---------|
| **Gen:** Safe / unsafe | Gen Agent Trust Hub | Independent safety classification |
| **Socket:** N | Socket.dev | Number of security alerts detected |
| **Snyk:** Low/Med/High/Critical | Snyk | Dependency vulnerability risk level |

External audit data is cached for 24 hours. Use **"Ext. Audits aktualisieren"** to force a refresh.

> The internal LLM audit is **always required** regardless of the external rating.
> External badges are informational only.

## Hard-Lock Execution Gate

Skills must also be approved in the `skill_registry` database table before they can be
invoked. Every invocation attempt is logged to `skill_audit_log` with outcome
(`executed` / `blocked` / `error`). A skill that passes the file-level audit but is
not registry-approved remains blocked at invocation time.

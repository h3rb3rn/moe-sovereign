# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. See also `AGENTS.md` for the full system overview, permanent agent tasks, and coding conventions.

## Git-Push-Policy (verbindlich)

**Niemals direkt auf den `main`-Branch eines GitHub-Remotes pushen** — weder auf das
`github`-Remote dieses Repos (`git@github.com:h3rb3rn/moe-sovereign.git`) noch auf `main`
im separaten Publish-Repository (`/opt/deployment/Github/moe-sovereign`, siehe
`AGENTS.md` §4 GitOps-Workflow). Das gilt unabhängig davon, über welchen Pfad gepusht wird.

Stattdessen immer:
1. Feature-Branch erstellen (`git checkout -b <branch-name>`).
2. Commit auf dem Feature-Branch.
3. `git push origin/github <branch-name>` — **nicht** `main`.
4. Pull Request auf GitHub öffnen statt direkt zu mergen.

Das self-hosted `origin`-Remote (Gitea, `git.4noobs.de`) ist von dieser Regel nicht
automatisch ausgenommen — im Zweifel vor jedem Push auf `main` (gleich welches Remote)
beim Nutzer nachfragen.

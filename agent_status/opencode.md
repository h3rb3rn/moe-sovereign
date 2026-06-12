# Status Log — OpenCode

Append-only. Newest entry at the bottom. Never delete prior entries — they
are the resumability record for this protocol (see `AGENT_LASTENHEFT.md`
Section 0).

Each entry format:

```
## <UTC timestamp> — <TASK-ID> — <state>
Plan / progress:
- ...
Pre-conditions verified:
- ...
Notes:
- ...
```

`<state>` is one of: `starting`, `in_progress`, `checkpoint`, `blocked`, `done`, `aborted`.

---

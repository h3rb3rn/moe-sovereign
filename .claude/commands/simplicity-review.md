Review the current diff (or the files I name) against the MoE Sovereign
Complexity Decision Ladder.

For each finding, output exactly:
- **File:line** — what was found
- **Ladder step violated** — which of the 5 steps was skipped
- **Suggested simplification** — concrete alternative (one line if possible)
- **Exempt?** — yes/no and reason if yes

Format: bullet list only. No preamble, no summary.
If nothing is overengineered, output exactly: "No findings — ladder satisfied."

**The ladder:**
1. Is this necessary? (YAGNI)
2. Does the stdlib/FastAPI/Pydantic provide it?
3. Does an already-installed dependency cover it?
4. Can it be one line?
5. Is this the minimum that works?

**Always exempt — do not flag:**
- The inference fallback chain in `services/inference.py` (explicit architecture)
- The 14-package split from `main.py` (deliberate refactor, not overengineering)
- Anything in `docs/system/` (compliance docs require completeness)
- Any `local_only=True` enforcement logic (data sovereignty — non-negotiable)
- VRAM budget checks (hardware constraint — non-negotiable)
- Security invariants: timing-safe auth, SSRF protection, input validation
- Explicitly requested features

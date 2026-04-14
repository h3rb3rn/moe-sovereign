#!/usr/bin/env bash
# MoE Sovereign — Auto-Documentation Hook
# Triggered by Claude Code PostToolUse hook after Write/Edit operations.
# Captures enriched metadata (feature, reason, impact) from the tool context
# and appends a structured entry to changelog_pending.jsonl.
#
# SECURITY: Never records env values, passwords, tokens, server IPs, or model
# names sourced from environment variables. Only file paths and git context.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
DOCS_DIR="$PROJECT_DIR/docs"
SYNC_AGENT="$PROJECT_DIR/sync_agent.py"
PENDING_LOG="$DOCS_DIR/changelog_pending.jsonl"

# Read stdin (Claude Code hook provides JSON context)
INPUT=$(cat)

# ── Extract tool metadata ─────────────────────────────────────────────────────
TOOL_NAME=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_name', ''))
" 2>/dev/null || echo "")

TOOL_INPUT=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
inp = d.get('tool_input', {})
print(inp.get('file_path', inp.get('command', '')))
" 2>/dev/null || echo "")

# Only track Write/Edit — Bash is too broad
case "$TOOL_NAME" in
  Write|Edit|NotebookEdit) ;;
  *) exit 0 ;;
esac

# Only track source files that affect behaviour or documentation
case "$TOOL_INPUT" in
  # Skip: generated files, secrets, scripts themselves, temp files
  */docs/*|*/promts/*|*\.env|*/userdb/*|*/\.claude/*|*.tmp|*.pyc|*__pycache__*) exit 0 ;;
  # Skip: the hook script itself (would cause infinite loops)
  */scripts/*) exit 0 ;;
  # Track: source files with meaningful changes
  *main.py|*admin_ui/*|*graph_rag/*|*mcp_server/*|*docker-compose*|*Dockerfile*) ;;
  *sync_agent.py|*mkdocs.yml|*complexity_estimator.py|*self_correction.py) ;;
  *) exit 0 ;;
esac

# ── Determine component section ───────────────────────────────────────────────
if [[ "$TOOL_INPUT" == *"admin_ui"* ]]; then
  SECTION="Admin UI"
elif [[ "$TOOL_INPUT" == *"graph_rag"* ]]; then
  SECTION="GraphRAG"
elif [[ "$TOOL_INPUT" == *"mcp_server"* ]]; then
  SECTION="MCP Tools"
elif [[ "$TOOL_INPUT" == *"main.py"* ]]; then
  SECTION="Orchestrator"
elif [[ "$TOOL_INPUT" == *"docker-compose"* || "$TOOL_INPUT" == *"Dockerfile"* ]]; then
  SECTION="Infrastructure"
elif [[ "$TOOL_INPUT" == *"sync_agent"* ]]; then
  SECTION="Docs Sync"
elif [[ "$TOOL_INPUT" == *"mkdocs"* ]]; then
  SECTION="Documentation"
elif [[ "$TOOL_INPUT" == *"complexity_estimator"* ]]; then
  SECTION="Orchestrator"
elif [[ "$TOOL_INPUT" == *"self_correction"* ]]; then
  SECTION="Orchestrator"
else
  SECTION="System"
fi

# ── Collect git context (safe — no secrets in git log) ───────────────────────
GIT_BRANCH=$(cd "$PROJECT_DIR" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_HASH=$(cd "$PROJECT_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# Lines changed (gives a rough sense of impact size)
LINES_CHANGED="unknown"
if [[ -f "$TOOL_INPUT" ]]; then
  LINES_CHANGED=$(cd "$PROJECT_DIR" && git diff --shortstat HEAD -- "$TOOL_INPUT" 2>/dev/null | \
    grep -oP '\d+ (insertion|deletion)' | awk '{sum += $1} END {print sum+0}' || echo "0")
fi

# ── Infer impact from file + change size ─────────────────────────────────────
IMPACT="patch"
if [[ "$SECTION" == "Orchestrator" || "$SECTION" == "GraphRAG" || "$SECTION" == "Infrastructure" ]]; then
  if [[ "${LINES_CHANGED:-0}" -gt 50 ]] 2>/dev/null; then
    IMPACT="minor"
  fi
  if [[ "${LINES_CHANGED:-0}" -gt 200 ]] 2>/dev/null; then
    IMPACT="major"
  fi
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE=$(date +"%Y-%m-%d")
FILE_BASENAME=$(basename "$TOOL_INPUT")

# ── Write structured pending entry ───────────────────────────────────────────
python3 - << PYEOF
import json, os

entry = {
    "date":          "$DATE",
    "timestamp":     "$TIMESTAMP",
    "section":       "$SECTION",
    "file":          os.path.relpath("$TOOL_INPUT", "$PROJECT_DIR"),
    "file_basename": "$FILE_BASENAME",
    "tool":          "$TOOL_NAME",
    "impact":        "$IMPACT",
    "git_branch":    "$GIT_BRANCH",
    "git_hash":      "$GIT_HASH",
    "lines_changed": "$LINES_CHANGED",
}

pending = "$PENDING_LOG"
os.makedirs(os.path.dirname(pending), exist_ok=True)
with open(pending, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PYEOF

# Flush pending entries into changelog (non-blocking, best-effort)
DOCS_DIR="$DOCS_DIR" python3 "$SYNC_AGENT" --changelog-only 2>/dev/null || true

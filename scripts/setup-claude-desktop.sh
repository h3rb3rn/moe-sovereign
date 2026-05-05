#!/usr/bin/env bash
# setup-claude-desktop.sh
# Configures Claude Desktop to use MoE Sovereign as a third-party inference gateway.
#
# What this does:
#   1. Locates claude_desktop_config.json on macOS / Linux / Windows (WSL)
#   2. Creates a backup before modifying
#   3. Merges the MoE gateway block into the existing config (preserves other settings)
#   4. Prints a summary and next steps
#
# Usage:
#   ./scripts/setup-claude-desktop.sh
#   MOE_URL=https://moe.example.com MOE_API_KEY=sk-... ./scripts/setup-claude-desktop.sh

set -euo pipefail

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
die()  { echo -e "${RED}✗${RESET}  $*" >&2; exit 1; }

# ─── Locate claude_desktop_config.json ───────────────────────────────────────
_find_config() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
    elif grep -qi microsoft /proc/version 2>/dev/null; then
        # WSL — point into Windows home
        WIN_HOME=$(wslpath "$(cmd.exe /C "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')" 2>/dev/null || true)
        if [[ -n "$WIN_HOME" ]]; then
            echo "$WIN_HOME/AppData/Roaming/Claude/claude_desktop_config.json"
        else
            echo "$HOME/.config/Claude/claude_desktop_config.json"
        fi
    else
        echo "$HOME/.config/Claude/claude_desktop_config.json"
    fi
}

CONFIG_PATH="${CLAUDE_DESKTOP_CONFIG:-$(_find_config)}"

# ─── Prompt for connection details ───────────────────────────────────────────
echo ""
echo -e "${BOLD}MoE Sovereign — Claude Desktop Setup${RESET}"
echo "─────────────────────────────────────"
echo ""

if [[ -z "${MOE_URL:-}" ]]; then
    read -rp "  MoE Sovereign URL  [e.g. https://moe.example.com]: " MOE_URL
fi
MOE_URL="${MOE_URL%/}"  # strip trailing slash

if [[ -z "${MOE_URL}" ]]; then
    die "No URL provided."
fi

if [[ -z "${MOE_API_KEY:-}" ]]; then
    read -rsp "  API Key: " MOE_API_KEY
    echo ""
fi

if [[ -z "${MOE_API_KEY}" ]]; then
    die "No API key provided."
fi

# ─── Verify connectivity (optional, best-effort) ─────────────────────────────
echo ""
echo "  Checking connectivity…"
if command -v curl &>/dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "x-api-key: ${MOE_API_KEY}" \
        "${MOE_URL}/v1/models" --max-time 5 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
        ok "Gateway reachable (HTTP 200)"
    elif [[ "$HTTP_CODE" == "401" ]]; then
        warn "Gateway reachable but API key rejected (HTTP 401) — double-check the key"
    elif [[ "$HTTP_CODE" == "000" ]]; then
        warn "Could not reach ${MOE_URL} — continuing anyway (you can test later)"
    else
        warn "Unexpected HTTP ${HTTP_CODE} from /v1/models — continuing anyway"
    fi
else
    warn "curl not found — skipping connectivity check"
fi

# ─── Merge into claude_desktop_config.json ───────────────────────────────────
mkdir -p "$(dirname "$CONFIG_PATH")"

# Read existing config or start with empty object
if [[ -f "$CONFIG_PATH" ]]; then
    EXISTING=$(cat "$CONFIG_PATH")
    # Backup
    BACKUP="${CONFIG_PATH}.bak.$(date +%Y%m%d_%H%M%S)"
    cp "$CONFIG_PATH" "$BACKUP"
    ok "Backup created: ${BACKUP}"
else
    EXISTING="{}"
    warn "No existing config found — creating new one at: ${CONFIG_PATH}"
fi

# Use python3 to merge (available on macOS/Linux/WSL)
if ! command -v python3 &>/dev/null; then
    die "python3 is required but not found. Install Python 3 and re-run."
fi

python3 - <<PYEOF
import json, sys

existing = json.loads('''${EXISTING}'''.replace("'", "\\'")) if '''${EXISTING}''' else {}

# Gateway block — Anthropic Third-Party Inference spec
gateway = {
    "type":    "gateway",
    "baseUrl": "${MOE_URL}",
    "apiKey":  "${MOE_API_KEY}",
}

existing["thirdPartyInference"] = gateway

print(json.dumps(existing, indent=2, ensure_ascii=False))
PYEOF

# Write merged config
python3 - > "$CONFIG_PATH" <<PYEOF
import json, sys

try:
    existing = json.loads(open("${CONFIG_PATH}").read()) if open("${CONFIG_PATH}").read().strip() != "" else {}
except Exception:
    existing = {}

existing_raw = '''${EXISTING}'''.replace("'", "\\'")
import ast
try:
    base = json.loads(existing_raw)
except Exception:
    base = {}

base["thirdPartyInference"] = {
    "type":    "gateway",
    "baseUrl": "${MOE_URL}",
    "apiKey":  "${MOE_API_KEY}",
}

print(json.dumps(base, indent=2, ensure_ascii=False))
PYEOF

ok "Config written: ${CONFIG_PATH}"

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Done!${RESET} Restart Claude Desktop to apply the changes."
echo ""
echo "  In Claude Desktop:"
echo "    Developer → Configure Third-Party Inference → Gateway"
echo "    → Base URL : ${MOE_URL}"
echo "    → The gateway will also appear automatically in the /model picker"
echo "      (models whose ID starts with 'claude-' are shown as 'From gateway')"
echo ""
echo "  To use in Claude Code:"
echo "    export ANTHROPIC_BASE_URL=${MOE_URL}"
echo "    export ANTHROPIC_API_KEY=${MOE_API_KEY}"
echo ""

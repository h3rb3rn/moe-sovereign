#!/usr/bin/env bash
# =============================================================================
#  bootstrap-macos.sh — macOS Docker Desktop quick-start
#
#  Generates an .env file with sane defaults + random secrets, creates the
#  required host directories under $HOME, and prints the next steps.
#  Run this once before "docker compose up -d" on macOS.
#
#  Usage: bash scripts/bootstrap-macos.sh
# =============================================================================
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[ERROR] This bootstrap is for macOS only. On Linux/WSL run install.sh."
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
MOE_DATA_ROOT_DEFAULT="${HOME}/moe-data"
GRAFANA_DATA_ROOT_DEFAULT="${HOME}/moe-grafana"

echo "════════════════════════════════════════════════════"
echo "  MoE Sovereign — macOS bootstrap"
echo "════════════════════════════════════════════════════"
echo ""

# Preserve existing values across re-runs.
read_env() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-
}

EXISTING_ADMIN_USER=$(read_env ADMIN_USER)
EXISTING_ADMIN_PASSWORD=$(read_env ADMIN_PASSWORD)
EXISTING_ADMIN_SECRET=$(read_env ADMIN_SECRET_KEY)
EXISTING_REDIS_PASS=$(read_env REDIS_PASSWORD)
EXISTING_NEO4J_PASS=$(read_env NEO4J_PASS)
EXISTING_GRAFANA_PASS=$(read_env GF_SECURITY_ADMIN_PASSWORD)
EXISTING_PG_LANGGRAPH_PASS=$(read_env POSTGRES_CHECKPOINT_PASSWORD)
EXISTING_PG_USERDB_PASS=$(read_env MOE_USERDB_PASSWORD)
EXISTING_DATA_ROOT=$(read_env MOE_DATA_ROOT)
EXISTING_GRAFANA_ROOT=$(read_env GRAFANA_DATA_ROOT)

if [[ -f "$ENV_FILE" ]]; then
  echo "  [!] Existing .env detected — preserving secrets and host roots."
  cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
fi

ADMIN_USER="${EXISTING_ADMIN_USER:-admin}"
read -p "  Admin username [$ADMIN_USER]: " input || true
ADMIN_USER="${input:-$ADMIN_USER}"

if [[ -n "$EXISTING_ADMIN_PASSWORD" ]]; then
  ADMIN_PASSWORD="$EXISTING_ADMIN_PASSWORD"
  echo "  Admin password: (kept from previous .env)"
else
  while true; do
    read -rsp "  Admin password (will be hashed for Dozzle): " ADMIN_PASSWORD
    echo ""
    if [[ -z "$ADMIN_PASSWORD" ]]; then
      echo "  [!] required"
      continue
    fi
    break
  done
fi

MOE_DATA_ROOT="${EXISTING_DATA_ROOT:-$MOE_DATA_ROOT_DEFAULT}"
GRAFANA_DATA_ROOT="${EXISTING_GRAFANA_ROOT:-$GRAFANA_DATA_ROOT_DEFAULT}"

# Auto-fill missing secrets
GEN_ADMIN_SECRET="${EXISTING_ADMIN_SECRET:-$(openssl rand -hex 32)}"
GEN_REDIS_PASS="${EXISTING_REDIS_PASS:-$(openssl rand -hex 16)}"
GEN_NEO4J_PASS="${EXISTING_NEO4J_PASS:-$(openssl rand -hex 16)}"
GEN_GRAFANA_PASS="${EXISTING_GRAFANA_PASS:-$(openssl rand -hex 12)}"
GEN_PG_LANGGRAPH_PASS="${EXISTING_PG_LANGGRAPH_PASS:-$(openssl rand -hex 16)}"
GEN_PG_USERDB_PASS="${EXISTING_PG_USERDB_PASS:-$(openssl rand -hex 16)}"

echo ""
echo "  Creating host directories under ${MOE_DATA_ROOT} and ${GRAFANA_DATA_ROOT}..."
mkdir -p \
  "${MOE_DATA_ROOT}/kafka-data" \
  "${MOE_DATA_ROOT}/neo4j-data" \
  "${MOE_DATA_ROOT}/neo4j-logs" \
  "${MOE_DATA_ROOT}/agent-logs" \
  "${MOE_DATA_ROOT}/chroma-onnx-cache" \
  "${MOE_DATA_ROOT}/chroma-data" \
  "${MOE_DATA_ROOT}/redis-data" \
  "${MOE_DATA_ROOT}/prometheus-data" \
  "${MOE_DATA_ROOT}/admin-logs" \
  "${MOE_DATA_ROOT}/userdb" \
  "${MOE_DATA_ROOT}/few-shot" \
  "${MOE_DATA_ROOT}/generated" \
  "${MOE_DATA_ROOT}/langgraph-checkpoints" \
  "${GRAFANA_DATA_ROOT}/data" \
  "${GRAFANA_DATA_ROOT}/dashboards"

echo "  Writing ${ENV_FILE}..."
{
  echo "# MoE Sovereign — macOS bootstrap output"
  echo "# Generated $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo ""
  echo "MOE_DATA_ROOT=${MOE_DATA_ROOT}"
  echo "GRAFANA_DATA_ROOT=${GRAFANA_DATA_ROOT}"
  echo ""
  echo "ADMIN_USER=${ADMIN_USER}"
  echo "ADMIN_PASSWORD=${ADMIN_PASSWORD}"
  echo "ADMIN_SECRET_KEY=${GEN_ADMIN_SECRET}"
  echo ""
  echo "REDIS_PASSWORD=${GEN_REDIS_PASS}"
  echo "REDIS_URL=redis://:${GEN_REDIS_PASS}@terra_cache:6379/0"
  echo "NEO4J_PASS=${GEN_NEO4J_PASS}"
  echo "POSTGRES_CHECKPOINT_PASSWORD=${GEN_PG_LANGGRAPH_PASS}"
  echo "MOE_USERDB_PASSWORD=${GEN_PG_USERDB_PASS}"
  echo "GF_SECURITY_ADMIN_USER=admin"
  echo "GF_SECURITY_ADMIN_PASSWORD=${GEN_GRAFANA_PASS}"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo ""
echo "════════════════════════════════════════════════════"
echo "  ✓ .env written ($(wc -l < "$ENV_FILE") lines)"
echo "  ✓ host directories created"
echo ""
echo "  [!] Docker Desktop must allow these paths in File Sharing:"
echo "        ${MOE_DATA_ROOT}"
echo "        ${GRAFANA_DATA_ROOT}"
echo "      (Settings → Resources → File Sharing → +  → Apply & Restart)"
echo ""
echo "  Then start the stack:"
echo "    docker compose up -d"
echo ""
echo "  Admin UI:  http://localhost:8088   (user: ${ADMIN_USER})"
echo "  Grafana:   http://localhost:3001   (admin / printed below)"
echo "  Dozzle:    http://localhost:9999"
echo ""
echo "  Grafana admin password: ${GEN_GRAFANA_PASS}"
echo "════════════════════════════════════════════════════"

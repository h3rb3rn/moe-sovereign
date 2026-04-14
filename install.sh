#!/usr/bin/env bash
# =============================================================================
#  MoE Sovereign — One-Line Installer
#  Usage: curl -sSL https://moe-sovereign.org/install.sh | bash
#         or: bash install.sh
#
#  Supported OS: Debian 11 (bullseye), 12 (bookworm), 13 (trixie)
#  Requires: root / sudo
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# --- Configurable defaults (override via environment) -----------------------
MOE_REPO_URL="${MOE_REPO_URL:-https://github.com/h3rb3rn/moe-sovereign.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/moe-sovereign}"
MOE_ENV_FILE="${INSTALL_DIR}/.env"

# =============================================================================
#  SECTION 1: Banner
# =============================================================================
print_banner() {
  cat <<'EOF'

  ███╗   ███╗ ██████╗ ███████╗    ███████╗ ██████╗ ██╗   ██╗███████╗██████╗
  ████╗ ████║██╔═══██╗██╔════╝    ██╔════╝██╔═══██╗██║   ██║██╔════╝██╔══██╗
  ██╔████╔██║██║   ██║█████╗      ███████╗██║   ██║██║   ██║█████╗  ██████╔╝
  ██║╚██╔╝██║██║   ██║██╔══╝      ╚════██║██║   ██║╚██╗ ██╔╝██╔══╝  ██╔══██╗
  ██║ ╚═╝ ██║╚██████╔╝███████╗    ███████║╚██████╔╝ ╚████╔╝ ███████╗██║  ██║
  ╚═╝     ╚═╝ ╚═════╝ ╚══════╝    ╚══════╝ ╚═════╝   ╚═══╝  ╚══════╝╚═╝  ╚═╝

           Sovereign Mixture-of-Experts Orchestrator — Installer
  ==========================================================================

EOF
}

print_banner

# =============================================================================
#  SECTION 1b: Re-run detection
# =============================================================================
# If .env and data volumes already exist, warn and offer the safe path.
# Re-running without preservation would regenerate Postgres/Neo4j/Redis
# passwords while the existing volumes keep the old credentials → every
# dependent service (moe-admin, langgraph-app) dies with auth errors.
# This early warning gives the operator a chance to abort.
if [[ -f "${MOE_ENV_FILE}" ]] && command -v docker >/dev/null 2>&1; then
  vol_count=$(docker volume ls -q 2>/dev/null | grep -cE '(terra_checkpoints|neo4j|redis|cache|grafana)' || true)
  if [[ "${vol_count:-0}" -gt 0 ]]; then
    echo ""
    echo "  [!] RE-RUN DETECTED"
    echo "      Found existing .env at ${MOE_ENV_FILE}"
    echo "      and ${vol_count} data volume(s) from a previous install."
    echo ""
    echo "      This installer will PRESERVE all infrastructure secrets"
    echo "      (Postgres, Neo4j, Redis, Grafana) from your existing .env"
    echo "      so that services stay compatible with their data volumes."
    echo ""
    echo "      Only admin user/password/domain will be re-prompted."
    echo ""
    if [[ -t 0 ]] || [[ -r /dev/tty ]]; then
      read -rp "  Continue? [Y/n] " _rerun_ok < /dev/tty || _rerun_ok="y"
      case "${_rerun_ok:-y}" in
        [nN]|[nN][oO])
          echo "  Aborted by user."
          exit 0
          ;;
      esac
    fi
  fi
fi

# =============================================================================
#  SECTION 2: Root check
# =============================================================================
if [[ $EUID -ne 0 ]]; then
  echo "[ERROR] This installer must be run as root or via sudo."
  echo "        Re-run with: sudo bash install.sh"
  exit 1
fi

# =============================================================================
#  SECTION 3: OS detection
# =============================================================================
echo "[1/9] Detecting operating system..."

if [[ ! -f /etc/os-release ]]; then
  echo "[ERROR] /etc/os-release not found. Cannot detect OS."
  exit 1
fi

source /etc/os-release

if [[ "${ID:-}" != "debian" ]]; then
  echo "[ERROR] Unsupported OS: ${PRETTY_NAME:-unknown}."
  echo "        MoE Sovereign currently supports Debian 11, 12, and 13 only."
  exit 1
fi

VERSION_MAJOR="${VERSION_ID%%.*}"
case "$VERSION_MAJOR" in
  11) VERSION_CODENAME="bullseye" ;;
  12) VERSION_CODENAME="bookworm" ;;
  13) VERSION_CODENAME="trixie"   ;;
  *)
    echo "[ERROR] Unsupported Debian version: ${VERSION_ID}."
    echo "        Supported: Debian 11 (bullseye), 12 (bookworm), 13 (trixie)."
    exit 1
    ;;
esac

echo "  OS: ${PRETTY_NAME} (${VERSION_CODENAME}) ✓"

# =============================================================================
#  SECTION 4: Docker detection
# =============================================================================
echo "[2/9] Checking for Docker..."

DOCKER_FOUND=false
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  DOCKER_VERSION=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
  echo "  Docker ${DOCKER_VERSION} already installed ✓ — skipping installation."
  DOCKER_FOUND=true
fi

# =============================================================================
#  SECTION 5: Docker CE installation (if not already present)
# =============================================================================
if [[ "$DOCKER_FOUND" != "true" ]]; then
  echo "[3/9] Installing Docker CE..."

  apt-get update -qq
  apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release git apache2-utils python3

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -qq
  apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

  systemctl enable --now docker
  echo "  Docker CE installed and started ✓"
else
  echo "[3/9] Installing required system packages..."
  apt-get install -y --no-install-recommends git apache2-utils python3 -qq
fi

# =============================================================================
#  SECTION 6: Host directory creation
# =============================================================================
echo "[4/9] Creating host directories..."

# MOE_DATA_ROOT is where bind-mounted volumes live. Default is /opt/moe-infra
# on Linux; on macOS Docker Desktop /opt is not in the default File Sharing
# list, so a user-owned home subdirectory is safer there.
case "$(uname -s)" in
  Darwin)
    MOE_DATA_ROOT_DEFAULT="${HOME}/moe-data"
    GRAFANA_DATA_ROOT_DEFAULT="${HOME}/moe-grafana"
    ;;
  *)
    MOE_DATA_ROOT_DEFAULT="/opt/moe-infra"
    GRAFANA_DATA_ROOT_DEFAULT="/opt/grafana"
    ;;
esac
# Preserve existing host roots from a previous install — moving them
# would orphan the data volumes. Read directly from .env (the full
# preservation block runs later, but we need these values now).
if [[ -f "${MOE_ENV_FILE}" ]]; then
  _prev_data=$(grep -E '^MOE_DATA_ROOT=' "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-)
  _prev_graf=$(grep -E '^GRAFANA_DATA_ROOT=' "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-)
  [[ -n "$_prev_data" ]] && MOE_DATA_ROOT_DEFAULT="$_prev_data"
  [[ -n "$_prev_graf" ]] && GRAFANA_DATA_ROOT_DEFAULT="$_prev_graf"
fi
MOE_DATA_ROOT="${MOE_DATA_ROOT:-$MOE_DATA_ROOT_DEFAULT}"
GRAFANA_DATA_ROOT="${GRAFANA_DATA_ROOT:-$GRAFANA_DATA_ROOT_DEFAULT}"

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
  "${GRAFANA_DATA_ROOT}/data" \
  "${GRAFANA_DATA_ROOT}/dashboards" \
  /opt/moe-sovereign 2>/dev/null || true

echo "  Host directories created at ${MOE_DATA_ROOT} ✓"

# Fix ownership for containers that run as non-root.
# kafka (confluentinc/cp-kafka): appuser uid=1000
chown -R 1000:1000 "${MOE_DATA_ROOT}/kafka-data" 2>/dev/null || true
# prometheus: runs as nobody uid=65534
chown -R 65534:65534 "${MOE_DATA_ROOT}/prometheus-data" 2>/dev/null || true
# langgraph-orchestrator + mcp-precision: moe uid=1001 gid=0
chown -R 1001:0 "${MOE_DATA_ROOT}/agent-logs" 2>/dev/null || true
# grafana: uid=472
chown -R 472:472 "${GRAFANA_DATA_ROOT}/data" "${GRAFANA_DATA_ROOT}/dashboards" 2>/dev/null || true

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo ""
  echo "  [!] macOS: Docker Desktop must allow ${MOE_DATA_ROOT} for bind"
  echo "      mounts. Open Docker Desktop → Settings → Resources →"
  echo "      File Sharing and add this path if it is not already listed."
  echo ""
fi

# =============================================================================
#  SECTION 7: Repo clone or update
# =============================================================================
echo "[5/9] Setting up MoE Sovereign repository..."

if [[ -d "${INSTALL_DIR}/.git" ]]; then
  echo "  Existing installation found — pulling latest changes..."
  git -C "${INSTALL_DIR}" pull --ff-only
else
  echo "  Cloning repository to ${INSTALL_DIR}..."
  git clone "${MOE_REPO_URL}" "${INSTALL_DIR}"
fi

echo "  Repository ready ✓"

# =============================================================================
#  SECTION 8: Interactive .env generation
# =============================================================================

# When running via pipe (curl | bash), stdin is the script itself.
# We must NOT use exec < /dev/tty here because that would prevent bash
# from reading the rest of the script. Instead, read from /dev/tty
# explicitly in prompt_input().

echo ""
echo "=========================================================================="
echo "  Configuration"
echo "=========================================================================="
echo ""
echo "  The following questions configure your MoE Sovereign instance."
echo "  Press ENTER to accept a default value [shown in brackets]."
echo "  Inference server (LLM API) configuration is done via the web wizard"
echo "  after installation."
echo ""

# Pre-initialize variables that will be set by prompt_input (prevents nounset errors)
ADMIN_USER=""
ADMIN_PASSWORD=""
DOMAIN=""

# Helper: prompt with optional default
prompt_input() {
  local varname="$1"
  local prompt_text="$2"
  local default_val="${3:-}"
  local is_secret="${4:-false}"
  local required="${5:-true}"

  # Ensure the variable exists (prevents nounset if read fails)
  declare -g "${varname}=${default_val}"

  local display_default=""
  if [[ -n "$default_val" ]]; then
    if [[ "$is_secret" == "true" ]]; then
      display_default=" [auto-generated]"
    else
      display_default=" [${default_val}]"
    fi
  fi

  while true; do
    if [[ "$is_secret" == "true" ]]; then
      read -rsp "  ${prompt_text}${display_default}: " input_val < /dev/tty
      echo ""
    else
      read -rp "  ${prompt_text}${display_default}: " input_val < /dev/tty
    fi

    if [[ -z "$input_val" ]]; then
      input_val="$default_val"
    fi

    if [[ -z "$input_val" && "$required" == "true" ]]; then
      echo "  [!] This field is required."
    else
      break
    fi
  done

  printf -v "${varname}" '%s' "${input_val}"
}

# Preserve infrastructure secrets across re-runs.
# Re-running install.sh must never desync from existing data volumes:
# Postgres, Neo4j, Redis and Grafana persist their credentials on first
# init. Regenerating the .env would lock every service out of its volume.
# Strategy: read existing secrets from .env (or backup) and only generate
# new values for fields that are still missing.
EXISTING_ADMIN_SECRET=""
EXISTING_REDIS_PASS=""
EXISTING_NEO4J_PASS=""
EXISTING_GRAFANA_PASS=""
EXISTING_PG_LANGGRAPH_PASS=""
EXISTING_PG_USERDB_PASS=""
EXISTING_ADMIN_USER=""
EXISTING_ADMIN_PASSWORD=""
EXISTING_DOMAIN=""

if [[ -f "${MOE_ENV_FILE}" ]]; then
  echo ""
  echo "  [!] Existing .env detected at ${MOE_ENV_FILE}"
  echo "      Infrastructure secrets will be PRESERVED to keep data"
  echo "      volumes (Postgres, Neo4j, Redis) accessible."
  echo ""
  # Source existing values without executing the file (grep + cut is safer)
  read_env() {
    local key="$1"
    grep -E "^${key}=" "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-
  }
  EXISTING_ADMIN_SECRET=$(read_env ADMIN_SECRET_KEY)
  EXISTING_REDIS_PASS=$(read_env REDIS_PASSWORD)
  EXISTING_NEO4J_PASS=$(read_env NEO4J_PASS)
  EXISTING_GRAFANA_PASS=$(read_env GF_SECURITY_ADMIN_PASSWORD)
  EXISTING_PG_LANGGRAPH_PASS=$(read_env POSTGRES_CHECKPOINT_PASSWORD)
  EXISTING_PG_USERDB_PASS=$(read_env MOE_USERDB_PASSWORD)
  EXISTING_ADMIN_USER=$(read_env ADMIN_USER)
  EXISTING_ADMIN_PASSWORD=$(read_env ADMIN_PASSWORD)
  EXISTING_DOMAIN=$(read_env DOMAIN)
fi

# Generate new secrets only where none exist yet
GEN_ADMIN_SECRET="${EXISTING_ADMIN_SECRET:-$(openssl rand -hex 32)}"
GEN_REDIS_PASS="${EXISTING_REDIS_PASS:-$(openssl rand -hex 16)}"
GEN_NEO4J_PASS="${EXISTING_NEO4J_PASS:-$(openssl rand -hex 16)}"
GEN_GRAFANA_PASS="${EXISTING_GRAFANA_PASS:-$(openssl rand -hex 12)}"
GEN_PG_LANGGRAPH_PASS="${EXISTING_PG_LANGGRAPH_PASS:-$(openssl rand -hex 16)}"
GEN_PG_USERDB_PASS="${EXISTING_PG_USERDB_PASS:-$(openssl rand -hex 16)}"

echo "  --- Admin Account ---"
prompt_input ADMIN_USER     "Admin username"   "${EXISTING_ADMIN_USER:-admin}"
if [[ -n "$EXISTING_ADMIN_PASSWORD" ]]; then
  prompt_input ADMIN_PASSWORD "Admin password (ENTER keeps existing)" "${EXISTING_ADMIN_PASSWORD}" "true" "false"
else
  prompt_input ADMIN_PASSWORD "Admin password (choose a strong password)" "" "true"
fi

# Validate password was provided
if [[ -z "$ADMIN_PASSWORD" ]]; then
  echo "[ERROR] Admin password is required."
  exit 1
fi

echo ""
echo "  --- Domain Configuration ---"
prompt_input DOMAIN "Your public domain (e.g. moe-sovereign.org), or leave blank for local use" "${EXISTING_DOMAIN:-}" "false" "false"

# Build public URLs from domain
if [[ -n "$DOMAIN" ]]; then
  DEFAULT_BASE_URL="https://${DOMAIN}:8088"
  DEFAULT_ADMIN_URL="https://admin.${DOMAIN}"
  DEFAULT_API_URL="https://api.${DOMAIN}"
else
  DEFAULT_BASE_URL="http://localhost:8088"
  DEFAULT_ADMIN_URL=""
  DEFAULT_API_URL=""
fi

echo ""
if [[ -n "$EXISTING_REDIS_PASS" ]]; then
  echo "  --- Infrastructure secrets (preserved from existing .env) ---"
  echo "  Admin secret key:  [kept]"
  echo "  Redis password:    [kept]"
  echo "  Neo4j password:    [kept]"
  echo "  Grafana password:  [kept]"
  echo "  Postgres passwords: [kept]"
else
  echo "  --- Auto-generated secrets (saved to .env) ---"
  echo "  Admin secret key:  [auto]"
  echo "  Redis password:    [auto]"
  echo "  Neo4j password:    [auto]"
  echo "  Grafana password:  [auto]"
fi
echo ""

# =============================================================================
#  SECTION 9: Dozzle users file (generated automatically on first compose start)
# =============================================================================
# The moe-dozzle-init container reads ADMIN_USER / ADMIN_PASSWORD from .env
# and generates /opt/moe-infra/dozzle-users.yml with a bcrypt hash on startup.
# No manual step required here.
echo "[6/9] Dozzle credentials: generated by moe-dozzle-init on first start ✓"

# =============================================================================
#  SECTION 10: Write .env
# =============================================================================
echo "[7/9] Writing configuration to ${MOE_ENV_FILE}..."

# If an existing .env exists, back it up
if [[ -f "${MOE_ENV_FILE}" ]]; then
  cp "${MOE_ENV_FILE}" "${MOE_ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
  echo "  Existing .env backed up ✓"
fi

# Write .env using printf to safely handle passwords with special characters
# (single quotes, dollar signs, backticks etc. are not interpreted).
{
  echo "# MoE Sovereign — Runtime Configuration"
  echo "# Generated by install.sh on $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "# Edit this file or use the Admin UI to change settings."
  echo "# After changes: sudo docker compose up -d"
  echo ""
  echo "# --- Host data roots (bind-mount sources) ---"
  printf 'MOE_DATA_ROOT=%s\n'            "${MOE_DATA_ROOT}"
  printf 'GRAFANA_DATA_ROOT=%s\n'        "${GRAFANA_DATA_ROOT}"
  printf 'FEW_SHOT_HOST_DIR=%s\n'        "${MOE_DATA_ROOT}/few-shot"
  echo ""
  echo "# --- Authentication (Admin UI) ---"
  printf 'ADMIN_USER=%s\n'               "${ADMIN_USER}"
  printf 'ADMIN_PASSWORD=%s\n'           "${ADMIN_PASSWORD}"
  printf 'ADMIN_SECRET_KEY=%s\n'         "${GEN_ADMIN_SECRET}"
  echo ""
  echo "# --- Infrastructure Passwords ---"
  printf 'REDIS_PASSWORD=%s\n'           "${GEN_REDIS_PASS}"
  printf 'REDIS_URL=redis://:%s@terra_cache:6379/0\n' "${GEN_REDIS_PASS}"
  printf 'NEO4J_PASS=%s\n'              "${GEN_NEO4J_PASS}"
  echo 'NEO4J_URI=bolt://neo4j-knowledge:7687'
  echo 'NEO4J_USER=neo4j'
  echo ""
  echo "# --- PostgreSQL ---"
  printf 'POSTGRES_CHECKPOINT_PASSWORD=%s\n' "${GEN_PG_LANGGRAPH_PASS}"
  printf 'POSTGRES_CHECKPOINT_URL=postgresql://langgraph:%s@terra_checkpoints:5432/langgraph\n' "${GEN_PG_LANGGRAPH_PASS}"
  printf 'MOE_USERDB_URL=postgresql://moe_admin:%s@terra_checkpoints:5432/moe_userdb\n' "${GEN_PG_USERDB_PASS}"
  printf 'MOE_USERDB_PASSWORD=%s\n' "${GEN_PG_USERDB_PASS}"
  echo ""
  echo "# --- Grafana ---"
  printf 'GF_SECURITY_ADMIN_USER=%s\n'   "${ADMIN_USER}"
  printf 'GF_SECURITY_ADMIN_PASSWORD=%s\n' "${GEN_GRAFANA_PASS}"
  echo 'GF_AUTH_ANONYMOUS_ENABLED=false'
  echo 'GF_ANALYTICS_REPORTING_ENABLED=false'
  echo 'GF_ANALYTICS_CHECK_FOR_UPDATES=false'
  echo ""
  echo "# --- Public URLs (optional) ---"
  printf 'APP_BASE_URL=%s\n'             "${DEFAULT_BASE_URL}"
  printf 'PUBLIC_ADMIN_URL=%s\n'         "${DEFAULT_ADMIN_URL}"
  printf 'PUBLIC_API_URL=%s\n'           "${DEFAULT_API_URL}"
  echo ""
  echo "# --- Inference Servers: configure via the Setup Wizard after first login ---"
  echo 'INFERENCE_SERVERS=[]'
  echo 'JUDGE_ENDPOINT='
  echo 'JUDGE_MODEL='
  echo 'PLANNER_MODEL='
  echo 'PLANNER_ENDPOINT='
  echo ""
  echo "# --- Legacy inference fields (backward compat) ---"
  echo 'OLLAMA_API_KEY='
  echo 'URL_RTX='
  echo 'URL_TESLA='
  echo 'GPU_COUNT=0'
  echo 'GPU_COUNT_RTX=0'
  echo 'GPU_COUNT_TESLA=0'
  echo 'EXPERT_MODELS={}'
  echo ""
  echo "# --- Web Search (SearXNG) ---"
  echo '# Set to your SearXNG instance URL for web research (optional)'
  echo 'SEARXNG_URL='
  echo ""
  echo "# --- Vector Store (ChromaDB) ---"
  echo 'CHROMA_HOST=chromadb-vector'
  echo 'CHROMA_PORT=8000'
  echo ""
  echo "# --- Logging ---"
  echo 'LOG_LEVEL=INFO'
  echo ""
  echo "# --- Token Pricing ---"
  echo 'TOKEN_PRICE_EUR=0.0000002'
  echo ""
  echo "# --- Claude Code Integration ---"
  echo "# Set CLAUDE_SKILLS_DIR to your Claude Code commands directory if using the integration"
  echo 'CLAUDE_SKILLS_DIR='
  echo 'CLAUDE_CODE_TOOL_MODEL='
  echo 'CLAUDE_CODE_TOOL_ENDPOINT='
  echo 'CLAUDE_CODE_MODELS='
  echo 'CLAUDE_CODE_MODE=code'
  echo 'CLAUDE_CODE_REASONING_MODEL='
  echo 'CLAUDE_CODE_REASONING_ENDPOINT='
  echo 'CLAUDE_CODE_PROFILES=[]'
  echo ""
  echo "# --- Cache & Routing ---"
  echo 'CACHE_HIT_THRESHOLD=0.15'
  echo 'SOFT_CACHE_THRESHOLD=0.50'
  echo 'SOFT_CACHE_MAX_EXAMPLES=2'
  echo 'CACHE_MIN_RESPONSE_LEN=150'
  echo 'EXPERT_TIER_BOUNDARY_B=20'
  echo 'EXPERT_MIN_SCORE=0.3'
  echo 'EXPERT_MIN_DATAPOINTS=5'
  echo 'EVAL_CACHE_FLAG_THRESHOLD=2'
  echo 'FEEDBACK_POSITIVE_THRESHOLD=4'
  echo 'FEEDBACK_NEGATIVE_THRESHOLD=2'
  echo ""
  echo "# --- Timeouts & Limits ---"
  echo 'HISTORY_MAX_TURNS=4'
  echo 'HISTORY_MAX_CHARS=3000'
  echo 'JUDGE_TIMEOUT=3600'
  echo 'EXPERT_TIMEOUT=3600'
  echo 'PLANNER_TIMEOUT=3600'
  echo 'PLANNER_RETRIES=3'
  echo 'PLANNER_MAX_TASKS=4'
  echo 'TOOL_MAX_TOKENS=8192'
  echo 'REASONING_MAX_TOKENS=16384'
  echo 'SSE_CHUNK_SIZE=32'
  echo 'MAX_EXPERT_OUTPUT_CHARS=8000'
  echo 'JUDGE_REFINE_MAX_ROUNDS=1'
  echo 'JUDGE_REFINE_MIN_IMPROVEMENT=0.05'
  echo ""
  echo "# --- Streaming / CORS ---"
  echo 'CORS_ALL_ORIGINS=false'
  echo 'CORS_ORIGINS='
  echo ""
  echo "# --- Expert Templates (managed via Admin UI) ---"
  echo 'EXPERT_TEMPLATES=[]'
  echo 'CUSTOM_EXPERT_PROMPTS={}'
  echo ""
  echo "# --- Graph RAG ---"
  echo 'GRAPH_INGEST_MODEL='
  echo 'GRAPH_INGEST_ENDPOINT='
  echo ""
  echo "# --- SMTP (optional) ---"
  echo 'SMTP_HOST='
  echo 'SMTP_PORT=587'
  echo 'SMTP_USER='
  echo 'SMTP_PASS='
  echo 'SMTP_FROM='
  echo 'SMTP_STARTTLS=true'
  echo 'SMTP_SSL=false'
  echo ""
  echo "# --- SSO / OIDC (optional) ---"
  echo 'AUTHENTIK_URL='
  echo 'OIDC_CLIENT_ID='
  echo 'OIDC_CLIENT_SECRET='
  echo 'OIDC_REDIRECT_URI='
  echo 'OIDC_JWKS_URL='
  echo 'OIDC_ISSUER='
  echo 'OIDC_END_SESSION_URL='
  echo 'PUBLIC_SSO_URL='
} > "${MOE_ENV_FILE}"

# 644: containers bind-mount this :ro and run as non-root (uid 1001 for langgraph,
# uid 1001 for moe-admin). chmod 600 would lock them out. The file is read-only
# inside all containers anyway, so world-readable on the host is acceptable.
chmod 644 "${MOE_ENV_FILE}"
echo "  Configuration written ✓"

# =============================================================================
#  SECTION 11: Build and deploy
# =============================================================================
echo "[8/9] Building and starting MoE Sovereign..."
echo "  This may take several minutes on first run (image pulls + builds)."
echo ""

cd "${INSTALL_DIR}"
docker compose pull --quiet 2>/dev/null || true
docker compose build --quiet
docker compose up -d

echo "  Containers started ✓"

# =============================================================================
#  SECTION 12: Health check
# =============================================================================
echo "[9/9] Waiting for MoE Sovereign API to become ready..."

# The /v1/models endpoint requires auth, so we check the metrics endpoint
# which is unauthenticated and confirms the FastAPI app is running.
HEALTH_URL="http://localhost:8002/metrics"
MAX_WAIT=120
INTERVAL=5
ELAPSED=0

while true; do
  if curl -sf "${HEALTH_URL}" &>/dev/null; then
    echo "  API ready ✓"
    break
  fi
  if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo ""
    echo "  [!] API did not respond within ${MAX_WAIT}s."
    echo "      Check logs with: sudo docker compose -f ${INSTALL_DIR}/docker-compose.yml logs langgraph-app"
    echo "      The rest of the stack may still be starting — this is normal"
    echo "      on first run while models and databases initialize."
    break
  fi
  printf "."
  sleep $INTERVAL
  ELAPSED=$(( ELAPSED + INTERVAL ))
done

# =============================================================================
#  SECTION 13: Success banner
# =============================================================================
echo ""
echo "=========================================================================="
echo ""
echo "  MoE Sovereign is installed and running!"
echo ""

if [[ -n "${DOMAIN:-}" ]]; then
  echo "  Admin UI:    https://admin.${DOMAIN}"
  echo "  API:         https://api.${DOMAIN}  (or http://localhost:8088)"
  echo "  Docs:        https://docs.${DOMAIN}"
  echo "  Log Viewer:  https://logs.${DOMAIN}"
else
  echo "  Admin UI:    http://localhost:8088"
  echo "  API:         http://localhost:8002"
  echo "  Log Viewer:  http://localhost (requires local Caddy or direct access)"
fi

echo ""
echo "  Login credentials:"
echo "    Username: ${ADMIN_USER}"
echo "    Password: (as configured)"
echo ""
echo "  NEXT STEPS:"
echo "  1. Open the Admin UI and complete the Setup Wizard"
echo "  2. Add at least one inference server (Ollama, OpenAI, LiteLLM, etc.)"
echo "  3. Configure Judge and Planner models"
echo "  4. Start chatting at your Open WebUI instance"
echo ""
echo "  Logs:    sudo docker compose logs -f"
echo "  Status:  sudo docker compose ps"
echo "  Stop:    sudo docker compose down"
echo ""
echo "=========================================================================="
echo ""
echo "  [!] SECURITY: configure your host firewall before exposing this box."
echo "      Ports 8002, 8003, 8088, 8098 and 3001 are published on 0.0.0.0 and"
echo "      several of them (e.g. /graph/*, /v1/admin/*) have NO authentication"
echo "      by design — they rely on network-level isolation. Only 80/443 should"
echo "      be reachable from the public internet (served by Caddy)."
echo ""
echo "      Full firewall recipe (UFW / firewalld / iptables):"
echo "      https://docs.moe-sovereign.org/deployment/firewall/"
echo ""
echo "=========================================================================="

#!/usr/bin/env bash
# =============================================================================
#  MoE Sovereign — One-Line Installer
#  Usage: curl -sSL https://moe-sovereign.org/install.sh | bash
#         or: bash install.sh
#
#  Supported OS: Debian 11 (bullseye), 12 (bookworm), 13 (trixie)
#                Ubuntu 22.04 (jammy), 24.04 (noble), 25.04 (plucky), 26.04+
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
#  SECTION 1a: Installation paths
# =============================================================================
# Prompted early so Section 1b (update detection) uses the correct MOE_ENV_FILE.
echo "=========================================================================="
echo "  Installation Paths"
echo "=========================================================================="
echo ""
echo "  Press ENTER to accept the default shown in [brackets]."
echo ""

_default_install="${INSTALL_DIR:-/opt/moe-sovereign}"
read -rp "  App installation directory [${_default_install}]: " _tmp_install < /dev/tty
INSTALL_DIR="${_tmp_install:-${_default_install}}"
MOE_ENV_FILE="${INSTALL_DIR}/.env"

echo ""
echo "  Install dir: ${INSTALL_DIR}"
echo ""

# =============================================================================
#  SECTION 1b: Re-run detection — UPDATE MODE
# =============================================================================
# If an existing .env and Docker volumes are found, this is an update run,
# not a fresh install. We must NOT overwrite credentials: Postgres, Neo4j,
# Redis and Grafana store their credentials inside their data volumes on
# first init — regenerating .env would lock every service out of its data.
#
# Update mode: re-apply volume ownership, git pull, compose rebuild. Done.
# No interactive prompts, no .env rewrite.
# Use an array so "docker compose" is two words regardless of IFS=$'\n\t'.
_upd_rt=()
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  _upd_rt=(docker compose)
elif command -v podman &>/dev/null; then
  if podman compose version &>/dev/null 2>&1; then
    _upd_rt=(podman compose)
  elif command -v podman-compose &>/dev/null; then
    _upd_rt=(podman-compose)
  else
    _upd_rt=(podman compose)
  fi
fi

if [[ -f "${MOE_ENV_FILE}" ]] && [[ ${#_upd_rt[@]} -gt 0 ]]; then
  if [[ "${_upd_rt[0]}" == podman* ]]; then
    vol_count=$(podman volume ls -q 2>/dev/null \
      | grep -cE '(terra_checkpoints|neo4j|redis|cache|grafana)' || true)
  else
    vol_count=$(docker volume ls -q 2>/dev/null \
      | grep -cE '(terra_checkpoints|neo4j|redis|cache|grafana)' || true)
  fi
  if [[ "${vol_count:-0}" -gt 0 ]]; then

    echo ""
    echo "  =================================================================="
    echo "  UPDATE MODE — existing installation detected"
    echo "  =================================================================="
    echo "  Found .env at ${MOE_ENV_FILE}"
    echo "  Found ${vol_count} data volume(s) from a previous install."
    echo ""
    echo "  Credentials will NOT be changed. Running:"
    echo "    1. Re-apply volume permissions (fixes container UID mismatches)"
    echo "    2. git pull --ff-only"
    echo "    3. ${_upd_rt[*]} build + up -d"
    echo "  =================================================================="
    echo ""

    # Root is required for chown. The global root check (Section 2) comes
    # later, so we do our own early check here for the update path.
    if [[ $EUID -ne 0 ]]; then
      echo "[ERROR] Update mode requires root. Re-run with: sudo bash install.sh"
      exit 1
    fi

    # Read data roots from existing .env (same logic as Section 6)
    _renv() { grep -E "^${1}=" "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-; }
    _upd_data=$(_renv MOE_DATA_ROOT);   _upd_data="${_upd_data:-/opt/moe-infra}"
    _upd_graf=$(_renv GRAFANA_DATA_ROOT); _upd_graf="${_upd_graf:-/opt/grafana}"

    # ── .env migration: add keys introduced in newer installer versions ──────
    # COMPOSE_PROFILES — added when moe-caddy moved behind a Compose profile.
    # Detect whether Caddy was running before and preserve that intent.
    if [[ -z "$(_renv COMPOSE_PROFILES)" ]]; then
      if docker ps -a --filter "name=moe-caddy" --format '{{.Names}}' 2>/dev/null \
          | grep -q "moe-caddy"; then
        echo "  [migrate] moe-caddy detected → COMPOSE_PROFILES=caddy added to .env ✓"
        echo "COMPOSE_PROFILES=caddy" >> "${MOE_ENV_FILE}"
      else
        echo "  [migrate] No Caddy container → COMPOSE_PROFILES= (disabled) ✓"
        echo "COMPOSE_PROFILES=" >> "${MOE_ENV_FILE}"
      fi
    fi

    # CPU limits — added to prevent startup failures on 2-core systems.
    for _cpu_var in NEO4J_CPU_LIMIT LANGGRAPH_CPU_LIMIT KAFKA_CPU_LIMIT CHROMA_CPU_LIMIT; do
      if [[ -z "$(_renv ${_cpu_var})" ]]; then
        echo "${_cpu_var}=2" >> "${MOE_ENV_FILE}"
      fi
    done
    # ────────────────────────────────────────────────────────────────────────

    # Ensure directories exist (they should, but guard against partial installs)
    mkdir -p \
      "${_upd_data}/kafka-data"       "${_upd_data}/neo4j-data"   \
      "${_upd_data}/neo4j-logs"       "${_upd_data}/agent-logs"   \
      "${_upd_data}/chroma-onnx-cache" "${_upd_data}/chroma-data" \
      "${_upd_data}/redis-data"       "${_upd_data}/prometheus-data" \
      "${_upd_data}/admin-logs"       "${_upd_data}/userdb"        \
      "${_upd_data}/few-shot"         \
      "${_upd_graf}/data"             "${_upd_graf}/dashboards"    \
      2>/dev/null || true

    # Re-apply container UID ownership. These must match the UIDs baked into
    # the Docker images; a mismatch causes write-permission crashes on startup.
    # kafka (confluentinc/cp-kafka): appuser uid=1000
    chown -R 1000:1000   "${_upd_data}/kafka-data"                          2>/dev/null || true
    # prometheus: runs as nobody uid=65534
    chown -R 65534:65534 "${_upd_data}/prometheus-data"                     2>/dev/null || true
    # langgraph-orchestrator + mcp-precision: moe uid=1001 gid=0
    chown -R 1001:0      "${_upd_data}/agent-logs"                          2>/dev/null || true
    # grafana: uid=472
    chown -R 472:472     "${_upd_graf}/data" "${_upd_graf}/dashboards"      2>/dev/null || true

    echo "  [1/3] Volume permissions reset ✓"

    # Pull latest code
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
      echo "  [2/3] Pulling latest code..."
      git -C "${INSTALL_DIR}" pull --ff-only \
        || echo "  [!] git pull failed — continuing with current code."
    else
      echo "  [2/3] ${INSTALL_DIR} is not a git repo — skipping pull."
    fi

    # Rebuild images and restart containers
    echo "  [3/3] Rebuilding containers..."
    cd "${INSTALL_DIR}"
    "${_upd_rt[@]}" build --quiet
    "${_upd_rt[@]}" up -d
    echo "        Containers started ✓"

    # Health check (same as Section 12)
    echo ""
    echo "  Waiting for MoE Sovereign API to become ready..."
    _health_url="http://localhost:8002/metrics"
    _max_wait=120; _interval=5; _elapsed=0
    while true; do
      if curl -sf "${_health_url}" &>/dev/null; then
        echo "  API ready ✓"
        break
      fi
      if [[ ${_elapsed} -ge ${_max_wait} ]]; then
        echo ""
        echo "  [!] API did not respond within ${_max_wait}s."
        echo "      Check logs: sudo ${_upd_rt[*]} -f ${INSTALL_DIR}/docker-compose.yml logs langgraph-app"
        break
      fi
      printf "."
      sleep "${_interval}"
      _elapsed=$(( _elapsed + _interval ))
    done

    echo ""
    echo "  =================================================================="
    echo "  MoE Sovereign updated successfully."
    echo ""
    echo "  Logs:    sudo ${_upd_rt[*]} logs -f"
    echo "  Status:  sudo ${_upd_rt[*]} ps"
    echo "  =================================================================="
    echo ""
    exit 0
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

DISTRO_ID="${ID:-}"

case "${DISTRO_ID}" in
  debian)
    VERSION_MAJOR="${VERSION_ID%%.*}"
    case "${VERSION_MAJOR}" in
      11) VERSION_CODENAME="bullseye" ;;
      12) VERSION_CODENAME="bookworm" ;;
      13) VERSION_CODENAME="trixie"   ;;
      *)
        echo "[ERROR] Unsupported Debian version: ${VERSION_ID}."
        echo "        Supported: Debian 11 (bullseye), 12 (bookworm), 13 (trixie)."
        exit 1
        ;;
    esac
    ;;
  ubuntu)
    # Ubuntu ships VERSION_CODENAME in /etc/os-release — use it directly.
    # Only validate the version number; the codename comes from the OS.
    case "${VERSION_ID}" in
      22.04|24.04|25.04|26.04) ;;
      *)
        echo "[ERROR] Unsupported Ubuntu version: ${VERSION_ID}."
        echo "        Supported: Ubuntu 22.04 (jammy), 24.04 (noble), 25.04 (plucky), 26.04."
        exit 1
        ;;
    esac
    # VERSION_CODENAME is already set by /etc/os-release on Ubuntu.
    ;;
  *)
    echo "[ERROR] Unsupported OS: ${PRETTY_NAME:-unknown}."
    echo "        Supported: Debian 11–13, Ubuntu 22.04–26.04."
    exit 1
    ;;
esac

echo "  OS: ${PRETTY_NAME} (${VERSION_CODENAME}) ✓"

# =============================================================================
#  SECTION 4: Container runtime detection
# =============================================================================
echo "[2/9] Detecting container runtime..."

CONTAINER_RUNTIME=""
COMPOSE=""        # display / .env string
COMPOSE_CMD=()    # executable array — immune to IFS=$'\n\t'

# Checks whether docker-compose plugin is registered — uses "docker info" which
# we know works, avoiding any dependency on "docker compose version" exit codes.
_docker_has_compose() {
  docker info --format '{{range .ClientInfo.Plugins}}{{.Name}} {{end}}' 2>/dev/null \
    | grep -qw "compose"
}

_resolve_podman_compose() {
  if podman compose version &>/dev/null 2>&1; then
    COMPOSE="podman compose"; COMPOSE_CMD=(podman compose)
  elif command -v podman-compose &>/dev/null; then
    COMPOSE="podman-compose"; COMPOSE_CMD=(podman-compose)
  else
    COMPOSE="podman compose"; COMPOSE_CMD=(podman compose)
  fi
}

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  DOCKER_VERSION=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
  CONTAINER_RUNTIME="docker"
  COMPOSE="docker compose"; COMPOSE_CMD=(docker compose)
  if _docker_has_compose; then
    echo "  Docker ${DOCKER_VERSION} + compose plugin detected ✓"
  else
    echo "  Docker ${DOCKER_VERSION} detected (compose plugin missing — will install) ✓"
  fi
elif command -v podman &>/dev/null; then
  PODMAN_VERSION=$(podman --version 2>/dev/null | awk '{print $3}')
  CONTAINER_RUNTIME="podman"
  _resolve_podman_compose
  echo "  Podman ${PODMAN_VERSION} detected ✓"
else
  echo ""
  echo "  No container runtime found. Choose one to install:"
  echo ""
  echo "  1) Docker CE  — industry-standard daemon-based engine, broad ecosystem"
  echo "  2) Podman     — daemonless, rootless OCI runtime, drop-in compatible"
  echo ""
  while true; do
    read -rp "  Your choice [1/2, default 1]: " _rt_choice < /dev/tty
    _rt_choice="${_rt_choice:-1}"
    case "${_rt_choice}" in
      1) CONTAINER_RUNTIME="docker"; COMPOSE="docker compose"; COMPOSE_CMD=(docker compose); break ;;
      2) CONTAINER_RUNTIME="podman"; break ;;
      *) echo "  Please enter 1 or 2." ;;
    esac
  done
fi

# =============================================================================
#  SECTION 5: Install runtime and required packages
# =============================================================================
echo "[3/9] Installing runtime and required packages..."

apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release git apache2-utils python3

if [[ "$CONTAINER_RUNTIME" == "docker" ]]; then

  if ! command -v docker &>/dev/null; then
    # ── Fresh Docker CE install from the official repo ──────────────────────
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DISTRO_ID} ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list

    apt-get update -qq
    apt-get install -y --no-install-recommends \
      docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin

    systemctl enable --now docker
    echo "  Docker CE + compose plugin installed ✓"

  elif ! _docker_has_compose; then
    # ── Docker present but compose plugin missing ────────────────────────────
    # Add the official Docker repo if not already configured, then install plugin.
    if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DISTRO_ID} ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update -qq
    fi
    apt-get install -y --no-install-recommends docker-compose-plugin
    if ! _docker_has_compose; then
      echo "[ERROR] docker-compose-plugin not registering with Docker."
      echo "        If Docker was installed via Snap, reinstall via the official repo:"
      echo "        https://docs.docker.com/engine/install/${DISTRO_ID}/"
      exit 1
    fi
    echo "  docker-compose-plugin installed ✓"

  else
    echo "  Docker CE + compose plugin already present ✓"
  fi

elif [[ "$CONTAINER_RUNTIME" == "podman" ]]; then

  if ! command -v podman &>/dev/null; then
    # TODO(human): install Podman and a compose provider for Debian/Ubuntu.
    # Decide between:
    #   a) OS-native repos  — apt install podman podman-compose
    #   b) kubic OBS repo   — latest builds, extra external repo to maintain
    # After installing, call _resolve_podman_compose to set $COMPOSE.
    echo "[!] Podman installation not yet implemented — please install podman manually."
    exit 1
  fi
  _resolve_podman_compose
  echo "  Podman + compose ready ✓"

fi

echo "  Runtime: ${CONTAINER_RUNTIME} | Compose: ${COMPOSE} ✓"

# =============================================================================
#  SECTION 6: Host directory creation
# =============================================================================
echo "[4/9] Creating host directories..."

# Determine system defaults (macOS uses home dir; bind mounts in /opt not shared by default)
case "$(uname -s)" in
  Darwin)
    _sys_data_default="${HOME}/moe-data"
    _sys_graf_default="${HOME}/moe-grafana"
    ;;
  *)
    _sys_data_default="/opt/moe-infra"
    _sys_graf_default="/opt/grafana"
    ;;
esac

# Preserve existing paths from a previous install as prompt defaults.
# Moving an existing data root would orphan all volumes.
_data_default="${MOE_DATA_ROOT:-${_sys_data_default}}"
_graf_default="${GRAFANA_DATA_ROOT:-${_sys_graf_default}}"
if [[ -f "${MOE_ENV_FILE}" ]]; then
  _prev_data=$(grep -E '^MOE_DATA_ROOT=' "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-)
  _prev_graf=$(grep -E '^GRAFANA_DATA_ROOT=' "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-)
  [[ -n "$_prev_data" ]] && _data_default="$_prev_data"
  [[ -n "$_prev_graf" ]] && _graf_default="$_prev_graf"
fi

read -rp "  Persistent data directory   [${_data_default}]: " _tmp_data < /dev/tty
MOE_DATA_ROOT="${_tmp_data:-${_data_default}}"

read -rp "  Grafana data directory      [${_graf_default}]: " _tmp_graf < /dev/tty
GRAFANA_DATA_ROOT="${_tmp_graf:-${_graf_default}}"

echo "  Data root:   ${MOE_DATA_ROOT}"
echo "  Grafana root: ${GRAFANA_DATA_ROOT}"
echo ""

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
  "${INSTALL_DIR}" 2>/dev/null || true

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
EXISTING_MINIO_USER=""
EXISTING_MINIO_PASS=""
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
  EXISTING_LIBRIS_DB_PASS=$(read_env LIBRIS_DB_PASSWORD)
  EXISTING_MINIO_USER=$(read_env MINIO_ROOT_USER)
  EXISTING_MINIO_PASS=$(read_env MINIO_ROOT_PASSWORD)
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
GEN_LIBRIS_DB_PASS="${EXISTING_LIBRIS_DB_PASS:-$(openssl rand -hex 16)}"
GEN_MINIO_USER="${EXISTING_MINIO_USER:-moeadmin}"
GEN_MINIO_PASS="${EXISTING_MINIO_PASS:-$(openssl rand -hex 16)}"

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
echo "  --- Optional Components ---"

# Caddy reverse proxy
echo ""
echo "  Caddy is a built-in TLS reverse proxy for this stack."
echo "  Skip if you already run Nginx, Traefik, or another proxy in front."
INSTALL_CADDY="false"
while true; do
  read -rp "  Install Caddy reverse proxy? [y/N]: " _caddy_choice < /dev/tty
  _caddy_choice="${_caddy_choice:-N}"
  case "${_caddy_choice,,}" in
    y|yes) INSTALL_CADDY="true";  break ;;
    n|no)  INSTALL_CADDY="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done

# Authentik SSO
echo ""
echo "  Authentik is a self-hosted SSO/OIDC provider."
echo "  Skip if you handle authentication differently or plan to add it later."
INSTALL_SSO="false"
AUTHENTIK_URL_INPUT=""
while true; do
  read -rp "  Configure Authentik SSO? [y/N]: " _sso_choice < /dev/tty
  _sso_choice="${_sso_choice:-N}"
  case "${_sso_choice,,}" in
    y|yes) INSTALL_SSO="true";  break ;;
    n|no)  INSTALL_SSO="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done
if [[ "$INSTALL_SSO" == "true" ]]; then
  prompt_input AUTHENTIK_URL_INPUT \
    "Authentik base URL (e.g. https://auth.example.com)" "" "false" "true"
fi

echo ""
if [[ -n "$EXISTING_REDIS_PASS" ]]; then
  echo "  --- Infrastructure secrets (preserved from existing .env) ---"
  echo "  Admin secret key:  [kept]"
  echo "  Redis password:    [kept]"
  echo "  Neo4j password:    [kept]"
  echo "  Grafana password:  [kept]"
  echo "  Postgres passwords: [kept]"
  echo "  MinIO credentials: [kept]"
else
  echo "  --- Auto-generated secrets (saved to .env) ---"
  echo "  Admin secret key:  [auto]"
  echo "  Redis password:    [auto]"
  echo "  Neo4j password:    [auto]"
  echo "  Grafana password:  [auto]"
  echo "  MinIO user:        ${GEN_MINIO_USER}"
  echo "  MinIO password:    [auto]"
fi
echo ""

# =============================================================================
#  SECTION 9: Caddyfile — only when Caddy was selected
# =============================================================================
# Skipped when INSTALL_CADDY=false (user runs Nginx, Traefik, or no proxy).
# When enabled, docker-compose activates the moe-caddy service via the
# "caddy" profile (COMPOSE_PROFILES=caddy in .env).
if [[ "$INSTALL_CADDY" == "true" ]]; then
  CADDYFILE="${INSTALL_DIR}/Caddyfile"
  if [[ ! -f "${CADDYFILE}" ]]; then
    if [[ -n "${DOMAIN:-}" ]]; then
      cat > "${CADDYFILE}" <<CADDY
# Generated by install.sh — edit as needed, then: sudo ${COMPOSE} restart moe-caddy

docs.${DOMAIN} {
    reverse_proxy moe-docs:8000
}

${DOMAIN} {
    handle /install.sh {
        root * /srv
        file_server
    }
    redir * https://docs.${DOMAIN}{uri} 302
}

logs.${DOMAIN} {
    reverse_proxy moe-dozzle:8080
}

files.${DOMAIN} {
    reverse_proxy moe-storage:9000
}

storage.${DOMAIN} {
    reverse_proxy moe-storage:9001
}
CADDY
      echo "[6/9] Caddyfile generated for domain '${DOMAIN}' ✓"
    else
      cat > "${CADDYFILE}" <<CADDY
# Minimal localhost stub — replace with Caddyfile.example once a domain is set,
# then: sudo ${COMPOSE} restart moe-caddy
:80 {
    respond "MoE Sovereign — configure domain in Admin UI" 200
}
CADDY
      echo "[6/9] Caddyfile: localhost stub written (no domain configured) ✓"
    fi
  else
    echo "[6/9] Caddyfile already exists — skipping ✓"
  fi
else
  echo "[6/9] Caddy skipped — configure your existing proxy manually ✓"
fi

# =============================================================================
#  SECTION 9b: Dozzle users file (generated automatically on first compose start)
# =============================================================================
# The moe-dozzle-init container reads ADMIN_USER / ADMIN_PASSWORD from .env
# and generates /opt/moe-infra/dozzle-users.yml with a bcrypt hash on startup.
# No manual step required here.
echo "[6b/9] Dozzle credentials: generated by moe-dozzle-init on first start ✓"

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
  echo "# After changes: sudo ${COMPOSE} up -d"
  echo ""
  echo "# --- Compose profiles (controls optional services) ---"
  if [[ "$INSTALL_CADDY" == "true" ]]; then
    echo 'COMPOSE_PROFILES=caddy'
  else
    echo 'COMPOSE_PROFILES='
  fi
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
  printf 'LIBRIS_DB_PASSWORD=%s\n' "${GEN_LIBRIS_DB_PASS}"
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
  echo "# --- Container CPU Limits (increase on systems with more than 2 cores) ---"
  echo '# Default 2 is safe for 2-core hosts; raise per-service on larger machines.'
  echo 'NEO4J_CPU_LIMIT=2'
  echo 'LANGGRAPH_CPU_LIMIT=2'
  echo 'KAFKA_CPU_LIMIT=2'
  echo 'CHROMA_CPU_LIMIT=2'
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
  printf 'AUTHENTIK_URL=%s\n' "${AUTHENTIK_URL_INPUT:-}"
  echo 'OIDC_CLIENT_ID='
  echo 'OIDC_CLIENT_SECRET='
  echo 'OIDC_REDIRECT_URI='
  echo 'OIDC_JWKS_URL='
  echo 'OIDC_ISSUER='
  echo 'OIDC_END_SESSION_URL='
  echo 'PUBLIC_SSO_URL='
  echo ""
  echo "# --- Object Storage (MinIO) ---"
  echo "# MINIO_ENDPOINT: internal container URL (moe-storage:9000)"
  echo "# MINIO_PUBLIC_URL: public download base URL — set after configuring Nginx"
  printf 'MINIO_ROOT_USER=%s\n'     "${GEN_MINIO_USER}"
  printf 'MINIO_ROOT_PASSWORD=%s\n' "${GEN_MINIO_PASS}"
  echo 'MINIO_ENDPOINT=moe-storage:9000'
  echo 'MINIO_PUBLIC_URL='
  echo 'MINIO_DEFAULT_BUCKET=moe-files'
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
"${COMPOSE_CMD[@]}" pull --quiet 2>/dev/null || true
"${COMPOSE_CMD[@]}" build --quiet
"${COMPOSE_CMD[@]}" up -d

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
    echo "      Check logs with: sudo ${COMPOSE} -f ${INSTALL_DIR}/docker-compose.yml logs langgraph-app"
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
  if [[ "$INSTALL_CADDY" == "true" ]]; then
    echo "  Log Viewer:  https://logs.${DOMAIN}"
  else
    echo "  Log Viewer:  http://localhost:8082 (direct, no proxy)"
  fi
else
  echo "  Admin UI:    http://localhost:8088"
  echo "  API:         http://localhost:8002"
  if [[ "$INSTALL_CADDY" == "true" ]]; then
    echo "  Log Viewer:  http://localhost (via Caddy)"
  else
    echo "  Log Viewer:  http://localhost:8082 (direct)"
  fi
fi

if [[ "$INSTALL_SSO" == "true" ]]; then
  echo ""
  echo "  SSO/OIDC:    ${AUTHENTIK_URL_INPUT}"
  echo "  [!] Complete OIDC client registration in Authentik, then add"
  echo "      Client ID, Secret and redirect URI via Admin UI → Settings → SSO."
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
echo "  Logs:    sudo ${COMPOSE} logs -f"
echo "  Status:  sudo ${COMPOSE} ps"
echo "  Stop:    sudo ${COMPOSE} down"
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

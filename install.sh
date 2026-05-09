#!/usr/bin/env bash
# =============================================================================
#  MoE Sovereign — One-Line Installer
#  Usage: curl -sSL https://moe-sovereign.org/install.sh | bash
#         or: bash install.sh
#
#  Supported OS: Debian 11 (bullseye), 12 (bookworm), 13 (trixie)
#                Ubuntu 22.04 (jammy), 24.04 (noble), 25.04 (plucky), 26.04+
#  Requires: sudo access (system packages, Docker install, group membership)
#  Run as the user that will own the installation, NOT as root directly.
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# --- Configurable defaults (override via environment) -----------------------
MOE_REPO_URL="${MOE_REPO_URL:-https://github.com/h3rb3rn/moe-sovereign.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/moe-sovereign}"
MOE_ENV_FILE="${INSTALL_DIR}/.env"

# --- Deploy user detection ---------------------------------------------------
# The deploy user is whoever runs this script. System commands use _sudo().
# If someone mistakenly runs as root via sudo, honour SUDO_USER so ownership
# ends up on the real account.
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
  DEPLOY_USER="$SUDO_USER"
else
  DEPLOY_USER="${USER:-$(id -un)}"
fi

# Elevate system commands without requiring the whole script to run as root.
_sudo() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

# Container runtime group (set by Section 4/5; empty means no group needed).
_RT_GROUP=""

# Group-aware compose execution: if the runtime group isn't active yet in this
# session (user was just added), use 'sg' to activate it for the command.
_compose() {
  if [[ -n "$_RT_GROUP" ]] && ! id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx "$_RT_GROUP"; then
    sg "$_RT_GROUP" -c "${COMPOSE_CMD[*]} $*"
  else
    "${COMPOSE_CMD[@]}" "$@"
  fi
}

# Idempotent post-up bootstrap for the enterprise data stack:
#   1. Ensure the MinIO bucket lakeFS will use exists.
#   2. Wait for lakeFS to come up, then run its one-shot setup_lakefs API call
#      so the LAKEFS_INSTALLATION_* credentials become valid login keys.
# Both steps are no-ops if the stack is not configured or already bootstrapped.
_bootstrap_enterprise_stack() {
  local _env="${MOE_ENV_FILE:-${INSTALL_DIR:-.}/.env}"
  [[ -r "$_env" ]] || return 0

  local _eds; _eds="$(grep -E '^INSTALL_ENTERPRISE_DATA_STACK=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  [[ "${_eds:-false}" == "true" ]] || return 0

  local _bucket="lakefs-data"
  local _lake_port; _lake_port="$(grep -E '^LAKEFS_HOST_PORT=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  _lake_port="${_lake_port:-8010}"
  local _akey; _akey="$(grep -E '^LAKEFS_ACCESS_KEY_ID=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  local _skey; _skey="$(grep -E '^LAKEFS_SECRET_ACCESS_KEY=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  local _user; _user="$(grep -E '^ADMIN_USER=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  _user="${_user:-admin}"

  echo "  Bootstrapping enterprise stack..."

  # Step 1: ensure MinIO bucket for lakeFS exists.
  if _compose ps moe-storage 2>/dev/null | grep -q "Up"; then
    _compose exec -T moe-storage sh -c \
      "mc alias set local http://localhost:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null 2>&1 && mc mb -p local/${_bucket} 2>/dev/null || true" \
      >/dev/null 2>&1 || true
    echo "    MinIO bucket '${_bucket}' ensured ✓"
  fi

  # Step 2: wait for lakeFS, then run setup_lakefs (idempotent: returns "already initialized" on second run).
  if [[ -n "$_akey" && -n "$_skey" ]]; then
    local _lake_url="http://localhost:${_lake_port}"
    local _i=0
    while [[ $_i -lt 30 ]]; do
      if curl -sf "${_lake_url}/_health" -o /dev/null 2>/dev/null; then break; fi
      sleep 2; _i=$((_i + 1))
    done
    if curl -sf "${_lake_url}/_health" -o /dev/null 2>/dev/null; then
      local _state; _state="$(curl -s "${_lake_url}/api/v1/setup_lakefs" 2>/dev/null | grep -oE '"state":"[^"]*"' | cut -d'"' -f4)"
      if [[ "$_state" == "not_initialized" ]]; then
        curl -s -X POST "${_lake_url}/api/v1/setup_lakefs" \
          -H "Content-Type: application/json" \
          -d "{\"username\":\"${_user}\",\"key\":{\"access_key_id\":\"${_akey}\",\"secret_access_key\":\"${_skey}\"}}" \
          >/dev/null 2>&1 || true
        echo "    lakeFS bootstrapped with admin '${_user}' ✓"
      else
        echo "    lakeFS already initialised ✓"
      fi
    else
      echo "    [!] lakeFS not reachable on ${_lake_url} — bootstrap skipped, run manually later"
    fi
  fi
}

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
# Trigger: existing .env + at least one data volume present.
# We NEVER overwrite credentials: Postgres, Neo4j, Redis and Grafana write
# their passwords into their data volumes on first init — regenerating .env
# would lock every service out of its own data.
#
# Update mode runs 4 steps — git pull is FIRST so subsequent steps always
# work with the newest code and the newest .env.example:
#   1. git pull                    (with dirty-tree guard and diverge recovery)
#   2. .env migration              (add new keys from .env.example; skip secrets)
#   3. Re-apply volume ownership   (fixes container UID mismatches)
#   4. docker/podman compose build + up
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

# Helper: read a key from existing .env — defined early so it's available
# during volume detection (line ~128) as well as the full update block.
_renv() { grep -E "^${1}=" "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-; }

if [[ -f "${MOE_ENV_FILE}" ]] && [[ ${#_upd_rt[@]} -gt 0 ]]; then
  # Volume detection: check both named volumes AND the data-root directory so
  # the update path is also triggered on installs that pre-date named volumes.
  if [[ "${_upd_rt[0]}" == podman* ]]; then
    vol_count=$(podman volume ls -q 2>/dev/null \
      | grep -cE '(terra_checkpoints|neo4j|redis|cache|grafana)' || true)
  else
    vol_count=$(docker volume ls -q 2>/dev/null \
      | grep -cE '(terra_checkpoints|neo4j|redis|cache|grafana)' || true)
  fi
  # Fallback: if no named volumes found, check whether the data directory exists
  # and contains container data (covers bind-mount installs and podman rootless).
  if [[ "${vol_count:-0}" -eq 0 ]]; then
    _upd_data_root="$(_renv MOE_DATA_ROOT)"
    if [[ -n "$_upd_data_root" && -d "${_upd_data_root}/redis-data" ]]; then
      vol_count=1
    fi
  fi
  if [[ "${vol_count:-0}" -gt 0 ]]; then

    # ── Read current version before any changes ───────────────────────────────
    _upd_ver_before=""
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
      _upd_ver_before=$(git -C "${INSTALL_DIR}" describe --tags --always 2>/dev/null \
        || git -C "${INSTALL_DIR}" log -1 --format='%h (%s)' 2>/dev/null \
        || echo "unknown")
    fi

    echo ""
    echo "  =================================================================="
    echo "  UPDATE MODE — existing installation detected"
    echo "  =================================================================="
    echo "  Found .env at ${MOE_ENV_FILE}"
    echo "  Found ${vol_count} data volume(s) from a previous install."
    echo "  Current version : ${_upd_ver_before:-unknown}"
    echo ""
    echo "  Credentials will NOT be changed. Running:"
    echo "    1. git pull (with dirty-tree and diverge handling)"
    echo "    2. Migrate .env — add new keys from .env.example, preserve all existing values"
    echo "    3. Re-apply volume permissions"
    echo "    4. ${_upd_rt[*]} build + up -d"
    echo "  =================================================================="
    echo ""

    # chown needs sudo — verify access early so we fail fast.
    if ! _sudo true 2>/dev/null; then
      echo "[ERROR] Update mode requires sudo access for volume ownership."
      exit 1
    fi

    _upd_data=$(_renv MOE_DATA_ROOT);     _upd_data="${_upd_data:-/opt/moe-infra}"
    _upd_graf=$(_renv GRAFANA_DATA_ROOT); _upd_graf="${_upd_graf:-/opt/grafana}"

    # ── STEP 1: git pull (with dirty-tree and diverge handling) ──────────────
    # Pull first so subsequent steps use the latest code and latest .env.example.
    _skip_pull=0
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
      _dirty=$(git -C "${INSTALL_DIR}" status --porcelain 2>/dev/null || true)
      if [[ -n "$_dirty" ]]; then
        echo ""
        echo "  [!] Local modifications detected in ${INSTALL_DIR}:"
        echo "$_dirty" | head -10 | sed 's/^/      /'
        [[ $(echo "$_dirty" | wc -l) -gt 10 ]] && echo "      ... (truncated)"
        echo ""
        echo "  Choose how to handle local changes:"
        echo "    s) Stash changes, then pull  (recommended — changes are saved)"
        echo "    k) Keep changes — skip git pull"
        echo "    a) Abort update"
        read -rp "  Choice [s/k/a] (default: s): " _pull_choice < /dev/tty
        _pull_choice="${_pull_choice:-s}"
        case "${_pull_choice,,}" in
          s|stash)
            if git -C "${INSTALL_DIR}" stash push \
                -m "install.sh update stash $(date +%Y%m%d-%H%M%S)" 2>/dev/null; then
              echo "  Changes stashed ✓  (restore later: git -C ${INSTALL_DIR} stash pop)"
            else
              echo "  [!] Stash failed — skipping pull to preserve local changes."
              _skip_pull=1
            fi
            ;;
          k|keep)
            echo "  Keeping local changes — skipping git pull."
            _skip_pull=1
            ;;
          *)
            echo "  Update aborted."
            exit 1
            ;;
        esac
      fi

      if [[ ${_skip_pull} -eq 0 ]]; then
        echo "  [1/4] Pulling latest code..."
        if ! git -C "${INSTALL_DIR}" pull --ff-only 2>&1; then
          # Fast-forward failed — branches may have diverged (e.g. force-push upstream)
          echo ""
          echo "  [!] Fast-forward pull failed — local and remote branches have diverged."
          echo "      (This can happen after a force-push to the upstream repository.)"
          echo ""
          echo "  Options:"
          echo "    r) Reset to origin  (discards local commits — data volumes are safe)"
          echo "    k) Keep current code — skip pull"
          echo "    a) Abort"
          read -rp "  Choice [r/k/a] (default: k): " _ff_choice < /dev/tty
          _ff_choice="${_ff_choice:-k}"
          case "${_ff_choice,,}" in
            r|reset)
              _upd_branch=$(git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD \
                2>/dev/null || echo "main")
              git -C "${INSTALL_DIR}" fetch origin
              git -C "${INSTALL_DIR}" reset --hard "origin/${_upd_branch}"
              echo "  Reset to origin/${_upd_branch} ✓"
              ;;
            k|keep)
              echo "  Keeping current code."
              _skip_pull=1
              ;;
            *)
              echo "  Update aborted."
              exit 1
              ;;
          esac
        fi
      fi

      _upd_ver_after=$(git -C "${INSTALL_DIR}" describe --tags --always 2>/dev/null \
        || git -C "${INSTALL_DIR}" log -1 --format='%h (%s)' 2>/dev/null \
        || echo "unknown")
      if [[ "${_upd_ver_before}" != "${_upd_ver_after}" ]]; then
        echo "  [1/4] Code updated: ${_upd_ver_before} → ${_upd_ver_after} ✓"
      else
        echo "  [1/4] Already at latest (${_upd_ver_after}) ✓"
      fi
    else
      echo "  [1/4] ${INSTALL_DIR} is not a git repo — skipping pull."
      _upd_ver_after="${_upd_ver_before:-unknown}"
    fi

    # ── STEP 2: .env migration ────────────────────────────────────────────────
    # Runs after git pull so .env.example is the freshly-fetched version.
    # Phase A: legacy hardcoded keys (kept for installs predating .env.example)
    if [[ -z "$(_renv COMPOSE_PROFILES)" ]]; then
      if docker ps -a --filter "name=moe-caddy" --format '{{.Names}}' 2>/dev/null \
          | grep -q "moe-caddy"; then
        echo "COMPOSE_PROFILES=caddy" >> "${MOE_ENV_FILE}"
        echo "  [migrate] COMPOSE_PROFILES=caddy (existing Caddy container detected) ✓"
      else
        echo "COMPOSE_PROFILES=" >> "${MOE_ENV_FILE}"
        echo "  [migrate] COMPOSE_PROFILES= (no Caddy container) ✓"
      fi
    fi
    for _cpu_var in NEO4J_CPU_LIMIT LANGGRAPH_CPU_LIMIT KAFKA_CPU_LIMIT CHROMA_CPU_LIMIT; do
      if [[ -z "$(_renv ${_cpu_var})" ]]; then
        echo "${_cpu_var}=2" >> "${MOE_ENV_FILE}"
      fi
    done

    # Phase B: generic sync from .env.example — add any key not yet in .env.
    # Credential/secret keys get an empty placeholder so the admin is alerted;
    # non-secret keys get the example default value directly.
    _env_example="${INSTALL_DIR}/.env.example"
    _migrated=0
    if [[ -f "${_env_example}" ]]; then
      while IFS= read -r _line; do
        [[ "$_line" =~ ^[[:space:]]*# ]] && continue   # skip comments
        [[ -z "${_line// }"            ]] && continue   # skip blank lines
        _ekey="${_line%%=*}"
        [[ -z "$_ekey"                 ]] && continue
        if ! grep -qE "^${_ekey}=" "${MOE_ENV_FILE}" 2>/dev/null; then
          if echo "$_ekey" | grep -qiE '(PASSWORD|SECRET|TOKEN|_PASS$|_KEY$|PRIVATE)'; then
            # Credential — leave empty; admin must set manually if needed
            printf '%s=\n' "${_ekey}" >> "${MOE_ENV_FILE}"
            echo "  [migrate] ${_ekey}= (credential — set manually if needed)"
          else
            _eval="${_line#*=}"
            printf '%s\n' "${_line}" >> "${MOE_ENV_FILE}"
            echo "  [migrate] ${_ekey}=${_eval}"
          fi
          (( _migrated++ )) || true
        fi
      done < "${_env_example}"
    fi
    if [[ ${_migrated} -gt 0 ]]; then
      echo "  [2/4] .env migrated — ${_migrated} new key(s) added ✓"
    else
      echo "  [2/4] .env up to date ✓"
    fi

    # ── STEP 3: Re-apply container UID ownership ──────────────────────────────
    _sudo mkdir -p \
      "${_upd_data}/kafka-data"        "${_upd_data}/neo4j-data"   \
      "${_upd_data}/neo4j-logs"        "${_upd_data}/agent-logs"   \
      "${_upd_data}/chroma-onnx-cache" "${_upd_data}/chroma-data"  \
      "${_upd_data}/redis-data"        "${_upd_data}/prometheus-data" \
      "${_upd_data}/admin-logs"        "${_upd_data}/userdb"        \
      "${_upd_data}/few-shot"          \
      "${_upd_graf}/data"              "${_upd_graf}/dashboards"    \
      2>/dev/null || true

    _upd_chown() {
      local uid="$1" gid="$2"; shift 2
      if [[ "${_upd_rt[0]}" == podman* ]]; then
        podman unshare chown -R "${uid}:${gid}" "$@" 2>/dev/null || true
      else
        _sudo chown -R "${uid}:${gid}" "$@" 2>/dev/null || true
      fi
    }
    _upd_chown 1000 1000   "${_upd_data}/kafka-data"
    # postgres:17-alpine runs as UID 70 internally. With rootless Podman, container
    # UID 0 maps to the invoking host user and the entrypoint can chmod to 70 inside,
    # so 0:0 is correct on disk. With rootful Docker, container UID 0 == host root;
    # the postgres process (UID 70) then cannot read root-owned data files, causing
    # FATAL: could not open file "global/pg_filenode.map": Permission denied on
    # every reconnect. Pick the host-side UID that matches the engine's mapping.
    if [[ "${_upd_rt[0]}" == podman* ]]; then
      _upd_chown 0  0  "${_upd_data}/langgraph-checkpoints"
    else
      _upd_chown 70 70 "${_upd_data}/langgraph-checkpoints"
    fi
    _upd_chown 0    0      "${_upd_data}/redis-data"
    _upd_chown 0    0      "${_upd_data}/neo4j-data"
    _upd_chown 0    0      "${_upd_data}/neo4j-logs"
    _upd_chown 0    0      "${_upd_data}/chroma-data"
    _upd_chown 0    0      "${_upd_data}/chroma-onnx-cache"
    _upd_chown 65534 65534 "${_upd_data}/prometheus-data"
    _upd_chown 1001 0      "${_upd_data}/agent-logs"
    _upd_chown 472  472    "${_upd_graf}/data" "${_upd_graf}/dashboards"

    echo "  [3/4] Volume permissions reset ✓"

    # ── STEP 4: Rebuild and restart containers ────────────────────────────────
    echo "  [4/4] Rebuilding containers..."
    cd "${INSTALL_DIR}"
    _upd_group=""; [[ "${_upd_rt[0]}" == "docker" ]] && _upd_group="docker"
    _upd_q="";    [[ "${_upd_rt[0]}" == "docker" ]] && _upd_q="--quiet"
    # Read active compose profiles from .env so optional services (caddy, neo4j,
    # authentik) are included in the update — compose up without --profile flags
    # silently skips any service that declares a profile.
    _upd_profiles=()
    _prof_val="$(grep -E '^COMPOSE_PROFILES=' "${MOE_ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")"
    IFS=',' read -ra _prof_arr <<< "${_prof_val}"
    for _p in "${_prof_arr[@]}"; do
      [[ -n "$_p" ]] && _upd_profiles+=(--profile "$_p")
    done
    # Podman-compose does not recreate containers with network-namespace
    # dependencies automatically. Bring the stack down first so every container
    # is removed cleanly before the new image is started.
    # Docker handles recreation in-place; the extra down is a no-op there.
    if [[ "${_upd_rt[0]}" == podman* ]]; then
      echo "  [4/4] Stopping existing containers (Podman)..."
      "${_upd_rt[@]}" "${_upd_profiles[@]}" down 2>/dev/null || true

      # After compose down, bind-mount data directories may be owned by UIDs
      # that are outside the rootless Podman subuid range (e.g. host UID 999
      # for Valkey, 70 for PostgreSQL). In rootless Podman without keep-id,
      # container root (UID 0) maps to the current user (typically UID 1000).
      # Files owned by any other host UID are inaccessible to container root,
      # causing entrypoint chown calls to fail with "Operation not permitted".
      #
      # Fix: reset ownership to the current user (= container root) so the
      # container entrypoints can re-apply their own UID/GID on startup.
      _cur_uid="$(id -u)"
      _cur_gid="$(id -g)"
      for _vol_dir in \
          "${_upd_data}/redis-data" \
          "${_upd_data}/langgraph-checkpoints" \
          "${_upd_data}/chroma-data" \
          "${_upd_data}/neo4j-data" \
          "${_upd_data}/neo4j-logs"; do
        if [[ -d "$_vol_dir" ]]; then
          _sudo chmod -R u+rwX "$_vol_dir" 2>/dev/null || true
          _sudo chown -R "${_cur_uid}:${_cur_gid}" "$_vol_dir" 2>/dev/null || true
        fi
      done
      echo "  Volume permissions reset for rootless Podman ✓"
    fi

    if [[ -n "$_upd_group" ]] && ! id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx "$_upd_group"; then
      sg "$_upd_group" -c "${_upd_rt[*]} ${_upd_profiles[*]} build ${_upd_q}"
      sg "$_upd_group" -c "${_upd_rt[*]} ${_upd_profiles[*]} up -d"
    else
      "${_upd_rt[@]}" "${_upd_profiles[@]}" build ${_upd_q}
      "${_upd_rt[@]}" "${_upd_profiles[@]}" up -d
    fi
    # Start enterprise data stack if it was enabled during initial install
    _upd_eds="$(grep -E '^INSTALL_ENTERPRISE_DATA_STACK=' "${MOE_ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")"
    if [[ "${_upd_eds:-false}" == "true" ]]; then
      if [[ -n "$_upd_group" ]] && ! id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx "$_upd_group"; then
        sg "$_upd_group" -c "${_upd_rt[*]} -f docker-compose.enterprise.yml up -d"
      else
        "${_upd_rt[@]}" -f docker-compose.enterprise.yml up -d
      fi
      _bootstrap_enterprise_stack
    fi
    echo "  Containers started ✓"

    # ── Health check ──────────────────────────────────────────────────────────
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
        echo "      Check logs: sudo ${_upd_rt[*]} logs langgraph-app"
        break
      fi
      printf "."
      sleep "${_interval}"
      _elapsed=$(( _elapsed + _interval ))
    done

    # ── Summary ───────────────────────────────────────────────────────────────
    echo ""
    echo "  =================================================================="
    echo "  MoE Sovereign updated successfully."
    echo ""
    if [[ "${_upd_ver_before:-unknown}" != "${_upd_ver_after:-unknown}" ]]; then
      echo "  Version : ${_upd_ver_before:-unknown} → ${_upd_ver_after:-unknown}"
    else
      echo "  Version : ${_upd_ver_after:-unknown} (no code change)"
    fi
    echo ""
    echo "  Logs:    sudo ${_upd_rt[*]} logs -f"
    echo "  Status:  sudo ${_upd_rt[*]} ps"
    echo "  =================================================================="
    echo ""
    exit 0
  fi
fi

# =============================================================================
#  SECTION 2: Sudo capability check
# =============================================================================
if [[ $EUID -ne 0 ]]; then
  echo "  Deploy user: ${DEPLOY_USER}"
  # Use sudo -n (non-interactive) rather than sudo -v: sudo -v requires a TTY
  # to validate credentials even with NOPASSWD, while -n just tests capability.
  if ! sudo -n true 2>/dev/null; then
    echo "[ERROR] '${DEPLOY_USER}' needs sudo access for system-level operations."
    echo "        Add to sudoers: echo '${DEPLOY_USER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${DEPLOY_USER}"
    exit 1
  fi
  echo "  Sudo access confirmed ✓"
else
  echo "  Running as root (SUDO_USER=${SUDO_USER:-none}) → deploy user: ${DEPLOY_USER} ✓"
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

_sudo apt-get update -qq
_sudo apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release git apache2-utils python3

if [[ "$CONTAINER_RUNTIME" == "docker" ]]; then

  if ! command -v docker &>/dev/null; then
    # ── Fresh Docker CE install from the official repo ──────────────────────
    _sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
      | _sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    _sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DISTRO_ID} ${VERSION_CODENAME} stable" \
      | _sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    _sudo apt-get update -qq
    _sudo apt-get install -y --no-install-recommends \
      docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin

    _sudo systemctl enable --now docker
    echo "  Docker CE + compose plugin installed ✓"

  elif ! _docker_has_compose; then
    # ── Docker present but compose plugin missing ────────────────────────────
    if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
      _sudo install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
        | _sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      _sudo chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DISTRO_ID} ${VERSION_CODENAME} stable" \
        | _sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
      _sudo apt-get update -qq
    fi
    _sudo apt-get install -y --no-install-recommends docker-compose-plugin
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

  # Add deploy user to docker group so they can run docker compose without sudo.
  _RT_GROUP="docker"
  if ! id -nG "$DEPLOY_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "docker"; then
    _sudo usermod -aG docker "$DEPLOY_USER"
    echo "  User '${DEPLOY_USER}' added to 'docker' group ✓"
    echo "  (Re-login after install to use docker without sudo in future sessions)"
  else
    echo "  User '${DEPLOY_USER}' already in 'docker' group ✓"
  fi

elif [[ "$CONTAINER_RUNTIME" == "podman" ]]; then

  if ! command -v podman &>/dev/null; then
    # Install Podman + rootless dependencies from OS-native repos.
    # Debian 11+ and Ubuntu 22.04+ ship recent-enough versions.
    echo "  Installing Podman + rootless dependencies..."
    _sudo apt-get update -qq
    _sudo apt-get install -y --no-install-recommends \
      podman podman-compose \
      uidmap \
      passt \
      slirp4netns \
      fuse-overlayfs \
      dbus-user-session
    if ! command -v podman &>/dev/null; then
      echo "[ERROR] Podman installation failed. Check 'apt policy podman'."
      exit 1
    fi
    echo "  Podman + rootless dependencies installed ✓"
  else
    echo "  Podman already present — installing missing rootless dependencies..."
    _sudo apt-get install -y --no-install-recommends \
      uidmap passt slirp4netns fuse-overlayfs dbus-user-session &>/dev/null || true
  fi

  # Ensure podman-compose is available (might be missing even if podman is)
  if ! command -v podman-compose &>/dev/null; then
    _sudo apt-get install -y --no-install-recommends podman-compose
  fi

  # Configure user namespace mappings required for rootless containers.
  # newuidmap/newgidmap (from uidmap) use these files to set up the UID/GID
  # ranges that Podman can map inside the container.
  if ! grep -q "^${DEPLOY_USER}:" /etc/subuid 2>/dev/null; then
    echo "${DEPLOY_USER}:100000:65536" | _sudo tee -a /etc/subuid > /dev/null
    echo "  subuid range added for '${DEPLOY_USER}' ✓"
  fi
  if ! grep -q "^${DEPLOY_USER}:" /etc/subgid 2>/dev/null; then
    echo "${DEPLOY_USER}:100000:65536" | _sudo tee -a /etc/subgid > /dev/null
    echo "  subgid range added for '${DEPLOY_USER}' ✓"
  fi

  # Helper to run podman as the deploy user even when script is root.
  _podman_as_user() {
    if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
      sudo -u "$DEPLOY_USER" podman "$@"
    else
      podman "$@"
    fi
  }

  # Configure Podman to use cgroupfs instead of systemd.
  # Without a full systemd user session (e.g. SSH logins), Podman would warn
  # "cgroupv2 manager set to systemd but no user session available" on every
  # invocation and may fail to start containers. cgroupfs works everywhere.
  _deploy_home=$(eval echo "~${DEPLOY_USER}")
  _containers_conf="${_deploy_home}/.config/containers/containers.conf"
  # Detect environments where nftables is unavailable (WSL, some LXC/VMs).
  # Netavark defaults to nftables; switch it to iptables when nft is missing
  # or broken. Note: CNI is not an option on Debian 13+ (not compiled in).
  _netavark_fw="nftables"
  if ! nft list ruleset &>/dev/null 2>&1; then
    _netavark_fw="iptables"
    _sudo apt-get install -y --no-install-recommends iptables 2>/dev/null || true
    echo "  nftables unavailable (WSL/LXC?) — netavark will use iptables ✓"
  fi

  # Always write containers.conf so re-runs pick up the correct settings.
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    sudo -u "$DEPLOY_USER" mkdir -p "${_deploy_home}/.config/containers"
    printf '[engine]\ncgroup_manager = "cgroupfs"\nenv = ["NETAVARK_FW=%s"]\n' \
      "$_netavark_fw" | sudo -u "$DEPLOY_USER" tee "$_containers_conf" > /dev/null
  else
    mkdir -p "${_deploy_home}/.config/containers"
    printf '[engine]\ncgroup_manager = "cgroupfs"\nenv = ["NETAVARK_FW=%s"]\n' \
      "$_netavark_fw" | tee "$_containers_conf" > /dev/null
  fi
  echo "  Podman: cgroup_manager=cgroupfs, NETAVARK_FW=${_netavark_fw} ✓"

  # Prune networks created with the wrong firewall backend so they are
  # recreated correctly on next compose up.
  _podman_as_user network prune --force &>/dev/null || true

  # Fix "/ is not a shared mount" warning: configure the kernel mount
  # propagation for the root filesystem via a systemd override.
  # This persists across reboots and avoids bind-mount issues in rootless mode.
  _mnt_override="/etc/systemd/system/local-fs.target.d/shared-propagation.conf"
  if [[ ! -f "$_mnt_override" ]]; then
    _sudo mkdir -p "$(dirname "$_mnt_override")"
    printf '[Unit]\nConditionPathIsMountPoint=/\n[Service]\nExecStartPost=/bin/mount --make-rshared /\n' \
      | _sudo tee "$_mnt_override" > /dev/null
    _sudo mount --make-rshared / 2>/dev/null || true
    echo "  Root mount propagation set to shared ✓"
  fi

  # Configure unqualified-search-registries so short image names like
  # "postgres:17-alpine" resolve to docker.io without explicit prefix.
  _reg_conf="/etc/containers/registries.conf.d/docker-io-search.conf"
  if [[ ! -f "$_reg_conf" ]]; then
    echo 'unqualified-search-registries = ["docker.io"]' \
      | _sudo tee "$_reg_conf" > /dev/null
    echo "  docker.io added as default search registry ✓"
  fi

  # Install nftables (required by netavark network backend) and aardvark-dns
  # (container DNS resolution). Without these, container networking fails.
  _sudo apt-get install -y --no-install-recommends nftables 2>/dev/null || true
  _sudo apt-get install -y --no-install-recommends aardvark-dns 2>/dev/null || true

  # Enable Podman socket so docker-socket-proxy and moe-admin can use the
  # container API. With rootless Podman the socket lives at
  # /run/user/<uid>/podman/podman.sock.
  _deploy_uid=$(id -u "$DEPLOY_USER" 2>/dev/null || echo "1000")
  _podman_socket="/run/user/${_deploy_uid}/podman/podman.sock"
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    XDG_RUNTIME_DIR="/run/user/${_deploy_uid}" \
      sudo -u "$DEPLOY_USER" systemctl --user enable --now podman.socket 2>/dev/null || true
  else
    systemctl --user enable --now podman.socket 2>/dev/null || true
  fi

  _podman_as_user system migrate &>/dev/null || true

  # Smoke-test: verify rootless Podman is functional before proceeding.
  echo "  Verifying rootless Podman..."
  if ! _podman_as_user info &>/dev/null; then
    echo "[ERROR] Podman smoke test failed — 'podman info' returned an error."
    echo "        Try: podman info"
    exit 1
  fi
  echo "  Podman rootless smoke test passed ✓"

  _resolve_podman_compose

  # Enable lingering so rootless containers survive logout / start on boot.
  if command -v loginctl &>/dev/null && [[ "$DEPLOY_USER" != "root" ]]; then
    _sudo loginctl enable-linger "$DEPLOY_USER" 2>/dev/null || true
    echo "  Linger enabled for '${DEPLOY_USER}' (containers survive logout) ✓"
  fi

  # Rootless Podman needs no group — each user manages their own containers.
  _RT_GROUP=""
  # Store socket path for use in .env generation (Section 10).
  _PODMAN_SOCKET="$_podman_socket"
  echo "  Podman + compose ready ✓"

  # Rootless Podman cannot bind privileged ports (< 1024) by default.
  # Lower the threshold to 80 so Caddy can serve HTTP/HTTPS without root.
  if [[ "$(uname -s)" == "Linux" ]] && [[ $EUID -ne 0 ]]; then
    _cur_port=$(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null || echo 1024)
    if [[ "${_cur_port}" -gt 80 ]]; then
      _sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80 &>/dev/null || true
      echo 'net.ipv4.ip_unprivileged_port_start=80' \
        | _sudo tee /etc/sysctl.d/99-podman-unprivileged-ports.conf >/dev/null 2>&1 || true
      echo "  Unprivileged port start lowered to 80 (Caddy TLS support) ✓"
    fi
  fi

fi

# podman-compose does not support --quiet; docker compose does.
[[ "$CONTAINER_RUNTIME" == "docker" ]] && _Q="--quiet" || _Q=""

echo "  Runtime: ${CONTAINER_RUNTIME} | Compose: ${COMPOSE} | Group: ${_RT_GROUP:-none} ✓"

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

_sudo mkdir -p \
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
  "${MOE_DATA_ROOT}/gap-healer-stats" \
  "${MOE_DATA_ROOT}/checkpoint-archives" \
  "${GRAFANA_DATA_ROOT}/data" \
  "${GRAFANA_DATA_ROOT}/dashboards" \
  "${INSTALL_DIR}" 2>/dev/null || true

# INSTALL_DIR is owned by the deploy user so git clone and .env writes work without sudo.
_sudo chown "$DEPLOY_USER":"$DEPLOY_USER" "${INSTALL_DIR}"

# Files that must exist as regular files (not directories) before the container
# runtime mounts them.  Create them owned by the deploy user so rootless
# container processes can write to them without a chown step.
_touch_as_user() {
  local f="$1"
  [[ -f "$f" ]] && return
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    sudo -u "$DEPLOY_USER" touch "$f" 2>/dev/null || { _sudo touch "$f"; _sudo chown "$DEPLOY_USER":"$DEPLOY_USER" "$f"; }
  else
    touch "$f" 2>/dev/null || { _sudo touch "$f"; _sudo chown "$DEPLOY_USER":"$DEPLOY_USER" "$f"; }
  fi
}
# dozzle-users.yml — moe-dozzle-init overwrites with bcrypt hash on first start
_touch_as_user "${MOE_DATA_ROOT}/dozzle-users.yml"
# moe-admin runtime files
_touch_as_user "${MOE_DATA_ROOT}/cleanup-config.json"
_touch_as_user "${MOE_DATA_ROOT}/cleanup-history.jsonl"

echo "  Host directories created at ${MOE_DATA_ROOT} ✓"

# Fix ownership for containers that run as non-root UIDs.
#
# Three strategies are tried in order:
#
# 1. `podman unshare chown` — runs chown inside the user namespace so that
#    container UIDs translate to the correct host subuid range automatically.
#    Works on native Linux; may fail on WSL2 kernels that restrict unshare.
#
# 2. Direct `sudo chown <host-uid>` — when unshare fails, compute the host
#    UID that corresponds to the container UID (UID 0 → deploy-user UID;
#    UID N → subuid_start + N − 1) and chown directly.  Requires sudo but
#    avoids user-namespace restrictions entirely.
#
# 3. chmod a+rwX fallback — last resort when both chown strategies fail
#    (e.g. read-only filesystem).  Less secure but prevents a total failure.
#
# Docker uses the host UID namespace directly, so strategy 1 is skipped and
# strategy 2 uses the literal container UID as the host UID.
_container_uid_to_host_uid() {
  local cuid="$1"
  if [[ "$cuid" -eq 0 ]]; then
    id -u "${DEPLOY_USER}" 2>/dev/null || echo "${UID:-1000}"
  else
    local _sub
    _sub=$(grep -m1 "^${DEPLOY_USER}:" /etc/subuid 2>/dev/null | cut -d: -f2)
    [[ -n "$_sub" ]] && echo $(( _sub + cuid - 1 )) || echo "$cuid"
  fi
}

_chown_for_container() {
  local uid="$1" gid="$2"; shift 2
  local _ok=0
  if [[ "${CONTAINER_RUNTIME}" == "podman" ]]; then
    # Strategy 1: podman unshare (native Linux)
    _podman_as_user unshare chown -R "${uid}:${gid}" "$@" 2>/dev/null && _ok=1 || true
    if [[ $_ok -eq 0 ]]; then
      # Strategy 2: direct sudo chown with computed host UIDs (WSL2 fallback)
      local h_uid h_gid
      h_uid=$(_container_uid_to_host_uid "$uid")
      h_gid=$(_container_uid_to_host_uid "$gid")
      _sudo chown -R "${h_uid}:${h_gid}" "$@" 2>/dev/null && _ok=1 || true
    fi
  else
    _sudo chown -R "${uid}:${gid}" "$@" 2>/dev/null && _ok=1 || true
  fi
  if [[ $_ok -eq 1 ]]; then
    _sudo chmod -R u+rwX "$@" 2>/dev/null || true
  else
    # Strategy 3: world-writable fallback
    echo "  [!] chown ${uid}:${gid} failed for $* — applying fallback chmod a+rwX"
    _sudo chmod -R a+rwX "$@" 2>/dev/null || true
  fi
}

# kafka (confluentinc/cp-kafka): appuser uid=1000
_chown_for_container 1000 1000 "${MOE_DATA_ROOT}/kafka-data"

# Containers whose entrypoints chown the data dir to their service user at
# startup need the directory owned by container root (UID 0) first.
#
# In rootless Podman container UID 0 == the deploy user on the host.
# Directories created by `sudo mkdir` are host-root-owned, which maps to
# UID 65534 (overflow/nobody) inside the container — the entrypoint's root
# process cannot chown a file it does not own.  Pre-chowning to UID 0 via
# `podman unshare` translates to the deploy user on the host, giving
# container root ownership and allowing the entrypoint's chown to succeed.
# In Docker container root == host root, so UID 0:0 is already the owner
# after `sudo mkdir` — this is effectively a no-op.
_chown_for_container 0 0 "${MOE_DATA_ROOT}/langgraph-checkpoints"  # postgres entrypoint: chown → postgres (70)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/redis-data"              # valkey entrypoint:   chown → valkey (999)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/neo4j-data"              # neo4j entrypoint:    chown → neo4j (7474)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/neo4j-logs"              # neo4j entrypoint:    chown → neo4j (7474)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/chroma-data"             # chromadb: runs as root, needs writable /data
_chown_for_container 0 0 "${MOE_DATA_ROOT}/chroma-onnx-cache"       # chromadb: ONNX model cache

# prometheus: runs as nobody uid=65534
_chown_for_container 65534 65534 "${MOE_DATA_ROOT}/prometheus-data"
# langgraph-orchestrator + mcp-precision: moe uid=1001 gid=0
_chown_for_container 1001 0 "${MOE_DATA_ROOT}/agent-logs"
# moe-admin: moe uid=1001 gid=0 (runtime state files and directories)
_chown_for_container 1001 0 \
  "${MOE_DATA_ROOT}/admin-logs" \
  "${MOE_DATA_ROOT}/generated" \
  "${MOE_DATA_ROOT}/gap-healer-stats" \
  "${MOE_DATA_ROOT}/checkpoint-archives" \
  "${MOE_DATA_ROOT}/cleanup-config.json" \
  "${MOE_DATA_ROOT}/cleanup-history.jsonl"
# grafana: uid=472
_chown_for_container 472 472 "${GRAFANA_DATA_ROOT}/data" "${GRAFANA_DATA_ROOT}/dashboards"

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

# Git operations must run as DEPLOY_USER so all repo files are user-owned.
# If the script is executed as root (sudo bash install.sh), use sudo -u to
# drop privileges for git commands.
_git_as_user() {
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    sudo -u "$DEPLOY_USER" git "$@"
  else
    git "$@"
  fi
}

if [[ -d "${INSTALL_DIR}/.git" ]]; then
  echo "  Existing repo found — pulling latest changes..."
  # Guard against dirty working tree: stash silently on fresh-install path
  # (no interactive prompts here — update mode handles the interactive case above).
  _s7_dirty=$(git -C "${INSTALL_DIR}" status --porcelain 2>/dev/null || true)
  _s7_stashed=0
  if [[ -n "$_s7_dirty" ]]; then
    echo "  [!] Local modifications found — stashing before pull..."
    if _git_as_user -C "${INSTALL_DIR}" stash push \
        -m "install.sh section-7 stash $(date +%Y%m%d-%H%M%S)" 2>/dev/null; then
      _s7_stashed=1
    else
      echo "  [!] Stash failed — attempting pull anyway."
    fi
  fi
  if ! _git_as_user -C "${INSTALL_DIR}" pull --ff-only 2>&1; then
    echo "  [!] Fast-forward pull failed — resetting to origin..."
    _s7_branch=$(git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    _git_as_user -C "${INSTALL_DIR}" fetch origin
    _git_as_user -C "${INSTALL_DIR}" reset --hard "origin/${_s7_branch}"
    echo "  Reset to origin/${_s7_branch} ✓"
  fi
  # Restore local modifications after pull. On conflict, always prefer the
  # upstream (HEAD) version so the deployment runs the correct released code.
  if [[ $_s7_stashed -eq 1 ]]; then
    if ! _git_as_user -C "${INSTALL_DIR}" stash pop 2>/dev/null; then
      echo "  [!] Stash pop conflict — keeping upstream versions for conflicted files."
      _git_as_user -C "${INSTALL_DIR}" checkout HEAD -- \
        $(git -C "${INSTALL_DIR}" diff --name-only --diff-filter=U 2>/dev/null) \
        2>/dev/null || true
      _git_as_user -C "${INSTALL_DIR}" reset HEAD 2>/dev/null || true
    fi
    echo "  Local modifications restored ✓"
  fi
else
  echo "  Cloning repository to ${INSTALL_DIR}..."
  _git_as_user clone "${MOE_REPO_URL}" "${INSTALL_DIR}"
fi

# Postgres mounts ./scripts/postgres-init as docker-entrypoint-initdb.d:ro.
# The container user (uid 70 in postgres:alpine) needs read + execute on the dir.
chmod -R a+rX "${INSTALL_DIR}/scripts" 2>/dev/null || true

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
GEN_OIDC_CLIENT_ID=""
GEN_OIDC_CLIENT_SECRET=""
GEN_OIDC_REDIRECT_URI=""
GEN_OIDC_JWKS_URL=""
GEN_OIDC_ISSUER=""
GEN_OIDC_END_SESSION_URL=""

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
EXISTING_AUTHENTIK_SECRET=""
EXISTING_AUTHENTIK_PG_PASS=""
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
  EXISTING_AUTHENTIK_SECRET=$(read_env AUTHENTIK_SECRET_KEY)
  EXISTING_AUTHENTIK_PG_PASS=$(read_env AUTHENTIK_POSTGRESQL__PASSWORD)
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
GEN_AUTHENTIK_SECRET="${EXISTING_AUTHENTIK_SECRET:-$(openssl rand -hex 32)}"
GEN_AUTHENTIK_PG_PASS="${EXISTING_AUTHENTIK_PG_PASS:-$(openssl rand -hex 16)}"

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

# URL defaults resolved after Caddy selection (see below)

echo ""
echo "  --- Optional Components ---"

# Neo4j GraphRAG
echo ""
echo "  Neo4j powers GraphRAG (knowledge-graph enrichment) and the ontology curator."
echo "  Requires ~1.5 GB RAM. Skip for small VMs that only need expert templates / CC profiles."
INSTALL_NEO4J="true"
while true; do
  read -rp "  Install Neo4j GraphRAG? [Y/n]: " _neo4j_choice < /dev/tty
  _neo4j_choice="${_neo4j_choice:-Y}"
  case "${_neo4j_choice,,}" in
    y|yes) INSTALL_NEO4J="true";  break ;;
    n|no)  INSTALL_NEO4J="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done

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

# Public URLs — prompted only when Caddy+domain, otherwise localhost defaults
if [[ "$INSTALL_CADDY" == "true" && -n "${DOMAIN:-}" ]]; then
  echo ""
  echo "  --- Public Subdomain URLs ---"
  echo "  Caddy will serve each service at the URL you confirm below."
  echo "  Press ENTER to accept the subdomain shown in [brackets]."
  prompt_input DEFAULT_ADMIN_URL  "Admin UI URL"    "https://admin.${DOMAIN}" "false" "false"
  prompt_input DEFAULT_BASE_URL   "User portal URL" "https://${DOMAIN}"       "false" "false"
  prompt_input DEFAULT_API_URL    "API URL"         "https://api.${DOMAIN}"   "false" "false"
else
  DEFAULT_ADMIN_URL="http://localhost:8088"
  DEFAULT_BASE_URL="http://localhost:8088"
  DEFAULT_API_URL="http://localhost:8000"
fi

# Authentik SSO — deploy built-in server
echo ""
echo "  Authentik is a self-hosted SSO/OIDC provider (server + worker + DB + Redis)."
echo "  Installs 4 containers inside this stack. Skip if you already run an external IdP."
INSTALL_AUTHENTIK="false"
AUTHENTIK_BOOTSTRAP_EMAIL_INPUT=""
AUTHENTIK_BOOTSTRAP_PASSWORD_INPUT=""
while true; do
  read -rp "  Deploy Authentik SSO server? [y/N]: " _authentik_choice < /dev/tty
  _authentik_choice="${_authentik_choice:-N}"
  case "${_authentik_choice,,}" in
    y|yes) INSTALL_AUTHENTIK="true";  break ;;
    n|no)  INSTALL_AUTHENTIK="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done
if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  prompt_input AUTHENTIK_BOOTSTRAP_EMAIL_INPUT \
    "Authentik bootstrap admin e-mail" "${ADMIN_EMAIL:-admin@example.com}" "false" "true"
  prompt_input AUTHENTIK_BOOTSTRAP_PASSWORD_INPUT \
    "Authentik bootstrap admin password (min 10 chars)" "" "true" "true"
fi

# Authentik SSO — OIDC client configuration
echo ""
INSTALL_SSO="false"
AUTHENTIK_URL_INPUT=""
if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  # Configure OIDC to point at the local Authentik instance
  INSTALL_SSO="true"
  if [[ -n "${DOMAIN:-}" && "$INSTALL_CADDY" == "true" ]]; then
    prompt_input AUTHENTIK_URL_INPUT \
      "SSO (Authentik) URL" "https://sso.${DOMAIN}" "false" "false"
  elif [[ -n "${DOMAIN:-}" ]]; then
    AUTHENTIK_URL_INPUT="https://sso.${DOMAIN}"
  else
    AUTHENTIK_URL_INPUT="http://localhost:9000"
  fi
else
  echo "  Configure OIDC against an existing Authentik (or other IdP)?"
  while true; do
    read -rp "  Configure Authentik/OIDC SSO? [y/N]: " _sso_choice < /dev/tty
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
#  SECTION 8b: Optional Enterprise Data Stack
# =============================================================================
echo "=========================================================================="
echo "  Optional: Enterprise Data Management Stack"
echo "=========================================================================="
echo ""
echo "  Dieser optionale Enterprise-Data-Stack (Apache NiFi, OpenLineage, lakeFS)"
echo "  bändigt Daten-Chaos aus Legacy-Systemen, ermöglicht lückenloses"
echo "  Audit-Tracking und bietet isolierte Branches für sichere"
echo "  Was-wäre-wenn-Simulationen."
echo ""
echo "  Requires approx. 6.5 GB additional RAM (NiFi JVM + Marquez + lakeFS)."
echo ""
read -rp "  Install Enterprise Data Stack (NiFi, Marquez, lakeFS)? [y/N]: " _eds_choice < /dev/tty
_eds_choice="${_eds_choice:-N}"
case "${_eds_choice,,}" in
  y|yes) INSTALL_ENTERPRISE_DATA_STACK=true  ;;
  *)     INSTALL_ENTERPRISE_DATA_STACK=false ;;
esac
export INSTALL_ENTERPRISE_DATA_STACK
echo ""

# =============================================================================
#  SECTION 9: Caddyfile — only when Caddy was selected
# =============================================================================
# Skipped when INSTALL_CADDY=false (user runs Nginx, Traefik, or no proxy).
# When enabled, docker-compose activates the moe-caddy service via the
# "caddy" profile (COMPOSE_PROFILES=caddy in .env).
if [[ "$INSTALL_CADDY" == "true" ]]; then
  CADDYFILE="${INSTALL_DIR}/Caddyfile"
  # Helper: strip scheme and path from a URL → bare hostname for Caddy
  _caddy_host() { echo "${1:-}" | sed 's|^https\?://||' | cut -d/ -f1; }

  if [[ ! -f "${CADDYFILE}" ]]; then
    if [[ -n "${DOMAIN:-}" ]]; then
      _h_admin=$(_caddy_host "${DEFAULT_ADMIN_URL:-admin.${DOMAIN}}")
      _h_portal=$(_caddy_host "${DEFAULT_BASE_URL:-${DOMAIN}}")
      _h_api=$(_caddy_host "${DEFAULT_API_URL:-api.${DOMAIN}}")

      {
        echo "# Generated by install.sh — edit as needed, then: ${COMPOSE} restart moe-caddy"
        echo ""
        echo "${_h_admin} {"
        echo "    reverse_proxy moe-admin:8088"
        echo "}"
        echo ""
        echo "${_h_api} {"
        echo "    reverse_proxy langgraph-orchestrator:8000"
        echo "}"
        echo ""
        echo "grafana.${DOMAIN} {"
        echo "    reverse_proxy moe-grafana:3000"
        echo "}"
        echo ""
        echo "logs.${DOMAIN} {"
        echo "    reverse_proxy moe-dozzle:8080"
        echo "}"
        echo ""
        echo "docs.${DOMAIN} {"
        echo "    reverse_proxy moe-docs:8000"
        echo "}"
        echo ""
        echo "files.${DOMAIN} {"
        echo "    reverse_proxy moe-storage:9000"
        echo "}"
        echo ""
        echo "storage.${DOMAIN} {"
        echo "    reverse_proxy moe-storage:9001"
        echo "}"
        if [[ "$INSTALL_AUTHENTIK" == "true" && -n "${AUTHENTIK_URL_INPUT:-}" ]]; then
          _h_sso=$(_caddy_host "${AUTHENTIK_URL_INPUT}")
          echo ""
          echo "${_h_sso} {"
          echo "    reverse_proxy authentik-server:9000"
          echo "}"
        fi
        echo ""
        # portal.DOMAIN — user-facing login; /login blocked to prevent admin access
        echo "${_h_portal} {"
        echo "    @admin_login path /login"
        echo "    redir @admin_login /user/login 302"
        echo "    @root path /"
        echo "    redir @root /user/login 302"
        echo "    reverse_proxy moe-admin:8088"
        echo "}"
        echo ""
        # bare domain → portal
        echo "${DOMAIN} {"
        echo "    handle /install.sh {"
        echo "        root * /srv"
        echo "        file_server"
        echo "    }"
        echo "    redir * https://${_h_portal}{uri} 302"
        echo "}"
      } > "${CADDYFILE}"
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
  _env_profiles=()
  [[ "$INSTALL_NEO4J"     == "true" ]] && _env_profiles+=(neo4j)
  [[ "$INSTALL_CADDY"     == "true" ]] && _env_profiles+=(caddy)
  [[ "$INSTALL_AUTHENTIK" == "true" ]] && _env_profiles+=(authentik)
  printf 'COMPOSE_PROFILES=%s\n' "$(IFS=,; echo "${_env_profiles[*]}")"
  printf 'INSTALL_ENTERPRISE_DATA_STACK=%s\n' "${INSTALL_ENTERPRISE_DATA_STACK:-false}"
  echo ""
  echo "# --- Container runtime socket + storage paths ---"
  echo "# Docker: DOCKER_SOCKET=/var/run/docker.sock, CONTAINER_STORAGE_ROOT=/var/lib/docker"
  echo "# Podman: DOCKER_SOCKET=/run/user/<uid>/podman/podman.sock, CONTAINER_STORAGE_ROOT=~/.local/share/containers"
  if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
    printf 'DOCKER_SOCKET=%s\n'              "${_PODMAN_SOCKET:-/run/user/1000/podman/podman.sock}"
    printf 'CONTAINER_STORAGE_ROOT=%s\n'    "${HOME}/.local/share/containers"
  else
    echo 'DOCKER_SOCKET=/var/run/docker.sock'
    echo 'CONTAINER_STORAGE_ROOT=/var/lib/docker'
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
  # NEO4J_URI empty when Neo4j is not installed — signals _init_graph_rag() to skip
  if [[ "$INSTALL_NEO4J" == "true" ]]; then
    printf 'NEO4J_PASS=%s\n' "${GEN_NEO4J_PASS}"
    echo 'NEO4J_URI=bolt://neo4j-knowledge:7687'
    echo 'NEO4J_USER=neo4j'
  else
    echo 'NEO4J_PASS='
    echo 'NEO4J_URI='
    echo 'NEO4J_USER=neo4j'
  fi
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
  printf 'AUTHENTIK_URL=%s\n'     "${AUTHENTIK_URL_INPUT:-}"
  printf 'OIDC_CLIENT_ID=%s\n'    "${GEN_OIDC_CLIENT_ID:-}"
  printf 'OIDC_CLIENT_SECRET=%s\n' "${GEN_OIDC_CLIENT_SECRET:-}"
  printf 'OIDC_REDIRECT_URI=%s\n' "${GEN_OIDC_REDIRECT_URI:-}"
  printf 'OIDC_JWKS_URL=%s\n'     "${GEN_OIDC_JWKS_URL:-}"
  printf 'OIDC_ISSUER=%s\n'       "${GEN_OIDC_ISSUER:-}"
  printf 'OIDC_END_SESSION_URL=%s\n' "${GEN_OIDC_END_SESSION_URL:-}"
  echo 'PUBLIC_SSO_URL='
  echo ""
  echo "# --- Authentik Server (only active when COMPOSE_PROFILES includes 'authentik') ---"
  echo "AUTHENTIK_TAG=2026.2.1"
  printf 'AUTHENTIK_SECRET_KEY=%s\n'           "${GEN_AUTHENTIK_SECRET}"
  echo 'AUTHENTIK_POSTGRESQL__USER=authentik'
  echo 'AUTHENTIK_POSTGRESQL__NAME=authentik'
  printf 'AUTHENTIK_POSTGRESQL__PASSWORD=%s\n' "${GEN_AUTHENTIK_PG_PASS}"
  printf 'AUTHENTIK_BOOTSTRAP_EMAIL=%s\n'      "${AUTHENTIK_BOOTSTRAP_EMAIL_INPUT:-}"
  printf 'AUTHENTIK_BOOTSTRAP_PASSWORD=%s\n'   "${AUTHENTIK_BOOTSTRAP_PASSWORD_INPUT:-}"
  echo 'AUTHENTIK_HTTP_PORT=9000'
  echo 'AUTHENTIK_HTTPS_PORT=9443'
  echo 'AUTHENTIK_ERROR_REPORTING__ENABLED=false'
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

# Create Authentik data directories (bind-mount sources must exist before compose up)
if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  _sudo mkdir -p "${MOE_DATA_ROOT}/authentik"
  for _ak_dir in postgres redis media certs custom-templates blueprints; do
    _sudo mkdir -p "${MOE_DATA_ROOT}/authentik/${_ak_dir}"
  done
  _sudo chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${MOE_DATA_ROOT}/authentik"
  echo "  Authentik data directories created under ${MOE_DATA_ROOT}/authentik/ ✓"

  # Build OIDC redirect URIs: always include localhost fallback
  _ak_redirect_uris="http://localhost:8088/auth/callback"
  if [[ -n "${DOMAIN:-}" ]]; then
    _ak_redirect_uris="${_ak_redirect_uris}
https://admin.${DOMAIN}/auth/callback
https://portal.${DOMAIN}/auth/callback"
  fi

  # Write Authentik blueprint — applied automatically by the worker on first start.
  # Creates the OAuth2/OIDC provider + MoE Sovereign application with pre-generated
  # client credentials so no manual Authentik UI interaction is needed.
  cat > "${MOE_DATA_ROOT}/authentik/blueprints/moe-sovereign.yaml" <<BLUEPRINT
# Generated by install.sh — applied once by authentik-worker on first start.
# Re-running install.sh regenerates this file with the current credentials.
version: 1
metadata:
  name: MoE Sovereign OIDC
entries:
  - model: authentik_providers_oauth2.oauth2provider
    state: present
    identifiers:
      name: MoE Sovereign
    attrs:
      name: MoE Sovereign
      client_type: confidential
      client_id: moe-sovereign
      client_secret: "${GEN_AUTHENTIK_SECRET}"
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      redirect_uris: |
$(echo "${_ak_redirect_uris}" | sed 's/^/        /')
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, authentik Self-signed Certificate]]
      include_claims_in_id_token: true
      issuer_mode: per_provider

  - model: authentik_core.application
    state: present
    identifiers:
      slug: moe-sovereign
    attrs:
      name: MoE Sovereign
      slug: moe-sovereign
      provider: !KeyOf moe-sovereign
      meta_launch_url: "${AUTHENTIK_URL_INPUT}/if/user/"
BLUEPRINT
  echo "  Authentik blueprint written ✓"

  # Pre-fill OIDC vars in the installer scope so they land in .env
  GEN_OIDC_CLIENT_ID="moe-sovereign"
  GEN_OIDC_CLIENT_SECRET="${GEN_AUTHENTIK_SECRET}"
  if [[ -n "${DOMAIN:-}" ]]; then
    GEN_OIDC_REDIRECT_URI="https://admin.${DOMAIN}/auth/callback"
    GEN_OIDC_ISSUER="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/"
    GEN_OIDC_JWKS_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/jwks/"
    GEN_OIDC_END_SESSION_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/end-session/"
  else
    GEN_OIDC_REDIRECT_URI="http://localhost:8088/auth/callback"
    GEN_OIDC_ISSUER="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/"
    GEN_OIDC_JWKS_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/jwks/"
    GEN_OIDC_END_SESSION_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/end-session/"
  fi
fi

# Build explicit --profile flags: podman-compose does not auto-read COMPOSE_PROFILES
# from .env; docker compose does. Passing --profile explicitly works for both.
_PROFILE_ARGS=()
[[ "$INSTALL_NEO4J"     == "true" ]] && _PROFILE_ARGS+=(--profile neo4j)
[[ "$INSTALL_CADDY"     == "true" ]] && _PROFILE_ARGS+=(--profile caddy)
[[ "$INSTALL_AUTHENTIK" == "true" ]] && _PROFILE_ARGS+=(--profile authentik)

_compose "${_PROFILE_ARGS[@]}" pull ${_Q} 2>/dev/null || true
_compose "${_PROFILE_ARGS[@]}" build ${_Q}
_compose "${_PROFILE_ARGS[@]}" up -d

if [[ "${INSTALL_ENTERPRISE_DATA_STACK:-false}" == "true" ]]; then
  echo ""
  echo "  Starting Enterprise Data Stack (NiFi, Marquez, lakeFS)..."
  _compose -f docker-compose.enterprise.yml pull ${_Q} 2>/dev/null || true
  _compose -f docker-compose.enterprise.yml up -d
  _bootstrap_enterprise_stack
  echo "  Enterprise Data Stack started ✓"
fi

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

if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  echo ""
  if [[ -n "${DOMAIN:-}" ]]; then
    echo "  Authentik:   https://sso.${DOMAIN}"
  else
    echo "  Authentik:   http://localhost:9000"
  fi
  echo "  Admin login: ${AUTHENTIK_BOOTSTRAP_EMAIL_INPUT}"
  echo ""
  echo "  OIDC is pre-configured via blueprint — no manual Authentik UI steps needed."
  echo "  Client ID:   moe-sovereign"
  echo "  Issuer:      ${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/"
  echo ""
  echo "  [!] Authentik needs ~60s to initialise and apply the blueprint on first start."
  echo "      The Admin UI SSO login will work automatically once Authentik is ready."
elif [[ "$INSTALL_SSO" == "true" ]]; then
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
echo "  Project: https://github.com/h3rb3rn/moe-sovereign"
echo "  Docs:    https://docs.moe-sovereign.org"
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

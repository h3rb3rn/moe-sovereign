#!/usr/bin/env bash
# MoE Sovereign — Proxmox / Debian / Ubuntu LXC bootstrap.
#
# Brings up a single-node orchestrator inside an unprivileged LXC container
# using rootless Podman + systemd Quadlet, plus Grafana Alloy as a native
# systemd service for log/metric shipping.
#
# Intended target: fresh Debian 12 or Ubuntu 22.04/24.04 LXC.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/moe-sovereign/moe-infra/main/deploy/lxc/setup.sh | sudo bash
#   # or, from a git checkout:
#   sudo VERSION=0.1.0 LOKI_URL=https://loki.example.com/loki/api/v1/push \
#        bash deploy/lxc/setup.sh
#
# Environment variables (all optional):
#   VERSION               Orchestrator image tag       (default: latest)
#   MOE_REGISTRY          OCI registry                 (default: ghcr.io/moe-sovereign)
#   MOE_USER              Service account name         (default: moe)
#   MOE_UID               Service account UID          (default: 1001)
#   LOKI_URL              Grafana Loki push endpoint   (default: empty → Alloy not installed)
#   TEMPO_URL             Tempo OTLP gRPC endpoint     (default: empty)
#   PROM_REMOTE_WRITE_URL Prometheus remote_write URL  (default: empty)
#   MOE_CLUSTER           Cluster label for logs       (default: lxc)
#   ALLOY_VERSION         Alloy release to install     (default: latest)

set -euo pipefail

VERSION="${VERSION:-latest}"
MOE_REGISTRY="${MOE_REGISTRY:-ghcr.io/moe-sovereign}"
MOE_USER="${MOE_USER:-moe}"
MOE_UID="${MOE_UID:-1001}"
MOE_CLUSTER="${MOE_CLUSTER:-lxc}"
ALLOY_VERSION="${ALLOY_VERSION:-latest}"
LOKI_URL="${LOKI_URL:-}"
TEMPO_URL="${TEMPO_URL:-}"
PROM_REMOTE_WRITE_URL="${PROM_REMOTE_WRITE_URL:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() { printf '\033[1;34m[moe-setup]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[moe-setup][ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root (sudo bash setup.sh). Rootless podman is installed for a dedicated service user."

# ---------- 1. Detect OS ----------
if ! command -v apt-get >/dev/null; then
  die "This script targets Debian/Ubuntu LXCs (apt-get not found)."
fi
export DEBIAN_FRONTEND=noninteractive

# ---------- 2. Minimal dependencies ----------
log "Installing minimal dependencies (podman, systemd user session support, curl, ca-certificates)…"
apt-get update -qq
apt-get install -y --no-install-recommends \
  podman uidmap slirp4netns fuse-overlayfs \
  systemd curl ca-certificates unzip \
  python3-minimal

# ---------- 3. Service user ----------
if ! id -u "${MOE_USER}" >/dev/null 2>&1; then
  log "Creating service user ${MOE_USER} (UID ${MOE_UID})…"
  useradd --system --create-home --shell /bin/bash \
          --uid "${MOE_UID}" "${MOE_USER}"
fi

# subuid/subgid for rootless podman user-namespaces
if ! grep -q "^${MOE_USER}:" /etc/subuid; then
  echo "${MOE_USER}:100000:65536" >> /etc/subuid
fi
if ! grep -q "^${MOE_USER}:" /etc/subgid; then
  echo "${MOE_USER}:100000:65536" >> /etc/subgid
fi

# Enable systemd user services to run without an active login shell —
# essential so the container survives reboots of the LXC.
log "Enabling linger for ${MOE_USER}…"
loginctl enable-linger "${MOE_USER}"

# ---------- 4. Prepare host paths ----------
MOE_HOME="/home/${MOE_USER}"
install -d -m 0750 -o "${MOE_USER}" -g "${MOE_USER}" \
  "${MOE_HOME}/moe/logs" \
  "${MOE_HOME}/moe/cache" \
  "${MOE_HOME}/moe/experts" \
  "${MOE_HOME}/.config/containers/systemd"

# Ship the expert YAMLs if we're running from a git checkout
if [[ -d "${REPO_ROOT}/configs/experts" ]]; then
  log "Copying expert templates from repo checkout…"
  cp -a "${REPO_ROOT}/configs/experts/." "${MOE_HOME}/moe/experts/" 2>/dev/null || true
  chown -R "${MOE_USER}:${MOE_USER}" "${MOE_HOME}/moe/experts"
fi

# Default orchestrator.env (operator can edit afterwards)
ORCH_ENV="${MOE_HOME}/moe/orchestrator.env"
if [[ ! -f "${ORCH_ENV}" ]]; then
  log "Writing default orchestrator.env (edit ${ORCH_ENV} for real deployments)…"
  cat > "${ORCH_ENV}" <<EOF
# MoE Sovereign orchestrator — rootless LXC profile
MOE_PROFILE=solo
# Data tier — configure via the Admin UI or here. Leave empty for embedded mode.
POSTGRES_CHECKPOINT_URL=
KAFKA_URL=
REDIS_URL=
NEO4J_URI=
CHROMA_HOST=
# Auth
JWT_ISSUER=http://localhost:8088
JWT_AUDIENCE=moe-orchestrator
EOF
  chown "${MOE_USER}:${MOE_USER}" "${ORCH_ENV}"
  chmod 0640 "${ORCH_ENV}"
fi

# ---------- 5. Install Quadlet unit ----------
QUADLET_SRC="${REPO_ROOT}/deploy/podman/systemd/moe-orchestrator.container"
QUADLET_DST="${MOE_HOME}/.config/containers/systemd/moe-orchestrator.container"
if [[ -f "${QUADLET_SRC}" ]]; then
  log "Installing Quadlet unit from ${QUADLET_SRC}…"
  install -m 0644 -o "${MOE_USER}" -g "${MOE_USER}" "${QUADLET_SRC}" "${QUADLET_DST}"
  # Allow the VERSION override to take effect
  sed -i "s|Image=ghcr.io/moe-sovereign/orchestrator:latest|Image=${MOE_REGISTRY}/orchestrator:${VERSION}|" "${QUADLET_DST}"
else
  die "Quadlet source not found at ${QUADLET_SRC} — run this script from a git checkout."
fi

# ---------- 6. Pull image as the service user ----------
log "Pulling ${MOE_REGISTRY}/orchestrator:${VERSION} (rootless)…"
sudo -u "${MOE_USER}" XDG_RUNTIME_DIR="/run/user/${MOE_UID}" \
  podman pull "${MOE_REGISTRY}/orchestrator:${VERSION}" || {
    log "WARNING: image pull failed — the systemd unit will retry at activation."
  }

# ---------- 7. Start the orchestrator via systemd (user scope) ----------
log "Reloading user systemd and starting moe-orchestrator.service…"
sudo -u "${MOE_USER}" XDG_RUNTIME_DIR="/run/user/${MOE_UID}" \
  systemctl --user daemon-reload
sudo -u "${MOE_USER}" XDG_RUNTIME_DIR="/run/user/${MOE_UID}" \
  systemctl --user enable --now moe-orchestrator.service || {
    log "Orchestrator failed to start — check: sudo -u ${MOE_USER} journalctl --user -u moe-orchestrator"
  }

# ---------- 8. Install Grafana Alloy (native systemd) ----------
if [[ -n "${LOKI_URL}" ]]; then
  log "Installing Grafana Alloy as systemd service…"

  if ! id -u alloy >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin alloy
  fi
  install -d -m 0755 /etc/alloy
  install -d -m 0750 -o alloy -g alloy /var/lib/alloy

  # Install Alloy binary
  TMP="$(mktemp -d)"
  ALLOY_URL="https://github.com/grafana/alloy/releases/${ALLOY_VERSION}/download/alloy-linux-amd64.zip"
  if [[ "${ALLOY_VERSION}" == "latest" ]]; then
    ALLOY_URL="https://github.com/grafana/alloy/releases/latest/download/alloy-linux-amd64.zip"
  fi
  log "Downloading Alloy from ${ALLOY_URL}…"
  curl -fsSL "${ALLOY_URL}" -o "${TMP}/alloy.zip"
  unzip -q "${TMP}/alloy.zip" -d "${TMP}"
  install -m 0755 "${TMP}/alloy-linux-amd64" /usr/local/bin/alloy
  rm -rf "${TMP}"

  # Ship config + environment file
  install -m 0644 "${REPO_ROOT}/deploy/alloy/alloy.river" /etc/alloy/config.river
  cat > /etc/default/alloy <<EOF
LOKI_URL=${LOKI_URL}
TEMPO_URL=${TEMPO_URL}
PROM_REMOTE_WRITE_URL=${PROM_REMOTE_WRITE_URL}
MOE_HOSTNAME=$(hostname)
MOE_CLUSTER=${MOE_CLUSTER}
EOF
  chmod 0640 /etc/default/alloy
  chown root:alloy /etc/default/alloy

  install -m 0644 "${REPO_ROOT}/deploy/alloy/alloy.systemd.service" /etc/systemd/system/alloy.service
  systemctl daemon-reload
  systemctl enable --now alloy.service
else
  log "LOKI_URL not set — skipping Grafana Alloy install. (Set LOKI_URL to ship logs.)"
fi

# ---------- 9. Summary ----------
cat <<EOF

╭─────────────────────────────────────────────────────────────╮
│ MoE Sovereign LXC bootstrap complete                        │
├─────────────────────────────────────────────────────────────┤
│ Profile      : solo (override in ${ORCH_ENV})
│ Orchestrator : http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000/health
│ Service user : ${MOE_USER} (UID ${MOE_UID})
│ Image        : ${MOE_REGISTRY}/orchestrator:${VERSION}
│ Logs         : journalctl -M ${MOE_USER}@ --user -u moe-orchestrator
│ Alloy        : $([[ -n "${LOKI_URL}" ]] && echo "active → ${LOKI_URL}" || echo "disabled (no LOKI_URL)")
╰─────────────────────────────────────────────────────────────╯

Next steps:
  1. Edit ${ORCH_ENV} to point at your data tier (Postgres/Kafka/etc.)
  2. sudo -u ${MOE_USER} XDG_RUNTIME_DIR=/run/user/${MOE_UID} \\
       systemctl --user restart moe-orchestrator
  3. Open the Admin UI (separate deployment) and register this node.
EOF

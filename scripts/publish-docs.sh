#!/usr/bin/env bash
# =============================================================================
#  publish-docs.sh — Build MkDocs site and deploy to web hosting
#
#  Supports two targets:
#    allinkl  — all-inkl.com shared hosting via SFTP/rsync-over-SSH
#    netcup   — netcup VPS / managed server via rsync-over-SSH
#
#  Usage:
#    bash scripts/publish-docs.sh allinkl [--dry-run]
#    bash scripts/publish-docs.sh netcup  [--dry-run]
#    bash scripts/publish-docs.sh both    [--dry-run]
#
#  Credentials are read from environment variables (or .env.publish):
#
#    ALLINKL_SSH_HOST   SSH/SFTP hostname (e.g. ssh.your-domain.all-inkl.com)
#    ALLINKL_SSH_USER   SSH username (e.g. www123_youraccount)
#    ALLINKL_SSH_PORT   SSH port (default: 22)
#    ALLINKL_REMOTE_DIR Remote path to docs webroot (e.g. /www/htdocs/docs/)
#
#    NETCUP_SSH_HOST    SSH hostname (e.g. your-server.netcup.net)
#    NETCUP_SSH_USER    SSH username (e.g. moe-sovereign)
#    NETCUP_SSH_PORT    SSH port (default: 22)
#    NETCUP_REMOTE_DIR  Remote path (e.g. /var/www/docs.moe-sovereign.org/)
#
#  Copy .env.publish.example to .env.publish and fill in your values.
#  The script loads .env.publish automatically if it exists.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SITE_DIR="$PROJECT_DIR/site"

TARGET="${1:-}"
DRY_RUN=false
if [[ "${2:-}" == "--dry-run" || "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

if [[ -z "$TARGET" || "$TARGET" == "--dry-run" ]]; then
  echo "Usage: $0 {allinkl|netcup|both} [--dry-run]"
  exit 1
fi

# ─── Load credentials from .env.publish ───────────────────────────────────────
ENV_FILE="$PROJECT_DIR/.env.publish"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

# ─── Defaults ─────────────────────────────────────────────────────────────────
ALLINKL_SSH_PORT="${ALLINKL_SSH_PORT:-22}"
NETCUP_SSH_PORT="${NETCUP_SSH_PORT:-22}"

# ─── Helper: rsync to a remote host ──────────────────────────────────────────
deploy_rsync() {
  local label="$1"
  local ssh_host="$2"
  local ssh_user="$3"
  local ssh_port="$4"
  local remote_dir="$5"

  echo ""
  echo "──────────────────────────────────────────────────"
  echo "  Deploying to: $label"
  echo "  Target: ${ssh_user}@${ssh_host}:${remote_dir}"
  echo "──────────────────────────────────────────────────"

  local rsync_args=(
    -avz
    --delete
    --checksum
    -e "ssh -p ${ssh_port} -o StrictHostKeyChecking=accept-new"
  )

  if $DRY_RUN; then
    rsync_args+=(--dry-run)
    echo "  [DRY RUN] No files will be transferred."
  fi

  rsync "${rsync_args[@]}" "$SITE_DIR/" "${ssh_user}@${ssh_host}:${remote_dir}"
  echo "  Done ✓"
}

# ─── Step 1: Build MkDocs site ────────────────────────────────────────────────
echo "════════════════════════════════════════════════════"
echo "  MoE Sovereign — Publish Docs"
echo "  Target: $TARGET"
echo "════════════════════════════════════════════════════"
echo ""

if $DRY_RUN; then
  echo "[DRY RUN] Skipping MkDocs build — using existing site/ if present."
  if [[ ! -d "$SITE_DIR" ]]; then
    echo "[ERROR] No existing site/ directory. Run without --dry-run once first."
    exit 1
  fi
else
  echo "[1/2] Building MkDocs site..."
  cd "$PROJECT_DIR"
  if ! command -v mkdocs &>/dev/null; then
    echo "[ERROR] mkdocs not found. Run: pip install mkdocs-material"
    exit 1
  fi
  mkdocs build --strict 2>&1 | tail -5
  echo "  Build complete ✓ ($(du -sh "$SITE_DIR" | awk '{print $1}'))"
fi

# ─── Step 2: Deploy ──────────────────────────────────────────────────────────
echo ""
echo "[2/2] Deploying..."

deploy_allinkl() {
  : "${ALLINKL_SSH_HOST:?ALLINKL_SSH_HOST not set}"
  : "${ALLINKL_SSH_USER:?ALLINKL_SSH_USER not set}"
  : "${ALLINKL_REMOTE_DIR:?ALLINKL_REMOTE_DIR not set}"
  deploy_rsync "all-inkl.com" \
    "$ALLINKL_SSH_HOST" "$ALLINKL_SSH_USER" "$ALLINKL_SSH_PORT" "$ALLINKL_REMOTE_DIR"
}

deploy_netcup() {
  : "${NETCUP_SSH_HOST:?NETCUP_SSH_HOST not set}"
  : "${NETCUP_SSH_USER:?NETCUP_SSH_USER not set}"
  : "${NETCUP_REMOTE_DIR:?NETCUP_REMOTE_DIR not set}"
  deploy_rsync "netcup" \
    "$NETCUP_SSH_HOST" "$NETCUP_SSH_USER" "$NETCUP_SSH_PORT" "$NETCUP_REMOTE_DIR"
}

case "$TARGET" in
  allinkl) deploy_allinkl ;;
  netcup)  deploy_netcup  ;;
  both)    deploy_allinkl; deploy_netcup ;;
  *)
    echo "[ERROR] Unknown target: $TARGET  (use allinkl, netcup, or both)"
    exit 1
    ;;
esac

echo ""
echo "════════════════════════════════════════════════════"
if $DRY_RUN; then
  echo "  DRY RUN complete. No files were transferred."
else
  echo "  Deployment complete!"
  echo ""
  echo "  docs.moe-sovereign.org should be live within seconds."
  echo "  Hard-refresh the browser (Ctrl+Shift+R) to bypass CDN cache."
fi
echo "════════════════════════════════════════════════════"

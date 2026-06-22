#!/usr/bin/env bash
# =============================================================================
#  sync-to-publish.sh — Sync moe-infra dev repo to publish-ready GitHub repo
#
#  Usage: bash scripts/sync-to-publish.sh [--dry-run]
#
#  Copies all code from the dev repo (/opt/moe-infra)
#  to the GitHub publish repo (/opt/deployment/Github/moe-sovereign),
#  excluding sensitive files, runtime data, and local configuration.
#  After sync, sanitizes hardcoded paths and checks for leaked secrets.
# =============================================================================
set -euo pipefail

DEV_DIR="/opt/moe-infra"
PUB_DIR="/opt/deployment/Github/moe-sovereign"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "[DRY RUN] No files will be modified."
fi

# Verify both directories exist
if [[ ! -d "$DEV_DIR" ]]; then
  echo "[ERROR] Dev directory not found: $DEV_DIR"
  exit 1
fi
if [[ ! -d "$PUB_DIR" ]]; then
  echo "[ERROR] Publish directory not found: $PUB_DIR"
  exit 1
fi

echo "════════════════════════════════════════════════════"
echo "  MoE Sovereign — Sync: DEV → PUBLISH"
echo "  From: $DEV_DIR"
echo "  To:   $PUB_DIR"
echo "════════════════════════════════════════════════════"

# ─── Step 1: rsync (exclude sensitive files) ──────────────────────────────

RSYNC_EXCLUDES=(
  --exclude='.git'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='!.env.example'
  --exclude='*.db'
  --exclude='*.sqlite*'
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='node_modules'
  --exclude='.claude/'
  --exclude='*.log'
  --exclude='data/'
  --exclude='.vscode/'
  --exclude='.idea/'
  --exclude='*.pem'
  --exclude='*.key'
  --exclude='*.cert'
  --exclude='registry-cache/'
  --exclude='docker-compose.override.yml'
  --exclude='.pytest_cache/'
  --exclude='.ai_context.md'
  --exclude='tmp/'
  --exclude='tmp_install/'
  --exclude='prometheus/*_targets.yml'
)

echo ""
echo "[1/4] Syncing files..."

if $DRY_RUN; then
  rsync -avn --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PUB_DIR/" | tail -20
  echo "  ... (showing last 20 lines of dry-run output)"
else
  rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PUB_DIR/"
  echo "  Files synced ✓"
fi

# ─── Step 2: Sanitize hardcoded paths ─────────────────────────────────────

echo "[2/4] Sanitizing hardcoded paths..."

SANITIZE_PATTERNS=(
  "/opt/moe-infra|/opt/moe-infra|*.py,*.sh,*.service"
  "/opt/moe-sovereign|/opt/moe-sovereign|*.py,*.sh,*.service"
)

if ! $DRY_RUN; then
  for entry in "${SANITIZE_PATTERNS[@]}"; do
    IFS='|' read -r pattern replacement globs <<< "$entry"
    IFS=',' read -ra glob_arr <<< "$globs"
    for g in "${glob_arr[@]}"; do
      find "$PUB_DIR" -name "$g" -not -path '*/.git/*' -exec \
        sed -i "s|${pattern}|${replacement}|g" {} + 2>/dev/null || true
    done
  done
  echo "  Paths sanitized ✓"
else
  echo "  (skipped in dry-run)"
fi

# ─── Step 3: Sanitize hardcoded IPs ───────────────────────────────────────

echo "[3/4] Checking for leaked IPs..."

LEAKED=$(grep -rn "192\.168\.155\." "$PUB_DIR" \
  --include="*.py" --include="*.yml" --include="*.yaml" \
  --include="*.json" --include="*.sh" --include="*.service" \
  2>/dev/null | grep -v "\.git/" || true)

if [[ -n "$LEAKED" ]]; then
  echo "  ⚠️  WARNING: Real IPs found in publish repo!"
  echo "$LEAKED" | head -10
  echo ""
  echo "  Fix these manually before pushing."
else
  echo "  No leaked IPs found ✓"
fi

# ─── Step 4: Check for leaked secrets ─────────────────────────────────────

echo "[4/4] Checking for leaked secrets..."

# File types to scan — include common config formats and .env variants.
SCAN_INCLUDES=(
  --include='*.py' --include='*.yml' --include='*.yaml' --include='*.json'
  --include='*.sh' --include='*.service' --include='*.toml' --include='*.ini'
  --include='*.conf' --include='*.cfg' --include='*.env' --include='*.properties'
  --include='Dockerfile*' --include='docker-compose*.yml'
)

# Exclusions: never flag fixture/example files or git internals.
SCAN_EXCLUDES=(
  --exclude-dir='.git'
  --exclude-dir='node_modules'
  --exclude-dir='__pycache__'
  --exclude='*.example'
  --exclude='*.sample'
  --exclude='*.template'
)

# Credential patterns to detect. Each line: LABEL|REGEX (grep -E compatible).
readonly CRED_PATTERNS=(
  "MOE-SK|\\bmoe-sk-[a-f0-9]{32,}"
  "LBK-ADMIN|\\blbk-[a-f0-9]{32,}"
  "SMTP-PASS|^[[:space:]]*SMTP_PASS(WORD)?[[:space:]]*=[[:space:]]*[^[:space:]\"'\\\$#][[:graph:]]{7,}"
  "SMTP-USER|^[[:space:]]*SMTP_USER[[:space:]]*=[[:space:]]*[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"
  "MAIL-PASS|^[[:space:]]*(MAIL|EMAIL)_PASS(WORD)?[[:space:]]*=[[:space:]]*[^[:space:]\"'\\\$#][[:graph:]]{7,}"
  "APP-PASSWORD|app[_-]password[[:space:]]*[:=][[:space:]]*['\"][A-Za-z0-9]{16,}['\"]"
  "SENDGRID|SG\\.[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{30,}"
  "MAILGUN|key-[a-f0-9]{32}"
  "POSTMARK|POSTMARK_(API|SERVER)_TOKEN[[:space:]]*=[[:space:]]*[0-9a-f-]{30,}"
  "BREVO|xkeysib-[a-f0-9]{60,}"
  "MAILJET|[A-Fa-f0-9]{32}-[A-Fa-f0-9]{32}.*(mailjet|MJ_APIKEY)"
  "AWS-AKIA|(AKIA|ASIA)[A-Z0-9]{16}"
  "AWS-SECRET|aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{40}"
  "GH-PAT|gh[opsur]_[A-Za-z0-9]{36,251}"
  "GH-CLASSIC|\\bghp_[A-Za-z0-9]{36}\\b"
  "JWT|\\beyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\b"
  "GENERIC-PASS|^[[:space:]]*[A-Z_][A-Z0-9_]*_?(PASS|PASSWORD|SECRET|TOKEN|KEY)[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=_.!@#%^&*-]{16,}"
)

readonly PLACEHOLDER_RE='(change[-_]?me|your[-_]?(key|password|token|secret)|example|placeholder|dummy|changeit|xxxxxxxx|\\*\\*\\*|<[^>]*>|\\$\\{|REPLACE|TODO)'

SECRETS=""
for entry in "${CRED_PATTERNS[@]}"; do
  label="${entry%%|*}"
  pattern="${entry#*|}"
  hits=$(grep -rInE "${pattern}" "${SCAN_INCLUDES[@]}" "${SCAN_EXCLUDES[@]}" "$PUB_DIR" 2>/dev/null \
         | grep -viE "$PLACEHOLDER_RE" || true)
  if [[ -n "$hits" ]]; then
    SECRETS="${SECRETS}
[$label]
$hits"
  fi
done

if [[ -n "$SECRETS" ]]; then
  echo "  ⚠️  WARNING: Potential real secrets found!"
  printf '%s\n' "$SECRETS" | head -30
  echo ""
  echo "  Fix these manually before pushing."
else
  echo "  No leaked secrets found ✓"
fi

# ─── Summary ──────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════"
if $DRY_RUN; then
  echo "  DRY RUN complete. No files were modified."
  echo "  Run without --dry-run to apply changes."
else
  PUB_SIZE=$(du -sh "$PUB_DIR" --exclude=.git | awk '{print $1}')
  FILE_COUNT=$(find "$PUB_DIR" -type f -not -path '*/.git/*' | wc -l)
  echo "  Sync complete!"
  echo "  Files: $FILE_COUNT | Size: $PUB_SIZE"
  echo ""
  echo "  Next steps:"
  echo "    cd $PUB_DIR"
  echo "    git add -A"
  echo "    git diff --cached --stat"
  echo "    git commit -m 'Sync from dev: <description>'"
  echo "    git push origin <branch-name>"
fi
echo "════════════════════════════════════════════════════"

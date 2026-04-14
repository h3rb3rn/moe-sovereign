#!/usr/bin/env bash
# run_security_scan.sh — MoE Sovereign DevSecOps pipeline
#
# Runs two security checks:
#   1. SAST  — Bandit (Python static analysis for common security issues)
#   2. SCA   — Trivy  (dependency vulnerability scan, run via Docker to keep host clean)
#
# Exit code: 0 if all checks pass, 1 if any check finds issues.
#
# Usage:
#   bash scripts/run_security_scan.sh
#   SEVERITY=MEDIUM,HIGH,CRITICAL bash scripts/run_security_scan.sh

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'  # reset

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()    { echo -e "${RED}[FAIL]${NC}  $*" >&2; }
header()  { echo -e "\n${BOLD}$*${NC}"; }

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Trivy severity level (comma-separated, overridable via env var).
SEVERITY="${SEVERITY:-HIGH,CRITICAL}"

OVERALL_EXIT=0

# ── SAST: Bandit ──────────────────────────────────────────────────────────────
header "=== SAST: Bandit (Python static analysis) ==="
info "Target: ${PROJECT_ROOT}"

if ! command -v bandit &>/dev/null; then
    warn "bandit not found in PATH — installing via pip..."
    pip install --quiet bandit
fi

# Exclude auto-generated site/, vendored venvs, and test fixtures to keep
# the report focused on first-party code.
BANDIT_EXCLUDE="${PROJECT_ROOT}/.venv,\
${PROJECT_ROOT}/tmp_install,\
${PROJECT_ROOT}/site,\
${PROJECT_ROOT}/tests"

if bandit -r "${PROJECT_ROOT}" \
          --exclude "${BANDIT_EXCLUDE}" \
          -l \
          -f text; then
    info "Bandit: no issues found."
else
    fail "Bandit found one or more security issues (see above)."
    OVERALL_EXIT=1
fi

# ── SCA: Trivy (via Docker — host OS stays clean) ────────────────────────────
header "=== SCA: Trivy (dependency vulnerability scan) ==="
info "Severity filter: ${SEVERITY}"

if ! command -v docker &>/dev/null; then
    fail "Docker is required to run Trivy but is not installed or not in PATH."
    exit 1
fi

# Bind-mount the project as read-only so Trivy scans lock files and images
# without needing write access to the host.
if docker run --rm \
       -v "${PROJECT_ROOT}:/app:ro" \
       aquasec/trivy fs /app \
       --exit-code 1 \
       --severity "${SEVERITY}" \
       --scanners vuln \
       --quiet; then
    info "Trivy: no ${SEVERITY} vulnerabilities found."
else
    fail "Trivy found ${SEVERITY} vulnerabilities in dependencies (see above)."
    OVERALL_EXIT=1
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [ "${OVERALL_EXIT}" -eq 0 ]; then
    info "${BOLD}All security checks passed.${NC}"
else
    fail "${BOLD}One or more security checks failed. Review the output above.${NC}"
fi

exit "${OVERALL_EXIT}"

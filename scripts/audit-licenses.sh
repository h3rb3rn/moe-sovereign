#!/usr/bin/env bash
# License hygiene audit for the MoE Sovereign stack.
#
# Checks docker-compose*.yml and requirements*.txt against the
# OSI-compatibility allowlist and the non-OSI blocklist defined in
# docs/system/license_compliance.md.
#
# Exit codes:
#   0 — clean (no blocklist hits)
#   1 — at least one blocklist hit found (prints details)
#   2 — argument error or file missing

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ----- Blocklist patterns ---------------------------------------------------
# Image-name globs and substrings that indicate a non-OSI-compatible licence.
# Pattern: "<image-substring>|<reason>"
BLOCK_IMAGES=(
    "confluentinc/cp-|Confluent Community License — use apache/kafka instead"
    "redpandadata/redpanda|Business Source License — use apache/kafka instead"
    "elasticsearch:|SSPL/Elastic License v2 — use opensearchproject/opensearch instead"
    "elastic/elasticsearch|SSPL/Elastic License v2 — use opensearchproject/opensearch instead"
    "mongo:|SSPL — use postgres or ferretdb instead"
    "mongodb/|SSPL — use postgres or ferretdb instead"
    "hashicorp/terraform|BSL 1.1 — use opentofu/opentofu instead"
    "hashicorp/vault|BSL 1.1 — use openbao/openbao instead"
    "hashicorp/consul|BSL 1.1 — evaluate Apache 2.0 alternative"
    "hashicorp/nomad|BSL 1.1 — evaluate Apache 2.0 alternative"
    "cockroachdb/cockroach|BSL 1.1 — use postgres + citus instead"
    "outline/outline|BSL 1.1 — use hedgedoc/hedgedoc instead"
    "airbyte/airbyte|Elastic License v2 — use meltano/meltano instead"
    "redis:|SSPL/RSALv2 since 7.4 — use valkey/valkey instead"
    "redis/redis-stack|SSPL/RSALv2 — use valkey/valkey instead"
)

# ----- Requirements blocklist (Python package names that pull non-OSI deps) -
BLOCK_PIP_PACKAGES=(
    "elasticsearch>=8"
    "elastic-transport"
)

# ----- Helpers --------------------------------------------------------------
hits=0
warn() { printf "  ❌ %s\n" "$1"; hits=$((hits + 1)); }
ok()   { printf "  ✅ %s\n" "$1"; }

# ----- Scan compose files ---------------------------------------------------
printf "\n[1] Scanning docker-compose files for blocklisted images …\n"
shopt -s nullglob
compose_files=("$ROOT"/docker-compose*.yml)
shopt -u nullglob

if (( ${#compose_files[@]} == 0 )); then
    echo "  (no compose files found)"
else
    for f in "${compose_files[@]}"; do
        printf "  scanning %s\n" "$(basename "$f")"
        # Skip lines beginning with `#` (commented) but include indented `image:` lines
        mapfile -t image_lines < <(grep -E "^\s*image:" "$f" | grep -v "^\s*#")
        for line in "${image_lines[@]}"; do
            # Extract image identifier (strip "image:" key)
            image="${line#*image:}"
            image="${image// /}"
            image="${image//\"/}"
            for pattern_entry in "${BLOCK_IMAGES[@]}"; do
                pattern="${pattern_entry%%|*}"
                reason="${pattern_entry#*|}"
                if [[ "$image" == *"$pattern"* ]]; then
                    warn "$(basename "$f"): image '$image' → $reason"
                fi
            done
        done
    done
    if (( hits == 0 )); then
        ok "no blocklisted images detected"
    fi
fi

# ----- Scan requirements files ---------------------------------------------
printf "\n[2] Scanning requirements files for blocklisted Python packages …\n"
shopt -s nullglob
req_files=("$ROOT"/requirements*.txt "$ROOT"/*/requirements*.txt)
shopt -u nullglob

req_hits_before="$hits"
if (( ${#req_files[@]} == 0 )); then
    echo "  (no requirements files found)"
else
    for f in "${req_files[@]}"; do
        for pkg in "${BLOCK_PIP_PACKAGES[@]}"; do
            if grep -qE "^${pkg}([^a-zA-Z0-9_-]|\$)" "$f" 2>/dev/null; then
                warn "$(realpath --relative-to="$ROOT" "$f"): blocklisted dependency '$pkg'"
            fi
        done
    done
    if (( hits == req_hits_before )); then
        ok "no blocklisted Python packages detected"
    fi
fi

# ----- Report ---------------------------------------------------------------
printf "\n"
if (( hits == 0 )); then
    printf "✅ License hygiene check passed — 0 blocklist hits.\n"
    exit 0
else
    printf "❌ License hygiene check FAILED — %d blocklist hit(s) above.\n" "$hits"
    printf "   See docs/system/license_compliance.md for replacement guidance.\n"
    exit 1
fi

#!/bin/bash
# Pull community skills from Anthropic official repository
# Copies SKILL.md files from skills/<name>/SKILL.md → community/<name>.md
#
# Usage:
#   bash scripts/pull_community_skills.sh [--dry-run]

set -euo pipefail

SKILLS_DIR="${SKILLS_DIR:-./skills}"
COMMUNITY_DIR="$SKILLS_DIR/community"
TEMP_DIR="/tmp/skill_pull_$$"
DRY_RUN="${1:-}"

mkdir -p "$COMMUNITY_DIR"
mkdir -p "$TEMP_DIR"

echo "=== Community Skills Pull $(date) ==="
echo "Target: $COMMUNITY_DIR"

ADDED=0
SKIPPED=0
UPDATED=0

# ─── Anthropic Official Skills ───────────────────────────────────────────────
echo ""
echo "--- Cloning github.com/anthropics/skills ---"
if git clone --depth=1 --quiet https://github.com/anthropics/skills.git "$TEMP_DIR/anthropic" 2>/dev/null; then
    for skill_dir in "$TEMP_DIR/anthropic/skills"/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name=$(basename "$skill_dir")
        skill_file="$skill_dir/SKILL.md"
        [ -f "$skill_file" ] || continue

        target="$COMMUNITY_DIR/${skill_name}.md"

        # Skip if already in built-in skills (our own version takes priority)
        if [ -f "$SKILLS_DIR/${skill_name}.md" ]; then
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        if [ "$DRY_RUN" = "--dry-run" ]; then
            echo "  [DRY] $skill_name"
        else
            cp "$skill_file" "$target"
            # Also copy reference files if they exist (some skills have /references/)
            ref_dir="$skill_dir/references"
            if [ -d "$ref_dir" ]; then
                mkdir -p "$COMMUNITY_DIR/${skill_name}_refs"
                cp "$ref_dir"/*.md "$COMMUNITY_DIR/${skill_name}_refs/" 2>/dev/null || true
            fi
            if [ -f "$target" ]; then
                ADDED=$((ADDED + 1))
                echo "  + $skill_name"
            fi
        fi
    done

    # Also grab the spec
    if [ -f "$TEMP_DIR/anthropic/spec/agent-skills-spec.md" ]; then
        cp "$TEMP_DIR/anthropic/spec/agent-skills-spec.md" "$COMMUNITY_DIR/_SPEC.md"
        echo "  + _SPEC (agent skills specification)"
    fi

    echo ""
    echo "  Added: $ADDED | Skipped (built-in exists): $SKIPPED"
else
    echo "  ERROR: Could not clone anthropics/skills"
fi

# ─── Cleanup ────────────────────────────────────────────────────────────────
rm -rf "$TEMP_DIR"

echo ""
echo "=== Results ==="
TOTAL=$(ls "$COMMUNITY_DIR"/*.md 2>/dev/null | wc -l)
echo "  Community skills: $TOTAL files in $COMMUNITY_DIR"
ls "$COMMUNITY_DIR"/*.md 2>/dev/null | while read f; do
    name=$(basename "$f" .md)
    desc=$(grep -oP 'description:\s*\K.*' "$f" 2>/dev/null | head -1 | cut -c1-60)
    printf "    %-25s %s\n" "$name" "${desc:-(no description)}"
done

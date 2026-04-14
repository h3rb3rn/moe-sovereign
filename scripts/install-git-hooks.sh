#!/usr/bin/env bash
# Install repo-local git hooks that prevent secret leaks.
# Run once after cloning: bash scripts/install-git-hooks.sh
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="${REPO_ROOT}/.git/hooks/pre-commit"

cat > "$HOOK" << 'HOOK_EOF'
#!/usr/bin/env bash
# Block commits that stage .env files or high-entropy credential patterns.
set -euo pipefail

# 1. Hard-block any .env file except .env.example/.env.sample/.env.template
BAD_ENV=$(git diff --cached --name-only --diff-filter=AM | \
  grep -E '(^|/)\.env([a-zA-Z0-9._-]*)?$' | \
  grep -vE '\.(example|sample|template)$' || true)
if [[ -n "$BAD_ENV" ]]; then
  echo "[pre-commit] BLOCKED: .env files must not be committed:"
  printf '  %s\n' $BAD_ENV
  echo "  (add them to .gitignore, use .env.example for templates)"
  exit 1
fi

# 2. Scan diff for obvious credential patterns in any staged file
PATTERNS=(
  'moe-sk-[a-f0-9]{32,}'
  'lbk-[a-f0-9]{32,}'
  'AKIA[A-Z0-9]{16}'
  'gh[opsur]_[A-Za-z0-9]{36,}'
  'sk-ant-[A-Za-z0-9_-]{40,}'
  'SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{30,}'
  'xkeysib-[a-f0-9]{60,}'
)
PLACEHOLDER='(YOUR_KEY_HERE|change[-_]?me|xxxxxxxx|48_hex_chars|<[^>]+>|\$\{)'
FOUND=""
for p in "${PATTERNS[@]}"; do
  hits=$(git diff --cached -U0 | grep -E "^\+" | grep -Ev "^\+\+\+" | grep -E "$p" | grep -vE "$PLACEHOLDER" || true)
  if [[ -n "$hits" ]]; then
    FOUND="${FOUND}
[$p]
$hits"
  fi
done
if [[ -n "$FOUND" ]]; then
  echo "[pre-commit] BLOCKED: potential secret in staged changes:"
  echo "$FOUND" | head -20
  echo "(bypass with --no-verify only if you are sure)"
  exit 1
fi
HOOK_EOF
chmod +x "$HOOK"
echo "Installed $HOOK"

"""
Environment variable documentation contract.

Every os.getenv("KEY") call in main.py MUST either:
  a) have a corresponding entry in .env.example, OR
  b) be in the KNOWN_UNDOCUMENTED baseline (advanced internals with sensible defaults)

The test is a "no new undocumented vars" gate: the baseline captures the current
technical debt, but adding new env vars without documenting them fails the test.

To add a new env var legitimately without documenting it, add it to KNOWN_UNDOCUMENTED
with a comment explaining why it's intentionally omitted.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_MAIN_PY    = _ROOT / "main.py"
_CONFIG_PY  = _ROOT / "config.py"
_ENV_EXAMPLE = _ROOT / ".env.example"

# Env vars that are legitimately not in .env.example:
_KNOWN_IMPLICIT = {
    "HOME", "USER", "PATH", "SHELL", "TERM",
    "ENV_FILE",    # test-only, set by conftest.py
    "EDGE_MODE",   # test-only flag (MemorySaver), not a prod setting
}

# Baseline: vars currently used in main.py but not yet documented in .env.example.
# This is existing technical debt — do NOT add new vars here without also
# documenting them in .env.example. Shrinking this set is always welcome.
_KNOWN_UNDOCUMENTED = {
    # Advanced inference / routing internals (have sensible defaults in code)
    "AIHUB_FALLBACK_MODEL", "AIHUB_FALLBACK_MODEL_SECOND", "AIHUB_FALLBACK_NODE",
    "BENCHMARK_SHADOW_RATE", "BENCHMARK_SHADOW_TEMPLATE",
    "CACHE_HIT_THRESHOLD", "CORRECTION_MEMORY_ENABLED",
    "EVAL_CACHE_FLAG_THRESHOLD", "EXPERT_MIN_DATAPOINTS", "EXPERT_MIN_SCORE",
    "EXPERT_TEMPLATES", "CUSTOM_EXPERT_PROMPTS",
    "FEEDBACK_NEGATIVE_THRESHOLD", "FEEDBACK_POSITIVE_THRESHOLD",
    "FUZZY_GRAPH_THRESHOLD", "FUZZY_VECTOR_THRESHOLD",
    "GRAPH_COMPRESS_LLM", "GRAPH_COMPRESS_LLM_TIMEOUT",
    "GRAPH_COMPRESS_THRESHOLD_FACTOR", "GRAPH_INGEST_ENDPOINT",
    "GRAPH_INGEST_MODEL", "GRAPH_VIA_MCP",
    "HISTORY_MAX_CHARS", "HISTORY_MAX_ENTRIES", "HISTORY_MAX_TURNS",
    "JUDGE_REFINE_MAX_ROUNDS", "JUDGE_REFINE_MIN_IMPROVEMENT",
    "MAX_EXPERT_OUTPUT_CHARS", "MCP_URL",
    "PLANNER_MAX_TASKS", "PLANNER_RETRIES",
    "REASONING_MAX_TOKENS", "ROUTE_GAP", "ROUTE_THRESHOLD",
    "SOFT_CACHE_MAX_EXAMPLES", "SSE_CHUNK_SIZE",
    "THOMPSON_LOAD_PENALTY", "THOMPSON_SAMPLING_ENABLED", "TOOL_MAX_TOKENS",
    "WEB_SEARCH_FALLBACK_DDG",
    # Auth / OIDC — partially documented via comments, full vars in Authentik section
    "AUTHENTIK_URL", "OIDC_CLIENT_ID", "OIDC_ISSUER", "OIDC_JWKS_URL",
    # Infrastructure URLs — auto-constructed from other env vars in install.sh
    "KAFKA_URL", "LITELLM_URL", "MOE_USERDB_URL", "NEO4J_URI", "NEO4J_USER",
    "POSTGRES_CHECKPOINT_URL", "REDIS_URL",
    # Environment flag (dev/prod/staging)
    "ENVIRONMENT",
    # CORS — secondary options
    "CORS_ORIGINS",
    # Claude Code integration (already in .env.example as comments)
    "CLAUDE_CODE_MODE", "CLAUDE_CODE_MODELS", "CLAUDE_CODE_PROFILES",
    "CLAUDE_CODE_REASONING_ENDPOINT", "CLAUDE_CODE_REASONING_MODEL",
    "CLAUDE_CODE_TOOL_CHOICE", "CLAUDE_CODE_TOOL_ENDPOINT", "CLAUDE_CODE_TOOL_MODEL",
}


def _vars_used_in_main() -> set[str]:
    src = (_MAIN_PY.read_text(encoding="utf-8")
           + _CONFIG_PY.read_text(encoding="utf-8"))
    return set(re.findall(r'os\.getenv\("([A-Z_][A-Z0-9_]*)(?:"|\s*,)', src))


def _vars_in_env_example() -> set[str]:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    # Match both active (KEY=...) and commented-out (# KEY=...) lines —
    # commented entries count as documented (user just needs to uncomment).
    return set(re.findall(r'^#?\s*([A-Z_][A-Z0-9_]*)=', text, re.MULTILINE))


def test_no_new_undocumented_env_vars():
    """
    No env var may be added to main.py without being documented in .env.example
    OR added to the KNOWN_UNDOCUMENTED baseline above.
    """
    used = _vars_used_in_main()
    documented = _vars_in_env_example()
    allowed_gap = _KNOWN_IMPLICIT | _KNOWN_UNDOCUMENTED
    new_undocumented = used - documented - allowed_gap

    assert not new_undocumented, (
        "Neue Env-Vars in main.py ohne Dokumentation in .env.example:\n"
        "  Option A: In .env.example eintragen (bevorzugt)\n"
        "  Option B: In _KNOWN_UNDOCUMENTED ergänzen (nur für interne Variablen)\n\n"
        + "\n".join(f"  {v}=" for v in sorted(new_undocumented))
    )


def test_known_undocumented_baseline_does_not_grow_silently():
    """Track the size of the technical-debt baseline. Alert if it grows."""
    used = _vars_used_in_main()
    documented = _vars_in_env_example()
    actual_gap = used - documented - _KNOWN_IMPLICIT

    # Vars in baseline but no longer used → can be removed from baseline
    stale_in_baseline = _KNOWN_UNDOCUMENTED - actual_gap
    if stale_in_baseline:
        pass  # Technical debt shrank — these can be removed from _KNOWN_UNDOCUMENTED

    # Vars actually undocumented but not in baseline → test_no_new catches these
    assert len(actual_gap) <= len(_KNOWN_UNDOCUMENTED) + 5, (
        f"Technische Schuld wächst: {len(actual_gap)} undokumentierte Vars "
        f"(Baseline: {len(_KNOWN_UNDOCUMENTED)}). Bitte .env.example aktualisieren."
    )


def test_env_example_credentials_are_placeholders():
    """
    PASSWORD, SECRET, KEY entries in .env.example must be placeholder strings,
    not real credentials. Excludes pricing, timeout, and rate config keys.
    """
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")

    # Only flag keys that look like actual credentials (not metrics/config numbers)
    credential_pattern = re.compile(
        r'^([A-Z_]*(?:PASSWORD|SECRET|(?<!PRICE_)(?<!_RATE)(?<!_KEY_ID)KEY)[A-Z_]*)=(.+)$',
        re.MULTILINE,
    )

    real_values = []
    for name, value in credential_pattern.findall(text):
        stripped = value.strip()
        # Acceptable placeholder patterns
        safe_patterns = [
            r'^$',
            r'.*change[_-]?me.*',      # changeme / change-me / nifi-changeme-pwd
            r'^your[_-]',
            r'^<.+>$',
            r'^sk-dein',
            r'^AKIA[A-Z0-9]{16}$',     # well-known fake AWS key format
            r'^wJalrX',                 # well-known AWS example secret
            r'EXAMPLE',                 # explicitly named example values
            r'^ollama$',               # Ollama default (no real auth)
        ]
        if not any(re.search(p, stripped, re.IGNORECASE) for p in safe_patterns):
            real_values.append(f"  {name}={stripped[:30]}...")

    assert not real_values, (
        ".env.example enthält möglicherweise echte Zugangsdaten (kein Placeholder-Muster erkannt):\n"
        + "\n".join(real_values)
    )


def test_env_example_file_exists():
    assert _ENV_EXAMPLE.exists()


def test_main_py_file_exists():
    assert _MAIN_PY.exists()

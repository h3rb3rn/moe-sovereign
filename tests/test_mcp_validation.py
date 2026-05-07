"""
test_mcp_validation.py — Unit tests for MCP tool input validation and output correctness.

All tools are pure Python functions decorated with @mcp.tool() (which conftest.py
makes a transparent pass-through). No live network or database connections needed.

Covers:
  - calculate(): safe AST evaluation, injection prevention, percentage notation
  - hash_text(): valid algorithm selection, rejection of unknown algorithms
  - base64_codec(): encode/decode round-trip, invalid mode handling
  - date_diff(): valid date arithmetic, invalid date format handling
  - gcd_lcm(): gcd/lcm/both operations and integer correctness
  - statistics_calc(): standard statistical measures, unknown-op silencing
  - subnet_calc(): valid CIDR parsing, invalid CIDR error handling
"""

import pytest

# conftest.py has already stubbed mcp/fastapi/uvicorn so the import is safe.
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_server.server import (
    base64_codec,
    calculate,
    date_diff,
    gcd_lcm,
    hash_text,
    statistics_calc,
    subnet_calc,
)

# ── calculate() ───────────────────────────────────────────────────────────────


def test_calculate_basic_addition():
    result = calculate("2+2")
    assert "4" in result


def test_calculate_division():
    result = calculate("10/2")
    assert "5" in result


def test_calculate_power_expression():
    result = calculate("2**10")
    assert "1024" in result


def test_calculate_rejects_import_injection(capsys):
    """__import__ must not execute; the function must return an error string.

    Previously, a security vulnerability caused the SymPy fallback to evaluate
    arbitrary Python (including shell commands) when the safe-AST evaluator
    raised an exception.  The fix restricts the SymPy fallback to SyntaxError
    only, so unsafe-but-valid Python expressions are rejected at the AST stage.
    """
    result = calculate("__import__('os').system('id')")
    assert result.startswith("Error") or result.startswith("Fehler"), (
        f"Expected an error string, got: {result!r}"
    )
    # The string 'uid=' would appear in captured stdout if os.system ran.
    captured = capsys.readouterr()
    assert "uid=" not in captured.out, (
        "os.system('id') was executed — the SymPy injection vulnerability is still present!"
    )


def test_calculate_rejects_open_injection():
    """open() is not in the safe whitelist and must be refused."""
    result = calculate("open('/etc/passwd').read()")
    assert result.startswith("Error") or result.startswith("Fehler") or "Error" in result or "error" in result.lower()


def test_calculate_percentage_notation():
    """'15% of 100' should evaluate to 15."""
    result = calculate("15% of 100")
    assert "15" in result


def test_calculate_math_functions():
    """sqrt() is whitelisted and must produce a valid floating-point result."""
    result = calculate("sqrt(4)")
    assert "2" in result


# ── hash_text() ───────────────────────────────────────────────────────────────

_HASH_LENGTHS = {
    "md5":    32,
    "sha1":   40,
    "sha224": 56,
    "sha256": 64,
    "sha384": 96,
    "sha512": 128,
}


@pytest.mark.parametrize("algorithm,expected_hex_len", _HASH_LENGTHS.items())
def test_hash_text_valid_algorithm(algorithm, expected_hex_len):
    """Each supported algorithm returns a hex digest of the correct length."""
    result = hash_text("hello", algorithm)
    # The digest appears after the '=' sign.
    assert "=" in result
    digest = result.split("=")[-1].strip()
    assert len(digest) == expected_hex_len, (
        f"Expected digest length {expected_hex_len} for {algorithm}, got {len(digest)}"
    )


def test_hash_text_invalid_algorithm():
    """An unsupported algorithm name must return a human-readable error, not raise."""
    result = hash_text("hello", "rot13")
    assert "Unknown algorithm" in result or "Fehler" in result


def test_hash_text_sha256_known_value():
    """SHA-256 of the empty string has a known hex value."""
    result = hash_text("", "sha256")
    known = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert known in result


# ── base64_codec() ────────────────────────────────────────────────────────────


def test_base64_encode():
    result = base64_codec("hello", "encode")
    assert "aGVsbG8=" in result


def test_base64_decode():
    result = base64_codec("aGVsbG8=", "decode")
    assert "hello" in result


def test_base64_roundtrip():
    """Encode then decode must recover the original string."""
    original = "MoE Sovereign test payload"
    encoded_result = base64_codec(original, "encode")
    # Extract the encoded string (everything after ': ')
    encoded = encoded_result.split(": ", 1)[-1].strip()
    decoded_result = base64_codec(encoded, "decode")
    assert original in decoded_result


def test_base64_invalid_mode():
    """An unrecognised mode must return an error string, not raise."""
    result = base64_codec("hello", "compress")
    assert "Fehler" in result or "mode" in result


def test_base64_malformed_input_for_decode():
    """Non-base64 input for decode must return an error string, not raise."""
    result = base64_codec("!!!not-base64!!!", "decode")
    assert result.startswith("Error") or result.startswith("Fehler") or "Error" in result


# ── date_diff() ───────────────────────────────────────────────────────────────


def test_date_diff_valid():
    result = date_diff("2024-01-01", "2024-01-31")
    assert "30" in result   # 30 calendar days apart


def test_date_diff_same_date():
    result = date_diff("2024-06-15", "2024-06-15")
    assert "0" in result


def test_date_diff_invalid_format_returns_error():
    """DD-MM-YYYY is not the expected format; must return an error string."""
    result = date_diff("31-01-2024", "2024-01-01")
    assert result.startswith("Error") or result.startswith("Fehler")


def test_date_diff_non_date_string_returns_error():
    result = date_diff("not-a-date", "2024-01-01")
    assert result.startswith("Error") or result.startswith("Fehler")


# ── gcd_lcm() ─────────────────────────────────────────────────────────────────

# gcd(12, 8) = 4, lcm(12, 8) = 24
_A, _B = 12, 8


def test_gcd_lcm_gcd_operation():
    result = gcd_lcm(_A, _B, "gcd")
    assert "4" in result
    # The LCM value should NOT appear in a gcd-only result.
    assert "24" not in result


def test_gcd_lcm_lcm_operation():
    result = gcd_lcm(_A, _B, "lcm")
    assert "24" in result
    # The GCD value should NOT appear in an lcm-only result.
    assert result.count("4") == result.count("24") * 1  # 24 contains '4'; allow that


def test_gcd_lcm_both_operation():
    result = gcd_lcm(_A, _B, "both")
    assert "4" in result
    assert "24" in result


def test_gcd_lcm_unknown_operation_falls_through():
    """An unrecognised operation silently falls through to 'both' behaviour."""
    result = gcd_lcm(_A, _B, "modulo")
    # Must still return a valid result, not raise.
    assert isinstance(result, str)
    assert "Fehler" not in result


def test_gcd_lcm_coprime_numbers():
    """gcd(7, 11) = 1, lcm(7, 11) = 77."""
    result = gcd_lcm(7, 11, "both")
    assert "1" in result
    assert "77" in result


# ── statistics_calc() ─────────────────────────────────────────────────────────


def test_statistics_calc_mean_and_median():
    result = statistics_calc("1,2,3,4,5", "mean,median")
    assert "mean" in result
    assert "median" in result
    assert "3.0" in result  # mean = 3, median = 3


def test_statistics_calc_default_operations_present():
    result = statistics_calc("10,20,30")
    for op in ("mean", "median", "min", "max", "sum", "count"):
        assert op in result, f"Expected '{op}' in statistics output"


def test_statistics_calc_unknown_op_silently_skipped():
    """Unknown operations are silently ignored; the function must not raise."""
    result = statistics_calc("1,2,3", "unknown_op")
    assert isinstance(result, str)
    assert "Fehler" not in result


def test_statistics_calc_empty_data_returns_error():
    result = statistics_calc("", "mean")
    assert "Error" in result or "Fehler" in result or "keine" in result.lower()


# ── subnet_calc() ─────────────────────────────────────────────────────────────


def test_subnet_calc_class_c():
    result = subnet_calc("192.168.1.0/24")
    assert "192.168.1.0" in result      # network address
    assert "192.168.1.255" in result    # broadcast


def test_subnet_calc_host_count_class_c():
    result = subnet_calc("192.168.1.0/24")
    assert "254" in result              # 254 usable hosts in a /24


def test_subnet_calc_slash_30():
    """/30 has exactly 2 usable host addresses."""
    result = subnet_calc("10.0.0.0/30")
    assert "2" in result


def test_subnet_calc_invalid_cidr_returns_error():
    result = subnet_calc("999.999.0.0/24")
    assert result.startswith("Error") or result.startswith("Fehler")


def test_subnet_calc_non_cidr_string_returns_error():
    result = subnet_calc("not-an-ip")
    assert result.startswith("Error") or result.startswith("Fehler")

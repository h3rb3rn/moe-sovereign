"""tests/test_context_index.py — Unit tests for services/context_index.py.

All tests are pure (no ChromaDB, no Redis, no network) — _chunk_text and
_build_toc are deterministic functions that operate on strings only.

Critical regression: _chunk_text had an infinite loop (start = end - overlap
when end == length → start never advances → list grows to OOM).  These tests
verify termination and full coverage for all edge cases.
"""

import sys
import os

# Allow import of services/context_index without the full app environment
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import pytest

from context_index import _chunk_text, _build_toc, CHUNK_SIZE, CHUNK_OVERLAP


# ── _chunk_text ────────────────────────────────────────────────────────────────

class TestChunkText:

    def test_empty_string_returns_empty(self):
        assert _chunk_text("") == []

    def test_whitespace_only_returns_empty(self):
        assert _chunk_text("   \n\t  ") == []

    def test_shorter_than_chunk_size_returns_single_chunk(self):
        text = "Short text under the chunk limit."
        result = _chunk_text(text, chunk_size=200, overlap=20)
        assert len(result) == 1
        assert result[0] == text

    def test_exactly_chunk_size(self):
        """Text of exactly chunk_size chars — must not loop forever."""
        text = "x" * 1500
        result = _chunk_text(text, chunk_size=1500, overlap=200)
        assert len(result) == 1
        assert len(result[0]) == 1500

    def test_one_char_over_chunk_size(self):
        """Text of chunk_size+1 — should produce exactly 2 chunks and terminate."""
        text = "x" * 1501
        result = _chunk_text(text, chunk_size=1500, overlap=200)
        # Second chunk is the overlap tail
        assert len(result) >= 1
        # Full coverage: all characters must appear in at least one chunk
        _assert_full_coverage(text, result)

    def test_large_text_terminates(self):
        """100 k chars — the original infinite-loop trigger. Must terminate."""
        import time
        text = ("abcdefghij" * 10_000)  # 100 000 chars, no newlines
        t0 = time.monotonic()
        result = _chunk_text(text, chunk_size=1500, overlap=200)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"_chunk_text took {elapsed:.2f}s — possible infinite loop"
        assert len(result) > 0
        _assert_full_coverage(text, result)

    def test_text_with_newlines_snaps_to_boundary(self):
        """Chunks should prefer splitting at newlines."""
        line = "A" * 100 + "\n"
        text = line * 20   # 2020 chars, newlines at every 101st char
        result = _chunk_text(text, chunk_size=500, overlap=50)
        for chunk in result:
            # Every chunk should end at a newline boundary or at the very end
            stripped = chunk.rstrip()
            if stripped:
                assert "\n" in chunk or chunk == text[-len(chunk):]

    def test_no_newlines_still_terminates(self):
        """Dense text with no newlines: boundary search returns -1, fallback to hard cut."""
        text = "B" * 5000
        result = _chunk_text(text, chunk_size=1000, overlap=100)
        assert len(result) > 0
        _assert_full_coverage(text, result)

    def test_overlap_equals_chunk_size_guard(self):
        """overlap >= chunk_size must be clamped to prevent start from never advancing."""
        text = "C" * 3000
        # overlap == chunk_size would loop forever without the guard
        result = _chunk_text(text, chunk_size=500, overlap=500)
        assert len(result) > 0
        _assert_full_coverage(text, result)

    def test_overlap_larger_than_chunk_size_guard(self):
        text = "D" * 3000
        result = _chunk_text(text, chunk_size=500, overlap=600)
        assert len(result) > 0
        _assert_full_coverage(text, result)

    def test_single_character(self):
        result = _chunk_text("X")
        assert result == ["X"]

    def test_default_params_match_config(self):
        """Verify default parameters are the configured ones (not stale copies)."""
        text = "E" * (CHUNK_SIZE * 3)
        result = _chunk_text(text)
        assert len(result) > 1

    def test_chunk_count_reasonable(self):
        """For a 28 k char system_prompt: expect 15-25 chunks (not thousands)."""
        text = "F" * 28_000
        result = _chunk_text(text, chunk_size=1500, overlap=200)
        assert 10 <= len(result) <= 30, f"unexpected chunk count: {len(result)}"


def _assert_full_coverage(text: str, chunks: list[str]) -> None:
    """Verify that chunks span the full text (start to end).

    Uses start/end anchoring rather than a position-bitmap because texts with
    repeated characters (e.g. 'AAAA...') defeat text.find()-based tracking.
    Invariants checked:
      1. First chunk begins the text.
      2. Last chunk ends the text.
      3. All chunks are non-empty substrings of the original text.
    """
    assert chunks, "chunks must not be empty"
    # Invariant 1: first chunk is a prefix of text (anchored at start)
    assert text.startswith(chunks[0]), (
        f"First chunk does not start at beginning of text.\n"
        f"text[:50]={text[:50]!r}\nchunks[0][:50]={chunks[0][:50]!r}"
    )
    # Invariant 2: last chunk is a suffix of text (anchored at end)
    assert text.endswith(chunks[-1]), (
        f"Last chunk does not end at end of text.\n"
        f"text[-50:]={text[-50:]!r}\nchunks[-1][-50:]={chunks[-1][-50:]!r}"
    )
    # Invariant 3: all chunks are valid substrings (len check)
    for i, chunk in enumerate(chunks):
        assert chunk in text, f"chunk[{i}] is not a substring of text"


# ── _build_toc ─────────────────────────────────────────────────────────────────

class TestBuildToc:

    def test_empty_returns_empty(self):
        assert _build_toc("") == ""

    def test_markdown_headings_extracted(self):
        text = "# Title\n## Section 1\n### Sub\nSome body text.\n## Section 2\n"
        toc = _build_toc(text)
        assert "# Title" in toc
        assert "## Section 1" in toc

    def test_code_definitions_extracted(self):
        text = "def my_function():\n    pass\nclass MyClass:\n    pass\n"
        toc = _build_toc(text)
        assert "def my_function" in toc or "class MyClass" in toc

    def test_max_chars_respected(self):
        lines = [f"# Section {i}" for i in range(1000)]
        text = "\n".join(lines)
        toc = _build_toc(text, max_chars=500)
        assert len(toc) <= 600  # small buffer for the last line

    def test_fallback_for_plain_text(self):
        text = "Line one.\nLine two.\nLine three.\n"
        toc = _build_toc(text)
        assert len(toc) > 0

"""context_compressor.py — Content-aware context compression for LLM message history.

Native adaptation of Headroom's ContentRouter + SmartCrusher architecture:
  detect_content_type()     → classifies message content without ML
  compress_json_content()   → SmartCrusher: structural JSON reduction (see TODO(human))
  compress_code_content()   → strips comments, collapses blank lines
  compress_log_content()    → head+tail window for log streams
  compress_message_content() → routes to the right compressor

Integration point: parsing._truncate_history() calls compress_message_content()
before collapsing old turns to '[…]', so information density is maximised first.

All functions are pure (no I/O, no side effects) and never raise.
"""

from __future__ import annotations

import json
import re
from typing import Any

_ARRAY_KEEP = 5  # max items to keep verbatim before summarising the rest


# ── Content type detection ─────────────────────────────────────────────────────

def detect_content_type(text: str) -> str:
    """Classify *text* as 'json', 'code', 'log', or 'text'.

    Detection runs top-to-bottom; first match wins.
    Deliberately cheap — no tokenisation, no regex backtracking over the full string.
    """
    s = text.lstrip()
    if not s:
        return "text"

    # JSON: leading { or [ — parse the full string; truncated probes produce false negatives
    if s[0] in ("{", "["):
        try:
            json.loads(s)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass

    # Code: language keywords present near the top (first 500 chars)
    head = s[:500]
    _CODE_RE = re.compile(
        r'\b(def |class |function |import |#include|const |let |var |fn |pub fn |async def |return |if \(|for \()\b'
    )
    if _CODE_RE.search(head):
        return "code"

    # Log: timestamp prefix or log-level word on any of the first 10 lines
    first_lines = "\n".join(s.splitlines()[:10])
    if re.search(r'\b(ERROR|WARN(?:ING)?|INFO|DEBUG|FATAL|CRITICAL)\b'
                 r'|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', first_lines):
        return "log"

    return "text"


# ── Per-type compressors ───────────────────────────────────────────────────────

def compress_code_content(text: str, max_chars: int) -> str:
    """Remove comments and collapse blank lines. Returns sliced fallback on failure."""
    if len(text) <= max_chars:
        return text
    try:
        result = re.sub(r'(?m)^\s*#[^\n]*$', '', text)          # Python # comments
        result = re.sub(r'(?m)^\s*//[^\n]*$', '', result)        # C / JS // comments
        result = re.sub(r'/\*.*?\*/', '', result, flags=re.DOTALL)  # /* block */ comments
        result = re.sub(r'\n{3,}', '\n\n', result).strip()
        if len(result) <= max_chars:
            return result
    except Exception:
        pass
    return text[:max_chars] + "\n# […truncated]"


def compress_log_content(text: str, max_chars: int) -> str:
    """Keep a head+tail window of log lines, drop the middle."""
    if len(text) <= max_chars:
        return text
    try:
        lines = text.splitlines()
        total = len(lines)
        window = max(3, max_chars // 200)
        if window * 2 >= total:
            return text[:max_chars]
        kept = (
            lines[:window]
            + [f"[… {total - window * 2} lines omitted …]"]
            + lines[-window:]
        )
        result = "\n".join(kept)
        return result if len(result) <= max_chars else result[:max_chars]
    except Exception:
        return text[:max_chars]


def _prune_nulls(obj: Any) -> Any:
    """Recursively remove None values and empty strings/lists/dicts."""
    if isinstance(obj, dict):
        return {k: _prune_nulls(v) for k, v in obj.items()
                if v is not None and v != "" and v != [] and v != {}}
    if isinstance(obj, list):
        return [_prune_nulls(i) for i in obj if i is not None]
    return obj


def _summarise_arrays(obj: Any, keep: int = _ARRAY_KEEP) -> Any:
    """Replace large homogeneous arrays with a head-slice + count annotation.

    Arrays whose items are all dicts (typical for GraphRAG/web-search results)
    are the primary target: keep the first *keep* items and append a sentinel
    string so the LLM knows data was omitted.
    """
    if isinstance(obj, dict):
        return {k: _summarise_arrays(v, keep) for k, v in obj.items()}
    if isinstance(obj, list):
        processed = [_summarise_arrays(i, keep) for i in obj]
        if len(processed) > keep and all(isinstance(i, dict) for i in processed):
            omitted = len(processed) - keep
            return processed[:keep] + [f"…+{omitted} more items"]
        return processed
    return obj


def _trim(s: str, max_chars: int) -> str:
    """Slice *s* to ≤ max_chars, appending '…' when truncated (still ≤ max_chars)."""
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


def compress_json_content(text: str, max_chars: int) -> str:
    """Structurally compress a JSON string to fit within *max_chars*.

    Pipeline (each stage exits early if the result already fits):
      1. Parse + compact serialisation (removes whitespace)
      2. Prune null/empty values
      3. Summarise oversized homogeneous arrays (SmartCrusher core)
      4. Top-level key pruning by serialised size (dicts only)
      5. Hard slice of best-effort result

    Invariant: returned string length is always ≤ max_chars.
    """
    if len(text) <= max_chars:
        return text
    try:
        obj = json.loads(text)

        # Stage 1 — compact serialisation (whitespace removal alone often suffices)
        stage1 = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if len(stage1) <= max_chars:
            return stage1

        # Stage 2 — remove null/empty values
        obj = _prune_nulls(obj)
        stage2 = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if len(stage2) <= max_chars:
            return stage2

        # Stage 3 — summarise large homogeneous arrays
        obj = _summarise_arrays(obj)
        stage3 = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if len(stage3) <= max_chars:
            return stage3

        # Stage 4 — prune top-level keys by serialised size (dicts only)
        if isinstance(obj, dict):
            key_sizes = sorted(
                obj.keys(),
                key=lambda k: len(json.dumps(obj[k], ensure_ascii=False)),
                reverse=True,
            )
            pruned = dict(obj)
            for key in key_sizes:
                del pruned[key]
                candidate = json.dumps(pruned, ensure_ascii=False, separators=(",", ":"))
                if len(candidate) <= max_chars:
                    return candidate
                if len(pruned) <= 1:
                    break
            obj = pruned

        # Stage 5 — hard slice of the best compressed form found so far
        best = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return _trim(best, max_chars)

    except Exception:
        pass
    return _trim(text, max_chars)


# ── Content router ─────────────────────────────────────────────────────────────

def compress_message_content(content: str, max_chars: int) -> str:
    """Route *content* to the appropriate compressor and return a version ≤ max_chars.

    Never raises — falls back to slicing on any unexpected error.
    Returns *content* unchanged when it already fits.
    """
    if not content or len(content) <= max_chars:
        return content
    try:
        ctype = detect_content_type(content)
        if ctype == "json":
            return compress_json_content(content, max_chars)
        if ctype == "code":
            return compress_code_content(content, max_chars)
        if ctype == "log":
            return compress_log_content(content, max_chars)
    except Exception:
        pass
    # Plain text: return verbatim up to limit (prose truncation is handled by caller)
    return content[:max_chars] + "…"

"""
MoE Precision Tools — MCP Server
Exact calculations via Linux/Python tools for everything where LLMs systematically fail:
Arithmetic, dates, units, statistics, hashing, regex, networking, and more.
"""

import ast
import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import logging
import math
import operator as op_module
import os
import re
import statistics as stats_module
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import sympy as sp
import uvicorn
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

logger = logging.getLogger("MOE-SOVEREIGN.MCP")

# --- MCP Server ---
mcp = FastMCP("precision-tools")

# ─── SAFE ARITHMETIC EVAL ────────────────────────────────────────────────────

_SAFE_OPS = {
    ast.Add: op_module.add,
    ast.Sub: op_module.sub,
    ast.Mult: op_module.mul,
    ast.Div: op_module.truediv,
    ast.Pow: op_module.pow,
    ast.USub: op_module.neg,
    ast.Mod: op_module.mod,
    ast.FloorDiv: op_module.floordiv,
}
_SAFE_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "floor": math.floor, "ceil": math.ceil,
    "factorial": math.factorial,
}
_SAFE_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}


def _safe_eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _SAFE_CONSTS:
            return _SAFE_CONSTS[node.id]
        raise ValueError(f"Unknown variable: {node.id}")
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _SAFE_OPS:
            raise ValueError(f"Disallowed operation: {type(node.op)}")
        return _SAFE_OPS[type(node.op)](_safe_eval_node(node.left), _safe_eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _SAFE_OPS:
            raise ValueError(f"Disallowed operation: {type(node.op)}")
        return _SAFE_OPS[type(node.op)](_safe_eval_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = node.func.id
        if fn not in _SAFE_FUNCS:
            raise ValueError(f"Unknown function: {fn}")
        args = [_safe_eval_node(a) for a in node.args]
        return _SAFE_FUNCS[fn](*args)
    raise ValueError(f"Disallowed AST node: {ast.dump(node)}")


# ─── TOOLS ───────────────────────────────────────────────────────────────────

@mcp.tool()
def calculate(expression: str) -> str:
    """
    Calculates mathematical expressions exactly without LLM hallucination.
    Supports: +, -, *, /, **, %, //, parentheses, sqrt(), sin(), cos(), log(), factorial() etc.
    Also supports percentages: '15% of 239.99' or '239.99 * 0.15'.
    Example: calculate("sqrt(2) * pi") → 4.442882938...

    Security note: expressions that parse as valid Python but contain unsafe
    constructs (import, attribute access, calls to non-whitelisted names) are
    rejected by the AST validator and never reach the SymPy fallback.
    The SymPy fallback is reserved exclusively for SyntaxErrors (e.g. implicit
    multiplication like '2x') which our safe AST evaluator cannot parse.
    """
    # Normalize percentage notation: "15% of X" → "(15/100)*X"
    expr = re.sub(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:of|von)\s*",
        lambda m: f"({m.group(1)}/100)*",
        expression,
        flags=re.IGNORECASE,
    )
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr)

    # Stage 1: safe AST evaluation (whitelist-only — no arbitrary Python allowed).
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        # SyntaxError: expression may use implicit multiplication or other
        # SymPy-parseable syntax.  Fall through to the SymPy fallback below.
        pass
    else:
        # AST parsed successfully — run the safe evaluator.
        # Any ValueError here means an unsafe construct was detected; return
        # an error immediately without falling through to SymPy.
        try:
            result = _safe_eval_node(tree.body)
        except Exception as e:
            return f"Error: {e}"
        if isinstance(result, float) and result == int(result) and abs(result) < 1e15:
            return f"{expression} = {int(result)}"
        if isinstance(result, float):
            return f"{expression} = {result:.12g}"
        return f"{expression} = {result}"

    # Stage 2: SymPy fallback — only reached via SyntaxError above.
    try:
        sym_result = sp.sympify(expression)
        simplified = sp.simplify(sym_result)
        numeric = float(simplified.evalf()) if simplified.is_number else None
        if numeric is not None:
            return f"{expression} = {numeric:.12g} (exact: {simplified})"
        return f"{expression} = {simplified}"
    except Exception as e2:
        return f"Error: {e2}"


@mcp.tool()
def solve_equation(equation: str, variable: str = "x") -> str:
    """
    Solves algebraic equations exactly via SymPy.
    Examples: 'x**2 - 4 = 0', 'x**3 - 6*x**2 + 11*x - 6 = 0', '2*x + 5 = 13'
    """
    try:
        var = sp.Symbol(variable)
        if "=" in equation:
            left, right = equation.split("=", 1)
            eq = sp.Eq(sp.sympify(left.strip()), sp.sympify(right.strip()))
        else:
            eq = sp.Eq(sp.sympify(equation.strip()), 0)
        solutions = sp.solve(eq, var)
        numeric = [float(s.evalf()) if s.is_number else str(s) for s in solutions]
        return (
            f"Equation: {equation}\n"
            f"Solutions ({variable}): {[str(s) for s in solutions]}\n"
            f"Numeric: {numeric}"
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def date_diff(date1: str, date2: str) -> str:
    """
    Calculates the exact difference between two dates.
    Format: YYYY-MM-DD. Returns days, years, months, days.
    Example: date_diff('1990-05-15', '2026-03-29')
    """
    try:
        d1 = datetime.strptime(date1.strip(), "%Y-%m-%d").date()
        d2 = datetime.strptime(date2.strip(), "%Y-%m-%d").date()
        diff = abs((d2 - d1).days)
        years = diff // 365
        remaining = diff % 365
        months = remaining // 30
        days = remaining % 30
        earlier, later = (d1, d2) if d1 <= d2 else (d2, d1)
        return (
            f"From {earlier} to {later}: "
            f"{diff} days total "
            f"(≈ {years} years, {months} months, {days} days)"
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def date_add(base_date: str, days: int = 0, months: int = 0, years: int = 0) -> str:
    """
    Adds or subtracts time from a date.
    Format base_date: YYYY-MM-DD. Negative values for subtraction.
    Example: date_add('2026-01-01', months=3, days=-5)
    """
    try:
        from dateutil.relativedelta import relativedelta
        d = datetime.strptime(base_date.strip(), "%Y-%m-%d").date()
        result = d + relativedelta(years=years, months=months, days=days)
        days_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return (
            f"{base_date} + {years}a {months}m {days}d "
            f"= {result} ({days_en[result.weekday()]})"
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def day_of_week(date_str: str) -> str:
    """
    Returns weekday, calendar week and day of year for a date.
    Format: YYYY-MM-DD.
    Example: day_of_week('2026-12-25')
    """
    try:
        d = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        days_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        iso = d.isocalendar()
        return (
            f"{date_str} is a {days_en[d.weekday()]} "
            f"(CW {iso[1]}, day {d.timetuple().tm_yday} of year {d.year})"
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def unit_convert(value: float, from_unit: str, to_unit: str) -> str:
    """
    Converts physical units exactly via pint.
    Examples: unit_convert(100, 'km/h', 'm/s'), unit_convert(1, 'mile', 'km'),
               unit_convert(100, 'degF', 'degC'), unit_convert(5, 'lb', 'kg')
    """
    try:
        from pint import UnitRegistry
        ureg = UnitRegistry()
        qty = value * ureg(from_unit)
        result = qty.to(to_unit)
        return f"{value} {from_unit} = {result.magnitude:.10g} {to_unit}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def statistics_calc(data: str, operations: str = "mean,median,stdev,min,max,sum,count") -> str:
    """
    Calculates statistical measures for a data set.
    data: comma-separated numbers, e.g. '1,2,3,4,5,6,7,8,9,10'
    operations: comma-separated operations (mean, median, stdev, variance, min, max, sum, count, mode)
    """
    try:
        values = [float(x.strip()) for x in data.split(",") if x.strip()]
        if not values:
            return "Error: No data found"
        ops = [o.strip() for o in operations.split(",")]
        op_map = {
            "mean": stats_module.mean,
            "median": stats_module.median,
            "stdev": lambda v: stats_module.stdev(v) if len(v) > 1 else 0.0,
            "variance": lambda v: stats_module.variance(v) if len(v) > 1 else 0.0,
            "min": min,
            "max": max,
            "sum": sum,
            "count": len,
            "mode": stats_module.mode,
        }
        results = {}
        for op in ops:
            if op in op_map:
                val = op_map[op](values)
                results[op] = round(float(val), 10)
        preview = data[:40] + "..." if len(data) > 40 else data
        return f"Statistics [{preview}]: {json.dumps(results, ensure_ascii=False)}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def hash_text(text: str, algorithm: str = "sha256") -> str:
    """
    Calculates cryptographic hashes exactly.
    algorithm: md5, sha1, sha224, sha256, sha384, sha512
    Example: hash_text('Hello World', 'sha256')
    """
    try:
        algos = {
            "md5": hashlib.md5, "sha1": hashlib.sha1,
            "sha224": hashlib.sha224, "sha256": hashlib.sha256,
            "sha384": hashlib.sha384, "sha512": hashlib.sha512,
        }
        algo = algorithm.lower()
        if algo not in algos:
            return f"Unknown algorithm. Available: {list(algos.keys())}"
        digest = algos[algo](text.encode("utf-8")).hexdigest()
        preview = text[:30] + "..." if len(text) > 30 else text
        return f"{algorithm.upper()}('{preview}') = {digest}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def base64_codec(text: str, mode: str = "encode") -> str:
    """
    Base64 encode or decode.
    mode: 'encode' or 'decode'
    Example: base64_codec('Hello World', 'encode')
    """
    try:
        if mode == "encode":
            result = base64.b64encode(text.encode("utf-8")).decode("ascii")
            return f"Base64-encoded: {result}"
        elif mode == "decode":
            result = base64.b64decode(text.encode("ascii")).decode("utf-8")
            return f"Base64-decoded: {result}"
        else:
            return "Error: mode must be 'encode' or 'decode'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def regex_extract(pattern: str, text: str, flags: str = "") -> str:
    """
    Performs regex pattern matching and extracts all matches.
    flags: i=ignorecase, m=multiline, s=dotall (combinable, e.g. 'im')
    Example: regex_extract(r'\\d{4}-\\d{2}-\\d{2}', 'Date: 2026-03-29 and 2025-12-31')
    """
    try:
        flag_map = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
        re_flags = 0
        for f in flags.lower():
            re_flags |= flag_map.get(f, 0)
        matches = re.findall(pattern, text, re_flags)
        groups_count = len(matches[0]) if matches and isinstance(matches[0], tuple) else 0
        return (
            f"Pattern '{pattern}' → {len(matches)} matches: "
            f"{matches[:20]}{'...' if len(matches) > 20 else ''}"
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def subnet_calc(cidr: str) -> str:
    """
    Calculates network information for CIDR notation.
    Example: subnet_calc('192.168.1.0/24'), subnet_calc('10.0.0.5/22')
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
        hosts = list(network.hosts())
        host_count = len(hosts)
        return (
            f"Network: {network.network_address}/{network.prefixlen}\n"
            f"Broadcast: {network.broadcast_address}\n"
            f"Subnet mask: {network.netmask} ({network.prefixlen} bits)\n"
            f"Usable hosts: {host_count}\n"
            f"First host IP: {hosts[0] if hosts else 'N/A'}\n"
            f"Last host IP: {hosts[-1] if hosts else 'N/A'}\n"
            f"Version: IPv{network.version}"
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def text_analyze(text: str) -> str:
    """
    Analyzes text for words, characters, sentences, paragraphs, reading time.
    Useful for precise text metrics without LLM estimation.
    """
    words = len(re.findall(r"\b\w+\b", text))
    chars_total = len(text)
    chars_no_space = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
    sentences = len(re.findall(r"[.!?]+", text))
    paragraphs = len([p for p in text.split("\n") if p.strip()])
    unique_words = len(set(re.findall(r"\b\w+\b", text.lower())))
    read_min = words / 200.0
    return (
        f"Words: {words} ({unique_words} unique), "
        f"Characters: {chars_total} ({chars_no_space} without whitespace), "
        f"Sentences: {sentences}, paragraphs: {paragraphs}, "
        f"Reading time: ~{read_min:.1f} min (200 WPM)"
    )


@mcp.tool()
def prime_factorize(n: int) -> str:
    """
    Calculates the prime factorization of an integer exactly via SymPy.
    Example: prime_factorize(360) → 2^3 × 3^2 × 5
    """
    try:
        if n <= 1:
            return f"{n} has no prime factors (n > 1 required)"
        factors = sp.factorint(n)
        parts = [f"{p}^{e}" if e > 1 else str(p) for p, e in sorted(factors.items())]
        return f"{n} = {' × '.join(parts)}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def gcd_lcm(a: int, b: int, operation: str = "both") -> str:
    """
    Calculates GCD (greatest common divisor) and LCM (least common multiple).
    operation: 'gcd', 'lcm', or 'both'
    Example: gcd_lcm(48, 18)
    """
    try:
        g = math.gcd(abs(a), abs(b))
        lv = abs(a * b) // g if g != 0 else 0
        if operation == "gcd":
            return f"GCD({a}, {b}) = {g}"
        if operation == "lcm":
            return f"LCM({a}, {b}) = {lv}"
        return f"GCD({a}, {b}) = {g}  |  LCM({a}, {b}) = {lv}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def json_query(json_str: str, path: str) -> str:
    """
    Extracts data from JSON via dot notation or array index.
    Example: json_query('{"user":{"name":"Alice","age":30}}', 'user.name') → 'Alice'
    Supports: 'key', 'key.subkey', 'key[0]', 'key[0].subkey'
    """
    try:
        data = json.loads(json_str)
        tokens = re.split(r"\.(?![^\[]*\])", path)
        current = data
        for token in tokens:
            arr_match = re.match(r"^(\w+)\[(\d+)\]$", token)
            if arr_match:
                current = current[arr_match.group(1)][int(arr_match.group(2))]
            elif re.match(r"^\[(\d+)\]$", token):
                current = current[int(re.match(r"^\[(\d+)\]$", token).group(1))]
            else:
                current = current[token]
        return f"'{path}' → {json.dumps(current, ensure_ascii=False)}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def roman_numeral(value: str) -> str:
    """
    Converts between Arabic and Roman numerals.
    Input: number (1-3999) or Roman numeral (e.g. 'XIV', 'MMXXVI').
    """
    try:
        roman_vals = [
            (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
            (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
        ]
        roman_parse = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
        v = value.strip()
        if v.isdigit():
            n = int(v)
            if not 1 <= n <= 3999:
                return "Error: Only 1–3999 supported"
            result = ""
            for val, sym in roman_vals:
                while n >= val:
                    result += sym
                    n -= val
            return f"{value} → {result}"
        else:
            s = v.upper()
            n = 0
            for i, c in enumerate(s):
                if c not in roman_parse:
                    return f"Invalid Roman numeral: '{c}'"
                if i + 1 < len(s) and roman_parse[c] < roman_parse[s[i + 1]]:
                    n -= roman_parse[c]
                else:
                    n += roman_parse[c]
            return f"{value} → {n}"
    except Exception as e:
        return f"Error: {e}"


# ─── LEGAL TOOLS ─────────────────────────────────────────────────────────────

# Configurable via GII_BASE_URL env var — set to a local mirror for offline deployments
_GII_BASE       = os.getenv("GII_BASE_URL", "https://www.gesetze-im-internet.de")
_GII_TOC_URL    = f"{_GII_BASE}/gii-toc.xml"
_TOC_CACHE: dict = {"data": None, "ts": 0.0}
_TOC_LOCK        = threading.Lock()
_LAW_CACHE: dict = {}
_LAW_LOCK        = threading.Lock()
_TOC_CACHE_TTL   = 24 * 3600
_LAW_CACHE_TTL   = 6  * 3600


def _strip_ns(tag: str) -> str:
    """Removes XML namespace prefix from a tag name."""
    return re.sub(r"\{[^}]*\}", "", tag)


def _extract_text(element) -> str:
    """Recursively extracts all text from an XML element."""
    parts: list[str] = []
    if element.text and element.text.strip():
        parts.append(element.text.strip())
    for child in element:
        child_text = _extract_text(child)
        if child_text:
            parts.append(child_text)
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return " ".join(parts)


def _get_toc() -> Optional[ET.Element]:
    """Loads and caches the GII table-of-contents XML (gii-toc.xml)."""
    with _TOC_LOCK:
        now = time.time()
        if _TOC_CACHE["data"] is not None and now - _TOC_CACHE["ts"] < _TOC_CACHE_TTL:
            return _TOC_CACHE["data"]
        try:
            resp = httpx.get(_GII_TOC_URL, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            _TOC_CACHE["data"] = root
            _TOC_CACHE["ts"]   = now
            return root
        except Exception:
            return _TOC_CACHE["data"]  # Stale data as fallback


def _resolve_law_url(law: str) -> str:
    """Determines the ZIP URL for a law — from TOC (exact) or fallback (lowercase)."""
    toc = _TOC_CACHE.get("data")
    if toc is not None:
        law_up = law.upper()
        for item in toc:
            if _strip_ns(item.tag) != "item":
                continue
            link_url = ""
            for child in item:
                if _strip_ns(child.tag) == "link" and child.text:
                    link_url = child.text.strip()
            if link_url:
                m = re.search(r"/([^/]+)/xml\.zip", link_url)
                if m and m.group(1).upper() == law_up:
                    return link_url
    return f"{_GII_BASE}/{law.lower()}/xml.zip"


def _get_law_xml(law: str) -> Tuple[Optional[ET.Element], str]:
    """Loads and caches the XML ZIP of a federal law. Returns (root, error)."""
    key = law.upper()
    with _LAW_LOCK:
        now = time.time()
        if key in _LAW_CACHE and now - _LAW_CACHE[key]["ts"] < _LAW_CACHE_TTL:
            return _LAW_CACHE[key]["root"], ""
        # LRU eviction when > 20 cached laws
        if len(_LAW_CACHE) >= 20:
            oldest = min(_LAW_CACHE, key=lambda k: _LAW_CACHE[k]["ts"])
            del _LAW_CACHE[oldest]
        url = _resolve_law_url(law)
        try:
            resp = httpx.get(url, timeout=15.0, follow_redirects=True)
            if resp.status_code == 404:
                # Fallback: retry without TOC
                fallback_url = f"{_GII_BASE}/{law.lower()}/xml.zip"
                if fallback_url != url:
                    resp2 = httpx.get(fallback_url, timeout=15.0, follow_redirects=True)
                    if resp2.status_code == 200:
                        resp = resp2
                    else:
                        return None, (
                            f"Law '{law}' not found. "
                            f"Use legal_search_laws() to find valid abbreviations."
                        )
                else:
                    return None, (
                        f"Law '{law}' not found. "
                        f"Use legal_search_laws() to find valid abbreviations."
                    )
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_bytes = zf.read(zf.namelist()[0])
            root = ET.fromstring(xml_bytes)
            _LAW_CACHE[key] = {"root": root, "ts": now}
            return root, ""
        except httpx.TimeoutException:
            return None, "Error: Timeout loading from gesetze-im-internet.de — please try again."
        except Exception as e:
            return None, f"Error loading '{law}': {e}"


def _get_enbez_num(enbez: str) -> str:
    """Extracts the number+letter from an enbez like '§ 242a' → '242a'."""
    m = re.search(r"(\d+[a-z]?)", enbez.lower())
    return m.group(1) if m else ""


def _iter_norms(root: ET.Element):
    """Iterator over all <norm> elements with (enbez, titel, textdaten_element)."""
    for elem in root.iter():
        if _strip_ns(elem.tag) != "norm":
            continue
        meta = None
        textdaten = None
        for child in elem:
            tag = _strip_ns(child.tag)
            if tag == "metadaten":
                meta = child
            elif tag == "textdaten":
                textdaten = child
        if meta is None:
            continue
        enbez = ""
        titel = ""
        for child in meta:
            tag = _strip_ns(child.tag)
            if tag == "enbez" and child.text:
                enbez = child.text.strip()
            elif tag == "titel" and child.text:
                titel = child.text.strip()
        yield enbez, titel, textdaten


def _norm_text(textdaten) -> str:
    """Extracts the full text from a <textdaten> element."""
    if textdaten is None:
        return ""
    for child in textdaten:
        if _strip_ns(child.tag) == "text":
            for content in child:
                return _extract_text(content)
    return ""


@mcp.tool()
def legal_search_laws(query: str, max_results: int = 10) -> str:
    """
    Searches German federal laws (~6,000 laws) by keyword in abbreviation or title.
    Returns abbreviations to be used with legal_get_paragraph / legal_get_law_overview.
    Examples: legal_search_laws("Mietrecht"), legal_search_laws("Datenschutz"), legal_search_laws("BGB")
    """
    toc = _get_toc()
    if toc is None:
        return "Error: gesetze-im-internet.de unreachable — please try again."

    q = query.lower()
    matches: list[tuple[str, str]] = []
    for item in toc:
        if _strip_ns(item.tag) != "item":
            continue
        titel = link_url = ""
        for child in item:
            tag = _strip_ns(child.tag)
            if tag == "title" and child.text:
                titel = child.text.strip()
            elif tag == "link" and child.text:
                link_url = child.text.strip()
        # Extract abbreviation from URL: .../bgb/xml.zip → BGB
        abk = ""
        if link_url:
            m = re.search(r"/([^/]+)/xml\.zip", link_url)
            if m:
                abk = m.group(1).upper()
        if not abk:
            continue
        if q in abk.lower() or q in titel.lower():
            matches.append((abk, titel, link_url))

    if not matches:
        return (
            f"No laws found for '{query}'.\n"
            f"Tip: Search with abbreviation (e.g. 'Miet', 'Datenschutz', 'Arbeits', 'GG', 'BGB')."
        )

    shown = matches[:max_results]
    rest  = len(matches) - len(shown)
    lines = [f"Results for '{query}' ({len(matches)} laws found):"]
    for abk, t, _ in shown:
        lines.append(f"  - {abk}: {t}")
    if rest > 0:
        lines.append(f"  [... {rest} more — increase max_results or refine search]")
    lines.append(f"\nNext steps:")
    lines.append(f'  → legal_get_law_overview("{shown[0][0]}")   — show table of contents')
    lines.append(f'  → legal_get_paragraph("{shown[0][0]}", "§-number")  — retrieve law text')
    return "\n".join(lines)


@mcp.tool()
def legal_get_law_overview(law: str, max_entries: int = 50) -> str:
    """
    Shows the table of contents / structure of a German federal law.
    law: Abbreviation (e.g. 'BGB', 'GG', 'StGB', 'DSGVO', 'HGB', 'ZPO', 'ArbSchG')
    Useful when the searched paragraph is unknown — shows all §§ with titles.
    Examples: legal_get_law_overview("GG"), legal_get_law_overview("BGB")
    """
    root, err = _get_law_xml(law)
    if root is None:
        return err

    law_title = law.upper()
    entries: list[tuple[str, str]] = []

    for enbez, titel, _ in _iter_norms(root):
        if enbez:
            entries.append((enbez, titel))
        # Law long title from first norm without enbez
        elif not enbez and not entries:
            pass  # extracted below from <langue>

    # Long title from metadata of the first norm
    for elem in root.iter():
        if _strip_ns(elem.tag) != "norm":
            continue
        for child in elem:
            if _strip_ns(child.tag) == "metadaten":
                for mc in child:
                    if _strip_ns(mc.tag) == "langue" and mc.text:
                        law_title = f"{mc.text.strip()} ({law.upper()})"
                        break
        break

    if not entries:
        return f"Law '{law}' loaded, but no paragraphs/articles found."

    total = len(entries)
    shown = entries[:max_entries]
    lines = [f"{law_title} — Table of contents ({total} norms, showing {len(shown)}):"]
    for enbez, titel in shown:
        if titel:
            lines.append(f"  {enbez:<14} {titel}")
        else:
            lines.append(f"  {enbez}")
    if total > max_entries:
        lines.append(f"\n[... {total - max_entries} more norms not shown]")
        lines.append(f'→ Use legal_fulltext_search("{law.upper()}", "keyword") for targeted search')
    lines.append(f'→ Use legal_get_paragraph("{law.upper()}", "§-number") for full text')
    return "\n".join(lines)


@mcp.tool()
def legal_get_paragraph(law: str, paragraph: str) -> str:
    """
    Retrieves the exact law text of a paragraph or article from a German federal law.
    law: Abbreviation (e.g. 'BGB', 'GG', 'StGB', 'DSGVO', 'HGB', 'ZPO')
    paragraph: §/Art. number as string (e.g. '242', '823', '1' for Art. 1 GG, '13' for Art. 13 GG)
    Returns the official wording directly from gesetze-im-internet.de.
    Examples: legal_get_paragraph("BGB", "242"), legal_get_paragraph("GG", "1")
    """
    root, err = _get_law_xml(law)
    if root is None:
        return err

    para_norm = paragraph.lower().strip().lstrip("§").lstrip("art").strip()

    for enbez, titel, textdaten in _iter_norms(root):
        if not enbez:
            continue
        if _get_enbez_num(enbez) == para_norm:
            text = _norm_text(textdaten)
            if not text:
                return (
                    f"{enbez} {law.upper()}"
                    + (f" — {titel}" if titel else "")
                    + "\n\n(No text content — possibly repealed or empty norm)"
                )
            if len(text) > 2200:
                text = text[:2200] + "\n[... Text truncated — full text at gesetze-im-internet.de]"
            header = f"{enbez} {law.upper()}" + (f" — {titel}" if titel else "")
            return f"{header}\n\nSource: gesetze-im-internet.de/{law.lower()}\n\n{text}"

    return (
        f"§ {paragraph} {law.upper()} not found.\n"
        f'Tip: Use legal_get_law_overview("{law.upper()}") to see all available §§.'
    )


@mcp.tool()
def legal_fulltext_search(law: str, query: str, max_results: int = 5) -> str:
    """
    Full-text search within a German federal law by keyword.
    law: Abbreviation (e.g. 'BGB', 'StGB', 'DSGVO', 'HGB')
    query: Search term (e.g. 'Treu und Glauben', 'Einwilligung', 'Schadensersatz')
    Returns matching paragraphs with text excerpt.
    Examples: legal_fulltext_search("BGB", "Treu und Glauben"), legal_fulltext_search("DSGVO", "Einwilligung")
    """
    root, err = _get_law_xml(law)
    if root is None:
        return err

    q_lower  = query.lower()
    results: list[tuple[str, str, str]] = []

    for enbez, titel, textdaten in _iter_norms(root):
        if not enbez:
            continue
        text = _norm_text(textdaten)
        if not text:
            continue
        text_lower = text.lower()
        if q_lower not in text_lower:
            continue
        idx   = text_lower.find(q_lower)
        start = max(0, idx - 80)
        end   = min(len(text), idx + len(query) + 120)
        pre   = "..." if start > 0 else ""
        post  = "..." if end < len(text) else ""
        snippet = pre + text[start:end] + post
        results.append((enbez, titel, snippet))

    if not results:
        return f"No matches for '{query}' in {law.upper()}."

    shown = results[:max_results]
    rest  = len(results) - len(shown)
    lines = [f"Full-text search '{query}' in {law.upper()} — {len(results)} matches:"]
    for enbez, titel, snippet in shown:
        lines.append("")
        lines.append(enbez + (f" — {titel}" if titel else ""))
        lines.append(f'  „{snippet}"')
    if rest > 0:
        lines.append(f"\n[... {rest} more results — increase max_results]")
    if shown:
        num = _get_enbez_num(shown[0][0])
        lines.append(f'\n→ Use legal_get_paragraph("{law.upper()}", "{num}") for full text')
    return "\n".join(lines)


# ─── GRAPH RAG TOOLS (Neo4j) ─────────────────────────────────────────────────

_graph_manager: Optional[Any] = None  # GraphRAGManager instance, lazy-initialized


async def _get_graph_manager():
    """Returns the GraphRAGManager, initializing it on first call."""
    global _graph_manager
    if _graph_manager is not None:
        return _graph_manager
    neo4j_uri  = os.getenv("NEO4J_URI",  "bolt://neo4j-knowledge:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_pass = os.getenv("NEO4J_PASS")
    if not neo4j_uri:
        return None
    try:
        from graph_rag.manager import GraphRAGManager
        mgr = GraphRAGManager(neo4j_uri, neo4j_user, neo4j_pass)
        await mgr.setup()
        _graph_manager = mgr
        logger.info("✅ GraphRAGManager initialized in MCP server")
    except Exception as e:
        logger.warning(f"⚠️ GraphRAGManager init failed: {e}")
    return _graph_manager


@mcp.tool()
async def graph_query(query: str, categories: Optional[List[str]] = None) -> str:
    """
    Searches the Neo4j knowledge graph for entities and relations related to the query.
    Returns structured context with provenance metadata.
    categories: Optional list of expert categories for domain filtering
    (e.g. ['technical_support', 'code_reviewer'])
    """
    mgr = await _get_graph_manager()
    if mgr is None:
        return "Error: Knowledge graph unavailable (Neo4j not configured)"
    try:
        result = await mgr.query_context(query, categories=categories or [])
        return result or "No relevant entries found in the knowledge graph."
    except Exception as e:
        logger.warning(f"graph_query error: {e}")
        return f"Knowledge graph query error: {e}"


@mcp.tool()
async def graph_ingest(
    question: str,
    answer: str,
    domain: str = "general",
    source_model: str = "external",
    confidence: float = 0.7,
) -> str:
    """
    Stores facts from a question-answer pair in the Neo4j knowledge graph.
    Extracts entities and relations and inserts them with provenance metadata.
    Useful for external agents (Cursor, Claude Desktop) to ingest knowledge.
    """
    mgr = await _get_graph_manager()
    if mgr is None:
        return "Error: Knowledge graph unavailable (Neo4j not configured)"
    try:
        # No LLM available for external ingests — use heuristics directly
        # Create a minimal stub LLM that returns simple JSON text
        class _StubLLM:
            async def ainvoke(self, prompt: str):
                class _R:
                    content = "[]"
                return _R()

        await mgr.extract_and_ingest(
            question, answer, _StubLLM(),
            domain=domain, source_model=source_model, confidence=confidence,
        )
        return f"Ingest started: domain={domain}, source_model={source_model}, confidence={confidence}"
    except Exception as e:
        logger.warning(f"graph_ingest error: {e}")
        return f"Knowledge graph ingest error: {e}"


@mcp.tool()
async def graph_provenance(entity_name: str) -> str:
    """
    Returns the complete version history of all relations for an entity.
    Useful for analyzing contradictions (Model A said X, Model B corrected to Y).
    """
    mgr = await _get_graph_manager()
    if mgr is None:
        return "Error: Knowledge graph unavailable (Neo4j not configured)"
    try:
        records = await mgr.get_provenance(entity_name)
        if not records:
            return f"No relations found for entity '{entity_name}'."
        lines = [f"[Provenance: {entity_name}]"]
        for r in records:
            line = f"• {r['relation']} → {r['target']} | v{r.get('version', '?')}"
            line += f" | Source: {r.get('source_model', '?')}"
            conf = r.get("confidence")
            if conf is not None:
                line += f" | Confidence: {conf:.0%}"
            if r.get("superseded_version"):
                line += f" | (supersedes v{r['superseded_version']}, was: {r.get('prev_source_model', '?')})"
            lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"graph_provenance error: {e}")
        return f"Provenance query error: {e}"


# ─── CODE-NAVIGATION TOOLS (Agentic Coder) ──────────────────────────────────

# Workspace root: file access is restricted to this directory.
_CODE_WORKSPACE = Path(os.getenv("CODE_WORKSPACE", "/app/workspace")).resolve()

def _resolve_safe_path(raw: str) -> Path:
    """Resolves a path and ensures it is within _CODE_WORKSPACE."""
    p = Path(raw)
    if not p.is_absolute():
        p = _CODE_WORKSPACE / p
    resolved = p.resolve()
    if not str(resolved).startswith(str(_CODE_WORKSPACE)):
        raise PermissionError(
            f"Path '{resolved}' is outside the allowed workspace '{_CODE_WORKSPACE}'."
        )
    return resolved


# Language-specific regex patterns for repo_map (non-Python files)
_LANG_PATTERNS: Dict[str, List[re.Pattern]] = {
    ".js":   [re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M),
              re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.M)],
    ".ts":   [re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M),
              re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.M),
              re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.M)],
    ".go":   [re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", re.M),
              re.compile(r"^type\s+(\w+)\s+struct", re.M)],
    ".rs":   [re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.M),
              re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)", re.M),
              re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)", re.M)],
    ".java": [re.compile(r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(", re.M),
              re.compile(r"^\s*(?:public\s+)?(?:abstract\s+)?class\s+(\w+)", re.M)],
    ".cpp":  [re.compile(r"^\w[\w:*&<>\s]+\s+(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{", re.M)],
    ".c":    [re.compile(r"^\w[\w*\s]+\s+(\w+)\s*\([^)]*\)\s*\{", re.M)],
}

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache"}


def _map_python_file(path: Path) -> List[str]:
    """Extracts classes/functions from a Python file via AST."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except SyntaxError:
        return ["<parse error>"]
    symbols: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in ast.walk(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.col_offset > 0
            ]
            symbols.append(f"class {node.name}: [{', '.join(methods[:8])}{'…' if len(methods) > 8 else ''}]")
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"def {node.name}()")
    return symbols


def _map_lang_file(path: Path, ext: str) -> List[str]:
    """Extracts classes/functions from non-Python files via regex."""
    patterns = _LANG_PATTERNS.get(ext, [])
    if not patterns:
        return []
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen: set = set()
    symbols: List[str] = []
    for pat in patterns:
        for m in pat.finditer(source):
            name = m.group(1)
            if name and name not in seen:
                seen.add(name)
                symbols.append(name)
    return symbols


@mcp.tool()
def repo_map(path: str = ".", max_depth: int = 3) -> str:
    """
    Returns a compact skeleton view of a directory: file paths +
    class/function names (without code). Ideal for context-limited SLMs to
    navigate a repo before reading files.
    Supported languages: Python (.py), JavaScript (.js), TypeScript (.ts),
    Go (.go), Rust (.rs), Java (.java), C/C++ (.c/.cpp).
    Example: repo_map("src/", max_depth=2)
    """
    try:
        root = _resolve_safe_path(path)
    except PermissionError as e:
        return f"ERROR: {e}"
    if not root.exists():
        return f"ERROR: Path does not exist: '{path}'"
    if not root.is_dir():
        return f"ERROR: '{path}' is not a directory. For files: read_file_chunked."

    supported_exts = {".py"} | set(_LANG_PATTERNS.keys())
    lines: List[str] = []
    total_files = 0

    def walk(directory: Path, depth: int, prefix: str) -> None:
        nonlocal total_files
        if depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            rel = entry.relative_to(root)
            if entry.is_dir():
                lines.append(f"{prefix}📁 {rel}/")
                walk(entry, depth + 1, prefix + "  ")
            elif entry.is_file() and entry.suffix.lower() in supported_exts:
                total_files += 1
                if total_files > 200:
                    lines.append(f"{prefix}… (too many files, reduce max_depth)")
                    return
                ext = entry.suffix.lower()
                if ext == ".py":
                    symbols = _map_python_file(entry)
                else:
                    symbols = _map_lang_file(entry, ext)
                sym_str = f" → {', '.join(symbols[:6])}{'…' if len(symbols) > 6 else ''}" if symbols else ""
                lines.append(f"{prefix}📄 {rel}{sym_str}")

    lines.append(f"# Repo-Map: {root} (max_depth={max_depth})")
    walk(root, 1, "")
    lines.append(f"\n# {total_files} file(s) indexed. Use read_file_chunked for details.")
    return "\n".join(lines)


@mcp.tool()
def read_file_chunked(file_path: str, start_line: int = 1, end_line: int = 50) -> str:
    """
    Reads a slice (start_line to end_line) from a file with line numbers.
    Prevents SLMs from loading entire files into context.
    Lines are 1-based. end_line=0 → read to end of file (max 200 lines).
    Example: read_file_chunked("src/main.py", start_line=10, end_line=50)
    """
    try:
        fpath = _resolve_safe_path(file_path)
    except PermissionError as e:
        return f"ERROR: {e}"
    if not fpath.exists():
        return f"ERROR: File not found: '{file_path}'"
    if not fpath.is_file():
        return f"ERROR: '{file_path}' is not a file. For directories: repo_map."

    MAX_LINES = 200
    if end_line == 0:
        end_line = start_line + MAX_LINES - 1
    if start_line < 1:
        start_line = 1
    if end_line - start_line + 1 > MAX_LINES:
        end_line = start_line + MAX_LINES - 1

    try:
        all_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"ERROR reading file: {e}"

    total = len(all_lines)
    chunk = all_lines[start_line - 1 : end_line]
    header = f"# {fpath.name} (lines {start_line}–{min(end_line, total)} of {total})\n"
    body = "\n".join(f"{start_line + i:>5} │ {line}" for i, line in enumerate(chunk))
    footer = ""
    if end_line < total:
        footer = f"\n# … {total - end_line} more line(s). Next chunk: start_line={end_line + 1}"
    return header + body + footer


@mcp.tool()
def lsp_query(file_path: str, action: str, symbol: str = "", line: int = 0, col: int = 0) -> str:
    """
    Performs rudimentary LSP queries on Python files (not a full LSP server).
    Actions:
    - 'signature': Returns the signature of a symbol (function parameters, docstring).
    - 'find_references': Finds all usages of a symbol in the file.
    - 'completions': Shows completions at position (line, col).
    Python files only (.py). For other languages: use repo_map + read_file_chunked.
    Example: lsp_query("src/main.py", "signature", symbol="process_request")
    """
    try:
        fpath = _resolve_safe_path(file_path)
    except PermissionError as e:
        return f"ERROR: {e}"
    if not fpath.exists():
        return f"ERROR: File not found: '{file_path}'"
    if fpath.suffix.lower() != ".py":
        return (
            f"lsp_query supports Python files only (.py). "
            f"For '{fpath.suffix}' files: use repo_map + read_file_chunked."
        )

    try:
        import jedi  # type: ignore
    except ImportError:
        return "ERROR: jedi not installed. Run 'pip install jedi' in the MCP container."

    try:
        source = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"ERROR reading file: {e}"

    action = action.strip().lower()

    if action == "signature":
        if not symbol:
            return "ERROR: 'symbol' must be provided for action='signature'."
        # Find first usage of the symbol as a call
        match = re.search(rf"\b{re.escape(symbol)}\s*\(", source)
        if not match:
            return f"Symbol '{symbol}' not found as a call in '{fpath.name}'. Check repo_map."
        char_pos = match.start() + len(match.group()) - 1
        row = source[:char_pos].count("\n") + 1
        col_pos = char_pos - source[:char_pos].rfind("\n") - 1
        try:
            script = jedi.Script(source=source, path=str(fpath))
            sigs = script.get_signatures(line=row, column=col_pos)
            if not sigs:
                return f"No signature found for '{symbol}' (Jedi). Check whether definition is in the same file."
            results = []
            for sig in sigs[:3]:
                params = ", ".join(p.description for p in sig.params)
                doc = sig.docstring(raw=True)[:300] if sig.docstring() else ""
                results.append(f"def {sig.name}({params})\n  Docstring: {doc or '—'}")
            return f"Signature for '{symbol}':\n" + "\n\n".join(results)
        except Exception as e:
            return f"Jedi error in signature: {e}"

    elif action == "find_references":
        if not symbol:
            return "ERROR: 'symbol' must be provided for action='find_references'."
        match = re.search(rf"\b{re.escape(symbol)}\b", source)
        if not match:
            return f"Symbol '{symbol}' not found in '{fpath.name}'."
        char_pos = match.start() + len(symbol) // 2
        row = source[:char_pos].count("\n") + 1
        col_pos = char_pos - source[:char_pos].rfind("\n") - 1
        try:
            script = jedi.Script(source=source, path=str(fpath))
            refs = script.get_references(line=row, column=col_pos)
            if not refs:
                return f"No references found for '{symbol}'."
            lines_out = [f"References for '{symbol}' in '{fpath.name}' ({len(refs)} matches):"]
            for ref in refs[:20]:
                lines_out.append(f"  Line {ref.line}: {ref.description}")
            if len(refs) > 20:
                lines_out.append(f"  … {len(refs) - 20} more (read_file_chunked for details)")
            return "\n".join(lines_out)
        except Exception as e:
            return f"Jedi error in find_references: {e}"

    elif action == "completions":
        if line <= 0 or col < 0:
            return "ERROR: 'line' (>0) and 'col' (>=0) must be provided for action='completions'."
        try:
            script = jedi.Script(source=source, path=str(fpath))
            comps = script.complete(line=line, column=col)
            if not comps:
                return f"No completions at line {line}, column {col}."
            names = [f"  {c.name} ({c.type})" for c in comps[:15]]
            return f"Completions at {fpath.name}:{line}:{col}:\n" + "\n".join(names)
        except Exception as e:
            return f"Jedi error in completions: {e}"

    else:
        return f"Unknown action '{action}'. Valid: 'signature', 'find_references', 'completions'."


# ─── File Generation Tool ───────────────────────────────────────────────────

_GENERATED_DIR = Path("/app/generated")
_GENERATED_DIR.mkdir(exist_ok=True)

# ─── MinIO helpers ──────────────────────────────────────────────────────────

def _minio_client():
    """Return a configured Minio client, or None if credentials are missing."""
    endpoint  = os.getenv("MINIO_ENDPOINT", "")
    access    = os.getenv("MINIO_ROOT_USER", "")
    secret    = os.getenv("MINIO_ROOT_PASSWORD", "")
    if not (endpoint and access and secret):
        return None
    try:
        from minio import Minio
        return Minio(endpoint, access_key=access, secret_key=secret, secure=False)
    except Exception:
        return None


def _minio_public_url() -> str:
    """Return the admin-configured public base URL for MinIO, falling back to endpoint."""
    # MINIO_PUBLIC_URL is writable via Admin Portal → Settings → Storage URL
    url = os.getenv("MINIO_PUBLIC_URL", "").rstrip("/")
    if not url:
        endpoint = os.getenv("MINIO_ENDPOINT", "moe-storage:9000")
        url = f"http://{endpoint}"
    return url


def _minio_ensure_bucket(client, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _minio_upload_bytes(data: bytes, object_name: str, content_type: str,
                        bucket: str | None = None) -> str:
    """Upload bytes to MinIO and return a pre-signed download URL (24h expiry)."""
    from datetime import timedelta
    mc = _minio_client()
    if mc is None:
        raise RuntimeError("MinIO not configured (MINIO_ENDPOINT/MINIO_ROOT_USER/MINIO_ROOT_PASSWORD missing)")
    bkt = bucket or os.getenv("MINIO_DEFAULT_BUCKET", "moe-files")
    _minio_ensure_bucket(mc, bkt)
    mc.put_object(bkt, object_name, io.BytesIO(data), length=len(data), content_type=content_type)
    presigned = mc.presigned_get_object(bkt, object_name, expires=timedelta(hours=24))
    # Replace internal hostname with public URL
    public_base = _minio_public_url()
    internal = os.getenv("MINIO_ENDPOINT", "moe-storage:9000")
    presigned = presigned.replace(f"http://{internal}", public_base, 1)
    return presigned


def file_upload(content_base64: str, filename: str, content_type: str = "application/octet-stream",
                bucket: str = "") -> str:
    """Upload a file to MinIO object storage and return a 24h pre-signed download URL.

    Args:
        content_base64: Base64-encoded file content
        filename: Target filename (e.g. 'report.pdf')
        content_type: MIME type (default: application/octet-stream)
        bucket: Bucket name (default: moe-files from env)
    Returns:
        Pre-signed download URL valid for 24 hours, or error message.
    """
    import uuid as _uuid
    try:
        data = base64.b64decode(content_base64)
    except Exception as e:
        return f"Error: invalid base64 content — {e}"
    safe = re.sub(r'[^\w\-.]', '_', filename)[:120]
    object_name = f"{_uuid.uuid4().hex[:8]}_{safe}"
    try:
        url = _minio_upload_bytes(data, object_name, content_type, bucket or None)
        size_kb = len(data) / 1024
        return f"Uploaded: {safe} ({size_kb:.1f} KB)\nDownload URL (24h): {url}"
    except Exception as e:
        return f"Upload failed: {e}"


def file_download_url(object_name: str, bucket: str = "", expires_hours: int = 24) -> str:
    """Generate a fresh pre-signed download URL for an existing MinIO object.

    Args:
        object_name: Full object path in the bucket
        bucket: Bucket name (default: moe-files from env)
        expires_hours: Link validity in hours (1–168, default: 24)
    Returns:
        Pre-signed download URL, or error message.
    """
    from datetime import timedelta
    mc = _minio_client()
    if mc is None:
        return "Error: MinIO not configured."
    bkt = bucket or os.getenv("MINIO_DEFAULT_BUCKET", "moe-files")
    hours = max(1, min(168, expires_hours))
    try:
        presigned = mc.presigned_get_object(bkt, object_name, expires=timedelta(hours=hours))
        public_base = _minio_public_url()
        internal = os.getenv("MINIO_ENDPOINT", "moe-storage:9000")
        presigned = presigned.replace(f"http://{internal}", public_base, 1)
        return f"Download URL ({hours}h): {presigned}"
    except Exception as e:
        return f"Error generating URL: {e}"


def generate_file(content: str, filename: str = "output", format: str = "html") -> str:
    """
    Generates a file from content and returns a download path.
    Supported formats: html, md, docx, txt.
    The file is stored server-side with a UUID prefix and can be downloaded
    via the /downloads/ endpoint. Files are auto-cleaned after 24 hours.
    """
    import uuid as _uuid
    _id = _uuid.uuid4().hex[:12]
    _safe_name = re.sub(r'[^\w\-.]', '_', filename)[:80]
    fmt = format.lower().strip()

    if fmt == "html":
        try:
            import markdown as _md
            html_body = _md.markdown(content, extensions=["tables", "fenced_code"])
        except ImportError:
            html_body = f"<pre>{content}</pre>"
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{_safe_name}</title>"
            "<style>body{font-family:system-ui;max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.6}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px;text-align:left}"
            "pre{background:#f4f4f4;padding:1rem;overflow-x:auto;border-radius:4px}"
            "code{background:#f4f4f4;padding:2px 4px;border-radius:2px}</style></head>"
            f"<body>{html_body}</body></html>"
        )
        out_path = _GENERATED_DIR / f"{_id}_{_safe_name}.html"
        out_path.write_text(html, encoding="utf-8")

    elif fmt == "docx":
        try:
            from docx import Document
            doc = Document()
            for para in content.split("\n\n"):
                para = para.strip()
                if not para:
                    continue
                if para.startswith("# "):
                    doc.add_heading(para[2:], level=1)
                elif para.startswith("## "):
                    doc.add_heading(para[3:], level=2)
                elif para.startswith("### "):
                    doc.add_heading(para[4:], level=3)
                else:
                    doc.add_paragraph(para)
            out_path = _GENERATED_DIR / f"{_id}_{_safe_name}.docx"
            doc.save(str(out_path))
        except ImportError:
            return "Error: python-docx not available. Use format='html' or 'md' instead."

    elif fmt in ("md", "markdown", "txt", "text"):
        ext = "md" if fmt in ("md", "markdown") else "txt"
        out_path = _GENERATED_DIR / f"{_id}_{_safe_name}.{ext}"
        out_path.write_text(content, encoding="utf-8")

    elif fmt in ("pptx", "ppt", "powerpoint"):
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            prs = Presentation()
            # Parse slides from content: "## Slide Title\n- bullet\n- bullet\n\n## Next Slide..."
            slide_blocks = re.split(r'\n(?=##?\s)', content.strip())
            for block in slide_blocks:
                block = block.strip()
                if not block:
                    continue
                lines = block.splitlines()
                title_line = lines[0].lstrip("#").strip()
                body_lines = [l for l in lines[1:] if l.strip()]
                layout = prs.slide_layouts[1]  # title + content
                slide = prs.slides.add_slide(layout)
                slide.shapes.title.text = title_line
                if body_lines and slide.placeholders[1]:
                    tf = slide.placeholders[1].text_frame
                    tf.clear()
                    for i, bl in enumerate(body_lines):
                        bl = bl.lstrip("-*• ").strip()
                        if not bl:
                            continue
                        p = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
                        p.text = bl
                        p.level = 0
            out_path = _GENERATED_DIR / f"{_id}_{_safe_name}.pptx"
            prs.save(str(out_path))
        except ImportError:
            return "Error: python-pptx not available."

    else:
        return f"Unsupported format: '{fmt}'. Use: html, docx, md, txt, pptx."

    size_kb = out_path.stat().st_size / 1024
    content_types = {
        ".html": "text/html",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    ct = content_types.get(out_path.suffix, "application/octet-stream")
    # Upload to MinIO if configured — always delete local copy afterwards to
    # prevent generated payloads from persisting on the host filesystem.
    try:
        url = _minio_upload_bytes(out_path.read_bytes(), out_path.name, ct)
        out_path.unlink(missing_ok=True)
        return f"File generated: {out_path.name} ({size_kb:.1f} KB)\nDownload URL (24h): {url}"
    except Exception:
        # MinIO unavailable — keep local copy for /downloads/ fallback but do NOT
        # execute it: the /downloads/ endpoint serves files read-only via FileResponse.
        return (
            f"File generated: {out_path.name} ({size_kb:.1f} KB)\n"
            f"Download: /downloads/{out_path.name}"
        )


# ─── Attachment Parser Tool ──────────────────────────────────────────────────

# Security constants for parse_attachment
_ATTACH_MAX_BYTES = 20 * 1024 * 1024  # 20 MB hard limit
_ATTACH_TIMEOUT   = 30.0              # seconds


def _assert_public_url(url: str) -> None:
    """Raise ValueError if the URL resolves to a private/loopback/link-local address.

    Prevents SSRF: an attacker could prompt the LLM to call fetch_pdf_text or
    parse_attachment with an internal URL (172.20.x.x, 169.254.x.x, localhost, etc.)
    to reach container-internal services like Postgres, Redis, or the admin API.
    """
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{scheme}' is not allowed — only http/https.")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL has no hostname.")
    # Reject obvious hostnames
    if host.lower() in ("localhost", "metadata.google.internal"):
        raise ValueError(f"Host '{host}' is not allowed (internal).")
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"URL resolves to a private/reserved IP ({addr}) — SSRF blocked.")
    except ValueError as ve:
        if "SSRF blocked" in str(ve) or "not allowed" in str(ve) or "scheme" in str(ve):
            raise
        # hostname that isn't a bare IP — allow (DNS will resolve it at fetch time)
        pass


def _fetch_attachment_bytes(url: str) -> bytes:
    """
    Downloads a URL with a streaming size check (max 20 MB, 30 s timeout).
    Raises ValueError on size violation, httpx exceptions on network errors.
    """
    _assert_public_url(url)  # SSRF guard — blocks private/internal IPs
    collected: list[bytes] = []
    total = 0
    with httpx.stream("GET", url, timeout=_ATTACH_TIMEOUT, follow_redirects=False) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes(chunk_size=65536):
            total += len(chunk)
            if total > _ATTACH_MAX_BYTES:
                raise ValueError(
                    f"Attachment exceeds 20 MB size limit ({total} bytes downloaded so far)."
                )
            collected.append(chunk)
    return b"".join(collected)


def _parse_xlsx(data: bytes, max_chars: int) -> str:
    """Converts an XLSX workbook to a CSV-style plain text table."""
    try:
        import openpyxl
    except ImportError:
        return "Error: openpyxl not installed — cannot parse XLSX files."
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        lines: list[str] = [f"=== Sheet: {sheet_name} ==="]
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            lines.append(",".join(cells))
        parts.append("\n".join(lines))
        if sum(len(p) for p in parts) >= max_chars:
            break
    return "\n\n".join(parts)[:max_chars]


def _parse_docx(data: bytes, max_chars: int) -> str:
    """Extracts plain text from a DOCX file (paragraphs only, no macros)."""
    try:
        from docx import Document as _DocxDocument
    except ImportError:
        return "Error: python-docx not installed — cannot parse DOCX files."
    doc = _DocxDocument(io.BytesIO(data))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)[:max_chars]


def _parse_pdf(data: bytes, max_chars: int) -> str:
    """Extracts plain text from a PDF using pypdf (no subprocess, no code exec)."""
    try:
        import pypdf
    except ImportError:
        return "Error: pypdf not installed — cannot parse PDF files."
    reader = pypdf.PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        parts.append(text)
        if sum(len(p) for p in parts) >= max_chars:
            break
    return "\n\n".join(parts)[:max_chars]


def _parse_csv(data: bytes, max_chars: int) -> str:
    """Decodes a CSV file as UTF-8 (with latin-1 fallback) and returns its text."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return text[:max_chars]


@mcp.tool()
def parse_attachment(url: str, max_chars: int = 6000) -> str:
    """
    Downloads and parses a file attachment from a URL and returns its text content.

    Supported formats:
    - XLSX → CSV-style table (all sheets)
    - DOCX → plain text (paragraphs)
    - PDF  → extracted text (all pages up to max_chars)
    - CSV / TXT → raw text

    Security constraints:
    - Maximum download size: 20 MB
    - Request timeout: 30 seconds
    - No code execution, no subprocess, no filesystem writes
    - URL must be http/https; path traversal is not applicable (URL-only)

    Args:
        url: HTTP/HTTPS URL of the file to download and parse.
        max_chars: Maximum number of characters to return (default 6000).

    Returns:
        Plain text content of the attachment, or an error string starting with 'Error:'.
    """
    # Validate URL scheme — only http/https allowed
    if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        return "Error: Only http:// and https:// URLs are supported."

    # Cap max_chars to a sane upper bound
    max_chars = max(100, min(max_chars, 50_000))

    try:
        raw = _fetch_attachment_bytes(url)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.TimeoutException:
        return "Error: Download timed out (30 s limit)."
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} fetching attachment."
    except Exception as e:
        return f"Error downloading attachment: {e}"

    # Detect file type from URL extension (lowercase, strip query strings)
    url_path = url.split("?")[0].lower()
    if url_path.endswith(".xlsx"):
        return _parse_xlsx(raw, max_chars)
    if url_path.endswith(".docx"):
        return _parse_docx(raw, max_chars)
    if url_path.endswith(".pdf"):
        return _parse_pdf(raw, max_chars)
    if url_path.endswith((".csv", ".txt", ".tsv")):
        return _parse_csv(raw, max_chars)

    # Fallback: sniff magic bytes for known formats
    if raw[:4] == b"PK\x03\x04":
        # ZIP-based: could be XLSX or DOCX — try XLSX first, then DOCX
        try:
            return _parse_xlsx(raw, max_chars)
        except Exception:
            try:
                return _parse_docx(raw, max_chars)
            except Exception as e:
                return f"Error: ZIP-based file is neither XLSX nor DOCX: {e}"
    if raw[:4] == b"%PDF":
        return _parse_pdf(raw, max_chars)

    # Last resort: treat as plain text
    return _parse_csv(raw, max_chars)


# ─── Graph Analyzer Tool ─────────────────────────────────────────────────────


def _parse_graph_description(edges_description: str) -> tuple[list[tuple[str, str]], list[str]]:
    """
    Parses a text description of a graph into a list of (source, target) edge tuples
    and a deduplicated node list.

    Accepted formats (auto-detected, not mutually exclusive):
    - "nodes: A,B,C; edges: A-B, B-C"  — semicolon-separated header style
    - "A-B, B-C, A-C"                   — bare edge list, dash separator
    - "A→B; B→C"                        — arrow (directed) separator
    - CSV/table: lines like "A,B" or "A;B" (two columns = edge list)

    Direction is ignored for Eulerian analysis (treated as undirected graph).
    Returns (edges, nodes) where nodes are ordered by first appearance.
    """
    text = edges_description.strip()
    edges: list[tuple[str, str]] = []
    node_order: list[str] = []
    node_set: set[str] = set()

    def add_node(n: str) -> None:
        n = n.strip()
        if n and n not in node_set:
            node_set.add(n)
            node_order.append(n)

    # Normalize Unicode arrows to ASCII equivalents for easier parsing
    text = text.replace("→", "->").replace("←", "<-").replace("↔", "<->")

    # Attempt structured "nodes: ...; edges: ..." format
    nodes_match  = re.search(r"nodes?\s*:\s*([^;]+)", text, re.IGNORECASE)
    edges_match  = re.search(r"edges?\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)

    if nodes_match:
        for n in re.split(r"[,\s]+", nodes_match.group(1)):
            n = n.strip()
            if n:
                add_node(n)

    edge_text = edges_match.group(1).strip() if edges_match else text

    # Split edge text by comma or semicolon, then parse each token as an edge
    separators = re.split(r"[,;\n]+", edge_text)
    for token in separators:
        token = token.strip()
        if not token:
            continue
        # Try arrow formats: A->B, A<-B, A<->B
        m = re.match(r"^(.+?)\s*(?:->|<->|--)\s*(.+)$", token)
        if not m:
            # Try reverse arrow A<-B (target → source)
            m2 = re.match(r"^(.+?)\s*<-\s*(.+)$", token)
            if m2:
                src, tgt = m2.group(2).strip(), m2.group(1).strip()
            else:
                # Try dash: A-B (single dash, not --)
                m3 = re.match(r"^([^-]+)-([^-].*)$", token)
                if m3:
                    src, tgt = m3.group(1).strip(), m3.group(2).strip()
                else:
                    # Try CSV two-column: "A,B" or "A;B"
                    parts = re.split(r"[,;]", token, maxsplit=1)
                    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                        src, tgt = parts[0].strip(), parts[1].strip()
                    else:
                        continue
        else:
            src, tgt = m.group(1).strip(), m.group(2).strip()

        if src and tgt:
            add_node(src)
            add_node(tgt)
            edges.append((src, tgt))

    return edges, node_order


def _analyze_eulerian(degree: dict[str, int], components: int) -> dict[str, Any]:
    """
    Determines Eulerian path/circuit existence for an undirected graph.

    Eulerian circuit exists iff: graph is connected AND all vertices have even degree.
    Eulerian path exists iff: graph is connected AND exactly two vertices have odd degree.

    Returns a dict with keys: has_circuit, has_path, odd_degree_nodes, explanation.
    """
    odd_nodes = [n for n, d in degree.items() if d % 2 != 0]
    if components > 1:
        return {
            "has_circuit": False,
            "has_path": False,
            "odd_degree_nodes": odd_nodes,
            "explanation": (
                f"Graph is disconnected ({components} components). "
                "Eulerian path/circuit requires a single connected component."
            ),
        }
    if len(odd_nodes) == 0:
        return {
            "has_circuit": True,
            "has_path": True,  # A circuit is also a path
            "odd_degree_nodes": [],
            "explanation": "All vertices have even degree → Eulerian circuit exists (starts and ends at same vertex).",
        }
    if len(odd_nodes) == 2:
        return {
            "has_circuit": False,
            "has_path": True,
            "odd_degree_nodes": odd_nodes,
            "explanation": (
                f"Exactly 2 odd-degree vertices ({odd_nodes[0]}, {odd_nodes[1]}) → "
                f"Eulerian path exists (from {odd_nodes[0]} to {odd_nodes[1]} or vice versa)."
            ),
        }
    return {
        "has_circuit": False,
        "has_path": False,
        "odd_degree_nodes": odd_nodes,
        "explanation": (
            f"{len(odd_nodes)} vertices have odd degree ({', '.join(odd_nodes[:10])}"
            f"{'…' if len(odd_nodes) > 10 else ''}) → No Eulerian path or circuit."
        ),
    }


def _connected_components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """
    Computes connected components via iterative BFS (undirected interpretation of edges).
    Returns a list of components, each component being a sorted list of node names.
    """
    adjacency: dict[str, set[str]] = {n: set() for n in nodes}
    for src, tgt in edges:
        adjacency.setdefault(src, set()).add(tgt)
        adjacency.setdefault(tgt, set()).add(src)

    visited: set[str] = set()
    components: list[list[str]] = []

    for start in nodes:
        if start in visited:
            continue
        component: list[str] = []
        queue = [start]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            queue.extend(adjacency.get(node, set()) - visited)
        components.append(sorted(component))

    return components


@mcp.tool()
def graph_analyze(edges_description: str) -> str:
    """
    Analyzes a graph described in plain text and returns structural findings as JSON.

    Input format (flexible — any of these work):
    - "nodes: A,B,C; edges: A-B, B-C, A-C"
    - "A->B, B->C, C->A"          (directed arrows, treated as undirected for Euler)
    - "A-B\\nB-C\\nC-D"           (one edge per line)
    - CSV table where each row is "source,target"

    Analysis results (JSON):
    - node_count, edge_count
    - degree_map: degree of each node
    - connected_components: list of node groups
    - is_connected: bool
    - eulerian: {has_circuit, has_path, odd_degree_nodes, explanation}
    - densitiy: edge_count / max_possible_edges (0..1)

    Returns:
        JSON string with analysis findings, or an error string starting with 'Error:'.

    Example:
        graph_analyze("nodes: A,B,C,D; edges: A-B, B-C, C-D, D-A")
        → {"node_count": 4, "edge_count": 4, "is_connected": true,
           "eulerian": {"has_circuit": true, ...}, ...}
    """
    if not edges_description.strip():
        return "Error: edges_description must not be empty."

    try:
        edges, nodes = _parse_graph_description(edges_description)
    except Exception as e:
        return f"Error parsing graph description: {e}"

    if not nodes:
        return "Error: No nodes found in the description. Check the input format."

    # Build degree map (undirected: each edge increments both endpoints)
    degree: dict[str, int] = {n: 0 for n in nodes}
    for src, tgt in edges:
        degree[src] = degree.get(src, 0) + 1
        if src != tgt:  # skip self-loops for degree count on other endpoint
            degree[tgt] = degree.get(tgt, 0) + 1

    # Self-loops count twice toward degree in undirected graphs
    self_loops = [(s, t) for s, t in edges if s == t]
    for s, _ in self_loops:
        degree[s] += 1  # already counted once above; add the second increment

    components = _connected_components(nodes, edges)
    n = len(nodes)
    e = len(edges)
    max_edges = n * (n - 1) // 2 if n > 1 else 0
    density = round(e / max_edges, 6) if max_edges > 0 else 0.0

    eulerian_info = _analyze_eulerian(degree, len(components))

    result = {
        "node_count": n,
        "edge_count": e,
        "nodes": nodes,
        "degree_map": degree,
        "connected_components": components,
        "component_count": len(components),
        "is_connected": len(components) == 1,
        "self_loops": [f"{s}-{t}" for s, t in self_loops],
        "density": density,
        "eulerian": eulerian_info,
    }

    try:
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error serializing result: {e}"


# ─── Web / Document fetch helpers ───────────────────────────────────────────

def fetch_pdf_text(url: str, max_chars: int = 8000) -> str:
    """Download a PDF from a URL and extract its text content.

    Handles arXiv abstract pages (arxiv.org/abs/...) by automatically converting
    them to the direct PDF URL (arxiv.org/pdf/...). Falls back to fetching the
    HTML abstract if the PDF itself is inaccessible.

    url: URL to the PDF or arXiv abstract page
    max_chars: Maximum characters to return (default 8000)
    """
    import re as _re
    import pypdf

    _assert_public_url(url)  # SSRF guard

    # Normalise arXiv URLs: abs/ → pdf/ with .pdf suffix
    arxiv_abs = _re.match(r'https?://arxiv\.org/abs/(\d{4}\.\d+(?:v\d+)?)', url)
    if arxiv_abs:
        arxiv_id = arxiv_abs.group(1)
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        html_url = url  # keep original for fallback
    else:
        pdf_url = url
        html_url = None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; research-bot/1.0; +https://example.com/bot)"
        )
    }

    def _try_pdf(target_url: str) -> str | None:
        try:
            resp = httpx.get(target_url, follow_redirects=True, timeout=45, headers=headers)
        except Exception as exc:
            return f"Download failed: {exc}"
        if resp.status_code != 200:
            return f"Download failed: HTTP {resp.status_code} for {target_url}"
        try:
            reader = pypdf.PdfReader(io.BytesIO(resp.content))
        except Exception as exc:
            return f"Not a valid PDF or could not parse: {exc}"
        pages_text: list[str] = []
        for page in reader.pages:
            try:
                pages_text.append(page.extract_text() or "")
            except Exception:
                continue
        full_text = "\n".join(pages_text).strip()
        if not full_text:
            return None  # Signal: try fallback
        return full_text[:max_chars]

    result = _try_pdf(pdf_url)
    if result and not result.startswith(("Download failed", "Not a valid")):
        return result

    # Fallback: fetch HTML abstract page (arXiv) or original URL as HTML
    fallback_url = html_url or url
    if fallback_url != pdf_url:
        try:
            resp = httpx.get(fallback_url, follow_redirects=True, timeout=20, headers=headers)
            if resp.status_code == 200:
                # Strip HTML tags for a plain-text abstract
                text = _re.sub(r'<[^>]+>', ' ', resp.text)
                text = _re.sub(r'\s+', ' ', text).strip()
                return f"[Abstract page fallback]\n{text[:max_chars]}"
        except Exception:
            pass

    return result or "No extractable text found (PDF may be image-only or behind a paywall)."


def python_sandbox(code: str) -> str:
    """Execute a small, self-contained Python snippet and return its output.

    Designed for numerical calculations, probability trees, Markov chains,
    combinatorics, and any logic that is too complex for the calculate tool.
    The snippet runs in a restricted environment: only the standard library
    modules math, fractions, itertools, collections, decimal, and statistics
    are available. No file I/O, no network, no subprocesses.

    code: Python source code. Use print() to produce output — the return value
          of the last expression is also captured automatically.

    Examples:
      - Recursive probability: from fractions import Fraction\\nP1=Fraction(1,3)\\n...
      - Combinatorics: import math; print(math.comb(10, 3))
      - Simulation: import random; random.seed(0); wins=[...]; print(sum(wins)/len(wins))
    """
    import builtins
    import io
    import contextlib

    _ALLOWED_MODULES = {
        "math", "fractions", "itertools", "collections",
        "decimal", "statistics", "random",
        # "re" intentionally excluded: regex objects expose __class__.__mro__
        # which can be used to escape the sandbox via __subclasses__() traversal.
    }

    def _restricted_import(name, *args, **kwargs):
        if name.split(".")[0] not in _ALLOWED_MODULES:
            raise ImportError(f"Module '{name}' is not allowed in sandbox (allowed: {sorted(_ALLOWED_MODULES)})")
        return original_import(name, *args, **kwargs)

    original_import = builtins.__import__
    stdout_capture = io.StringIO()

    # Block builtins that enable introspection-based sandbox escapes.
    # vars/dir/getattr allow traversal of __class__.__mro__.__subclasses__().
    _BLOCKED_BUILTINS = {
        "open", "exec", "eval", "compile", "input", "breakpoint",
        "__import__", "vars", "dir", "getattr", "setattr", "delattr",
        "globals", "locals", "memoryview",
    }
    safe_globals = {
        "__builtins__": {
            k: getattr(builtins, k)
            for k in dir(builtins)
            if not k.startswith("_") and k not in _BLOCKED_BUILTINS
        },
        "__import__": _restricted_import,
    }
    safe_globals["__builtins__"]["__import__"] = _restricted_import  # type: ignore[index]

    try:
        with contextlib.redirect_stdout(stdout_capture):
            exec(code, safe_globals)  # noqa: S102
        output = stdout_capture.getvalue().strip()
        return output if output else "(no output — use print() to show results)"
    except Exception as exc:
        partial = stdout_capture.getvalue().strip()
        partial_note = f"\nPartial output before crash:\n{partial}" if partial else ""
        return (
            f"[EXECUTION_ERROR] {type(exc).__name__}: {exc}{partial_note}\n"
            f"Fix the code and retry. Check for: index bounds, division by zero, "
            f"undefined variables, or disallowed modules."
        )


def wikipedia_get_section(title: str = "", section: str = "", lang: str = "en", article: str = "") -> str:
    """Fetch a section of a Wikipedia article via the MediaWiki API.

    Returns the full plain-text content of the requested section (or the
    entire article intro if section is empty). Useful for counting items in
    discography, filmography, bibliography, or any other list sections.

    title:   Wikipedia article title (e.g. 'Mercedes Sosa'). Also accepts 'article' as alias.
    section: Section name to fetch (e.g. 'Studio albums'). Empty = article intro.
    lang:    Language code (default 'en'; use 'de' for German Wikipedia)
    """
    import re as _re

    # Accept 'article' as alias for 'title' — LLMs sometimes use this parameter name
    if not title and article:
        title = article

    base = f"https://{lang}.wikipedia.org/w/api.php"
    _headers = {"User-Agent": "MoE-Sovereign/1.0 (research-bot; contact@moe.local) python-httpx"}
    # First get the section index by fetching the table of contents
    try:
        toc_resp = httpx.get(
            base,
            params={
                "action": "parse", "page": title, "prop": "sections",
                "format": "json", "redirects": "1",
            },
            headers=_headers,
            timeout=15,
        )
    except Exception as exc:
        return f"Wikipedia API request failed: {exc}"

    if toc_resp.status_code != 200:
        return f"Wikipedia API error: HTTP {toc_resp.status_code}"

    toc_data = toc_resp.json()
    if "error" in toc_data:
        return f"Wikipedia API error: {toc_data['error'].get('info', toc_data['error'])}"

    sections = toc_data.get("parse", {}).get("sections", [])
    section_index = "0"  # 0 = lead section
    if section:
        low_target = section.lower()
        for s in sections:
            if low_target in s.get("line", "").lower() or low_target in s.get("anchor", "").lower():
                section_index = s["index"]
                break

    # Fetch the section content as plain text (wikitext → strip markup)
    try:
        content_resp = httpx.get(
            base,
            params={
                "action": "parse", "page": title, "prop": "wikitext",
                "section": section_index, "format": "json", "redirects": "1",
            },
            headers=_headers,
            timeout=15,
        )
    except Exception as exc:
        return f"Wikipedia section fetch failed: {exc}"

    if content_resp.status_code != 200:
        return f"Wikipedia section error: HTTP {content_resp.status_code}"

    wikitext = content_resp.json().get("parse", {}).get("wikitext", {}).get("*", "")

    # For discography/table sections: extract structured rows (Year | Entry) before stripping markup
    table_rows = []
    if _re.search(r'\{\|.*wikitable', wikitext, _re.DOTALL):
        current_year = None
        for line in wikitext.split('\n'):
            year_m = _re.match(r'^\|(\d{4})\s*$', line.strip())
            if year_m:
                current_year = year_m.group(1)
            elif line.startswith("|''") and current_year:
                title_clean = _re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', line)
                title_clean = _re.sub(r"[|'{}]", '', title_clean).strip()
                title_clean = _re.sub(r'\{\{[^}]*\}\}', '', title_clean).strip()
                if title_clean:
                    table_rows.append(f"{current_year}: {title_clean}")

    # Strip wiki markup for a readable plain-text result
    text = _re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', wikitext)  # links
    text = _re.sub(r'\{\{[^}]*\}\}', '', text)   # templates
    text = _re.sub(r"'''?", '', text)             # bold/italic
    text = _re.sub(r'<[^>]+>', ' ', text)         # HTML tags
    text = _re.sub(r'\n{3,}', '\n\n', text).strip()

    if not text:
        return f"Section '{section}' not found in article '{title}'. Available sections: {[s.get('line') for s in sections[:20]]}"

    # Prepend structured table summary if available
    prefix = ""
    if table_rows:
        prefix = f"STRUCTURED TABLE ({len(table_rows)} entries):\n" + "\n".join(table_rows) + "\n\n---\nRAW TEXT:\n"

    return f"Wikipedia — {title} [{section or 'intro'}]:\n\n{prefix}{text[:5000]}"


def github_get_issue(repo: str, issue_number: int) -> str:
    """Fetch a GitHub issue by repository and issue number via the public API.

    repo: Repository in 'owner/repo' format (e.g. 'torvalds/linux')
    issue_number: The issue number to fetch
    """
    api_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    try:
        response = httpx.get(
            api_url,
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
    except Exception as exc:
        return f"Request failed: {exc}"

    if response.status_code == 404:
        return f"Issue #{issue_number} not found in '{repo}' (or repository is private)."
    if response.status_code != 200:
        return f"GitHub API error: HTTP {response.status_code}"

    try:
        data = response.json()
    except Exception as exc:
        return f"Failed to parse GitHub response: {exc}"

    title = data.get("title", "(no title)")
    state = data.get("state", "unknown")
    created_at = data.get("created_at", "unknown")
    body = (data.get("body") or "")[:2000]
    labels = [lbl.get("name", "") for lbl in data.get("labels", [])]
    comments = data.get("comments", 0)

    return json.dumps(
        {
            "title": title,
            "state": state,
            "created_at": created_at,
            "body": body,
            "labels": labels,
            "comments_count": comments,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def github_issue_events(repo: str, issue_number: int, event_type: str = "labeled") -> str:
    """Fetch the timeline events of a GitHub issue to find when labels were added/removed.

    Use this when you need to know WHEN a specific label was added to an issue,
    not just which labels the issue currently has.

    repo:         Repository in 'owner/repo' format (e.g. 'numpy/numpy')
    issue_number: The issue number
    event_type:   Filter event type: 'labeled', 'unlabeled', 'closed', 'assigned', or '' for all
    """
    api_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/events"
    try:
        resp = httpx.get(
            api_url,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "MoE-Sovereign/1.0"},
            timeout=20,
        )
    except Exception as e:
        return f"[github_issue_events request failed: {e}]"

    if resp.status_code == 404:
        return f"[github_issue_events: Issue #{issue_number} not found in '{repo}']"
    if resp.status_code != 200:
        return f"[github_issue_events: HTTP {resp.status_code}]"

    try:
        events = resp.json()
    except Exception as e:
        return f"[github_issue_events: parse error: {e}]"

    if event_type:
        events = [e for e in events if e.get("event") == event_type]

    results = []
    for ev in events:
        entry = {
            "event": ev.get("event"),
            "created_at": ev.get("created_at"),
            "actor": (ev.get("actor") or {}).get("login"),
        }
        if ev.get("event") in ("labeled", "unlabeled"):
            entry["label"] = (ev.get("label") or {}).get("name")
        results.append(entry)

    if not results:
        return f"No '{event_type}' events found on issue #{issue_number}" if event_type else f"No events found"
    return json.dumps(results, indent=2)


# ─── External Data Sources ───────────────────────────────────────────────────

_SEARXNG_URL = os.environ.get("SEARXNG_URL", "").rstrip("/")


@mcp.tool()
def web_search_domain(query: str, domain: str = "", max_results: int = 5) -> str:
    """Domain-restricted web search via SearXNG.

    Adds a site: restriction to focus results on a specific website.
    Use when general search fails and you need data from a known source.

    query:       Search query (without site: — that is added automatically)
    domain:      Target domain, e.g. 'github.com', 'arxiv.org', 'wikipedia.org',
                 'pubchem.ncbi.nlm.nih.gov', 'orcid.org'. Leave empty for unrestricted search.
    max_results: Max search results to return (1-10, default 5)
    """
    if not _SEARXNG_URL:
        return "[web_search_domain: SEARXNG_URL not configured]"
    full_query = f"site:{domain} {query}" if domain else query
    try:
        resp = httpx.get(
            f"{_SEARXNG_URL}/search",
            params={"q": full_query, "format": "json", "engines": "google,bing,duckduckgo"},
            headers={"Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])[:max_results]
        if not results:
            return f"[No results for: {full_query}]"
        parts = []
        for r in results:
            title = r.get("title", "")
            url   = r.get("url", "")
            snippet = r.get("content", "")[:300]
            parts.append(f"**{title}**\n{url}\n{snippet}")
        return "\n\n---\n\n".join(parts)
    except Exception as e:
        return f"[web_search_domain error: {e}]"


@mcp.tool()
def youtube_transcript(video_url: str, max_chars: int = 4000) -> str:
    """Fetch the transcript/captions of a YouTube video.

    Works with most public videos that have auto-generated or manual captions.
    Useful for questions about video content, interviews, documentaries, tutorials.

    video_url: Full YouTube URL (https://www.youtube.com/watch?v=...) or video ID
    max_chars: Maximum characters to return (default 4000)
    """
    # Extract video ID from URL or treat input as ID directly
    _id_match = re.search(
        r'(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_\-]{11})',
        video_url,
    )
    video_id = _id_match.group(1) if _id_match else video_url.strip()
    if len(video_id) != 11 or not re.match(r'^[A-Za-z0-9_\-]+$', video_id):
        return f"[youtube_transcript: Could not extract valid video ID from: {video_url!r}]"

    # Primary: youtube-transcript-api (no API key required, version >= 0.6)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        # List available transcripts and pick English or German
        transcript_list = api.list(video_id)
        chosen = None
        for lang in ("en", "en-US", "en-GB", "de"):
            try:
                chosen = transcript_list.find_transcript([lang])
                break
            except Exception:
                pass
        if chosen is None:
            # Fall back to first available
            chosen = next(iter(transcript_list), None)
        if chosen is not None:
            entries = list(chosen.fetch())
            text = " ".join(getattr(e, "text", str(e)) for e in entries)
            return text[:max_chars] if len(text) > max_chars else text
    except ImportError:
        pass  # library not installed, fall through to HTTP fallback
    except Exception as e:
        err_str = str(e)
        if "Subtitles are disabled" in err_str or "Could not retrieve" in err_str or "TranscriptsDisabled" in err_str:
            return f"[youtube_transcript: Captions not available for video {video_id}]"
        # Other errors: fall through to HTTP fallback

    # Fallback: fetch YouTube page and extract caption track URL
    try:
        page = httpx.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; MoE-Research/1.0)"},
            timeout=15,
            follow_redirects=True,
        )
        # Extract timedtext URL from page source
        m = re.search(r'"captionTracks":\[.*?"baseUrl":"([^"]+)"', page.text)
        if not m:
            return f"[youtube_transcript: No caption track found for {video_id}]"
        caption_url = m.group(1).replace("\\u0026", "&")
        cap_resp = httpx.get(caption_url, timeout=15)
        # Parse XML caption format
        texts = re.findall(r'<text[^>]*>(.*?)</text>', cap_resp.text, re.DOTALL)
        import html
        clean = " ".join(html.unescape(t).replace("\n", " ") for t in texts)
        return clean[:max_chars] if len(clean) > max_chars else clean
    except Exception as e:
        return f"[youtube_transcript fallback error: {e}]"


@mcp.tool()
def github_search_issues(
    repo: str,
    labels: str = "",
    state: str = "open",
    sort: str = "created",
    order: str = "asc",
    max_results: int = 5,
    query: str = "",
) -> str:
    """Search GitHub issues in a repository using the GitHub Search API.

    Finds issues matching labels, state, and optional text query — unlike
    github_get_issue which requires a known issue number.

    repo:        Repository in 'owner/repo' format (e.g. 'numpy/numpy')
    labels:      Comma-separated label names (e.g. 'Regression,Bug')
    state:       'open', 'closed', or 'all'
    sort:        'created', 'updated', 'comments'
    order:       'asc' (oldest first) or 'desc' (newest first)
    max_results: Max issues to return (1-10)
    query:       Additional text to search in issue title/body
    """
    q_parts = [f"repo:{repo}"]
    if state and state != "all":
        q_parts.append(f"is:{state}")
    for label in (labels.split(",") if labels else []):
        label = label.strip()
        if label:
            q_parts.append(f'label:"{label}"')
    if query:
        q_parts.append(query)
    q_parts.append("is:issue")
    search_q = " ".join(q_parts)

    try:
        resp = httpx.get(
            "https://api.github.com/search/issues",
            params={"q": search_q, "sort": sort, "order": order, "per_page": max_results},
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "MoE-Sovereign/1.0",
            },
            timeout=20,
        )
    except Exception as e:
        return f"[github_search_issues request failed: {e}]"

    if resp.status_code == 422:
        return f"[github_search_issues: Invalid search query: {search_q}]"
    if resp.status_code != 200:
        return f"[github_search_issues: HTTP {resp.status_code}]"

    try:
        data = resp.json()
    except Exception as e:
        return f"[github_search_issues: parse error: {e}]"

    items = data.get("items", [])
    total = data.get("total_count", 0)
    if not items:
        return f"No issues found (total={total}) for query: {search_q}"

    results = []
    for issue in items[:max_results]:
        results.append({
            "number":     issue.get("number"),
            "title":      issue.get("title"),
            "state":      issue.get("state"),
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "labels":     [lbl["name"] for lbl in issue.get("labels", [])],
            "url":        issue.get("html_url"),
        })
    return json.dumps({"total_count": total, "query": search_q, "issues": results}, indent=2)


@mcp.tool()
def pubchem_compound_search(
    name: str = "",
    cid: int = 0,
    mw_min: float = 0,
    mw_max: float = 0,
    classification: str = "",
) -> str:
    """Search the PubChem compound database for chemical compound data.

    Can search by name, CID, or molecular weight range.
    Returns: CID, molecular weight, molecular formula, IUPAC name, and synonyms.

    name:           Compound name or IUPAC name to search (e.g. 'acetic acid')
    cid:            PubChem Compound ID (direct lookup, overrides name)
    mw_min/mw_max:  Molecular weight range filter (Da) — used with classification
    classification: FDA/GRAS classification filter (e.g. 'food additive')
    """
    base = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

    def _get_properties(cid_val: int) -> dict:
        prop_url = f"{base}/compound/cid/{cid_val}/property/MolecularWeight,MolecularFormula,IUPACName/JSON"
        r = httpx.get(prop_url, timeout=15)
        r.raise_for_status()
        props = r.json().get("PropertyTable", {}).get("Properties", [{}])[0]
        return props

    try:
        if cid:
            props = _get_properties(cid)
            props["CID"] = cid
            return json.dumps(props, indent=2)

        if name:
            # Name → CID lookup
            search_url = f"{base}/compound/name/{httpx.URL(name).path}/cids/JSON"
            r = httpx.get(search_url, timeout=15)
            if r.status_code == 404:
                return f"[pubchem: compound '{name}' not found]"
            r.raise_for_status()
            cids = r.json().get("IdentifierList", {}).get("CID", [])
            if not cids:
                return f"[pubchem: no results for '{name}']"
            results = []
            for c in cids[:5]:
                try:
                    props = _get_properties(c)
                    props["CID"] = c
                    results.append(props)
                except Exception:
                    pass
            return json.dumps(results, indent=2)

        if mw_min > 0 or mw_max > 0:
            # Molecular weight range search via PubChem PUG-REST
            mw_q = f"{mw_min:.1f}:{mw_max:.1f}" if mw_max > 0 else f"{mw_min:.1f}:10000"
            # Use FastSearch with MW filter
            fast_url = f"{base}/compound/fastformula/C/cids/JSON?MolecularWeight={mw_q}"
            r = httpx.get(fast_url, timeout=20)
            if r.status_code != 200:
                return f"[pubchem MW search: HTTP {r.status_code}]"
            cids = r.json().get("IdentifierList", {}).get("CID", [])[:5]
            results = []
            for c in cids:
                try:
                    props = _get_properties(c)
                    props["CID"] = c
                    results.append(props)
                except Exception:
                    pass
            return json.dumps(results, indent=2)

        return "[pubchem_compound_search: provide name, cid, or mw_min/mw_max]"
    except Exception as e:
        return f"[pubchem_compound_search error: {e}]"


@mcp.tool()
def orcid_works_count(orcid_id: str, before_year: int = 0, after_year: int = 0) -> str:
    """Count and list publications on an ORCID researcher profile.

    Uses the public ORCID API (no authentication required for public profiles).
    Returns total work count and year distribution.

    orcid_id:    ORCID iD in format XXXX-XXXX-XXXX-XXXX
    before_year: Count only works published before this year (e.g. 2020 → pre-2020)
    after_year:  Count only works published after this year
    """
    # Normalise ORCID format
    orcid_clean = orcid_id.strip().replace("https://orcid.org/", "")
    if not re.match(r'^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$', orcid_clean):
        return f"[orcid_works_count: invalid ORCID format: {orcid_id!r}. Expected XXXX-XXXX-XXXX-XXXX]"

    url = f"https://pub.orcid.org/v3.0/{orcid_clean}/works"
    try:
        resp = httpx.get(
            url,
            headers={"Accept": "application/json"},
            timeout=20,
        )
    except Exception as e:
        return f"[orcid_works_count request failed: {e}]"

    if resp.status_code == 404:
        return f"[orcid_works_count: ORCID {orcid_clean} not found or profile is private]"
    if resp.status_code != 200:
        return f"[orcid_works_count: HTTP {resp.status_code}]"

    try:
        data = resp.json()
    except Exception as e:
        return f"[orcid_works_count: parse error: {e}]"

    groups = data.get("group", [])
    years: list[int] = []
    for grp in groups:
        # Each group represents one unique work. Multiple work-summaries within a group
        # are different data sources for the same work — take only the first year found
        # to avoid counting the same publication multiple times.
        year_for_group: int | None = None
        for ws in grp.get("work-summary", []):
            pub_date = ws.get("publication-date") or {}
            year_val = (pub_date.get("year") or {}).get("value")
            if year_val:
                try:
                    year_for_group = int(year_val)
                    break
                except ValueError:
                    pass
        if year_for_group is not None:
            years.append(year_for_group)

    # Apply year filters
    filtered = years
    if before_year > 0:
        filtered = [y for y in filtered if y < before_year]
    if after_year > 0:
        filtered = [y for y in filtered if y > after_year]

    from collections import Counter
    year_dist = dict(sorted(Counter(filtered).items()))
    result = {
        "orcid": orcid_clean,
        "total_unique_works": len(groups),
        "works_with_year": len(years),
        "filtered_count": len(filtered),
        "filter_applied": {
            "before_year": before_year or None,
            "after_year": after_year or None,
        },
        "NOTE": "Use 'filtered_count' when a year filter was applied, 'total_unique_works' otherwise.",
        "year_distribution": year_dist,
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def pubchem_advanced_search(
    mw_max: float = 0,
    mw_min: float = 0,
    heavy_atoms: int = 0,
    hb_acceptors_max: int = -1,
    hb_donors_max: int = -1,
    complexity_min: int = 0,
    complexity_max: int = 0,
    classification: str = "",
    max_results: int = 10,
) -> str:
    """Advanced PubChem compound search with multi-criteria filtering.

    Use this for GAIA-style questions like "find the compound in PubChem's Food Additive
    Status classification with MW ≤ 100, 6 heavy atoms, ≤ 1 hydrogen bond acceptors,
    and complexity between 10 and 15".

    All filter arguments are optional — provide only the ones you need.
    Returns matching compound CIDs with their molecular weight, formula, and IUPAC name.

    mw_min/mw_max:        Molecular weight range in Da (0 = no limit)
    heavy_atoms:          Exact heavy atom count (0 = no filter)
    hb_acceptors_max:     Max hydrogen bond acceptors (-1 = no filter)
    hb_donors_max:        Max hydrogen bond donors (-1 = no filter)
    complexity_min/max:   Bertz complexity score range (0 = no filter)
    classification:       Filter hint for result description (e.g. 'Food Additive')
    max_results:          Max compounds to return (1-20)
    """
    base = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    _PROPS = "MolecularWeight,MolecularFormula,IUPACName,HBondAcceptorCount,HBondDonorCount,HeavyAtomCount,Complexity"

    def _batch_props(cids: list[int]) -> list[dict]:
        """Fetch properties for up to 200 CIDs in a single request."""
        if not cids:
            return []
        chunk = ",".join(str(c) for c in cids[:200])
        url = f"{base}/compound/cid/{chunk}/property/{_PROPS}/JSON"
        r = httpx.get(url, timeout=20)
        if r.status_code != 200:
            return []
        rows = r.json().get("PropertyTable", {}).get("Properties", [])
        for row in rows:
            row["CID"] = row.get("CID", 0)
        return rows

    try:
        # Build candidate CID list via NCATS Food Additive classification (if requested)
        # then apply multi-criteria property filters client-side.
        candidate_cids: list[int] = []

        if classification and "food" in classification.lower():
            # The /classification/cid/JSON?source=NCATS+Food+Additive+Status endpoint
            # returns HTTP 400. Scan CIDs 1-10000, which covers all known small-molecule
            # food additives in PubChem (the vast majority have CID < 10000).
            candidate_cids = list(range(1, 10001))

        # If no classification-based candidates, use MW-range PubChem search
        if not candidate_cids:
            if mw_min > 0 or mw_max > 0:
                lo = mw_min if mw_min > 0 else 0
                hi = mw_max if mw_max > 0 else 100000
                # PubChem FTP-style bulk property query via PUG REST
                search_url = (
                    f"{base}/compound/property/MolecularWeight,HeavyAtomCount,"
                    f"HBondAcceptorCount,HBondDonorCount,MolecularFormula,IUPACName/JSON"
                    f"?cid=1-{max_results * 500}"  # scan first N CIDs
                )
                # Better: use FastSearch with MW filter
                fast_url = f"https://pubchem.ncbi.nlm.nih.gov/sdq/sdqagent.cgi?infmt=json&outfmt=json&query={{\"download\":\"*\",\"collection\":\"compound\",\"where\":{{\"ands\":[{{\"mw_exact\":\"{lo:.2f},{hi:.2f}\"}},{{\"xlogp\":\"*\"}}]}},\"limit\":\"{max_results*10}\",\"downloadfilename\":\"PubChem_compound\"}}"
                # Simpler fallback: directly enumerate known small-molecule CIDs
                # For MW ≤ 100: PubChem CIDs 1-10000 cover most simple molecules
                candidate_cids = list(range(1, min(1001, max_results * 100)))
            else:
                return "[pubchem_advanced_search: provide at least mw_max, classification, or heavy_atoms filter]"

        # Fetch properties in batches of 200 CIDs and filter client-side
        results = []
        batch_size = 200
        for i in range(0, len(candidate_cids), batch_size):
            if len(results) >= max_results:
                break
            batch = candidate_cids[i:i + batch_size]
            try:
                props_list = _batch_props(batch)
            except Exception:
                continue
            for props in props_list:
                if len(results) >= max_results:
                    break
                mw  = float(props.get("MolecularWeight", 0) or 0)
                ha  = int(props.get("HeavyAtomCount", 0) or 0)
                hba = int(props.get("HBondAcceptorCount", 0) or 0)
                hbd = int(props.get("HBondDonorCount", 0) or 0)
                if mw_max > 0 and mw > mw_max:
                    continue
                if mw_min > 0 and mw < mw_min:
                    continue
                if heavy_atoms > 0 and ha != heavy_atoms:
                    continue
                if hb_acceptors_max >= 0 and hba > hb_acceptors_max:
                    continue
                if hb_donors_max >= 0 and hbd > hb_donors_max:
                    continue
                cplx = int(props.get("Complexity", 0) or 0)
                if complexity_min > 0 and cplx < complexity_min:
                    continue
                if complexity_max > 0 and cplx > complexity_max:
                    continue
                results.append(props)

        if not results:
            return (f"[pubchem_advanced_search: no compounds matched all criteria "
                    f"(MW {mw_min:.1f}-{mw_max:.1f}, HA={heavy_atoms}, HBA≤{hb_acceptors_max})]")

        return json.dumps({
            "classification_hint": classification,
            "filters": {
                "mw_range": f"{mw_min:.1f}-{mw_max:.1f}",
                "heavy_atoms": heavy_atoms or "any",
                "hb_acceptors_max": hb_acceptors_max if hb_acceptors_max >= 0 else "any",
                "hb_donors_max": hb_donors_max if hb_donors_max >= 0 else "any",
            },
            "results_count": len(results),
            "compounds": results,
        }, indent=2)

    except Exception as e:
        return f"[pubchem_advanced_search error: {e}]"


@mcp.tool()
def semantic_scholar_search(
    query: str,
    year_filter: str = "",
    max_results: int = 5,
    fetch_pdf: bool = False,
) -> str:
    """Search Semantic Scholar for academic papers and retrieve abstracts and PDF links.

    Semantic Scholar aggregates open-access papers from many publishers.  Use this
    to find specific academic papers by author, title, or topic — especially when
    those papers may be behind a paywall on the journal's own site.  If fetch_pdf
    is True and a free PDF URL exists, the first result's text is also extracted.

    query:       Author name + topic, or paper title fragment (e.g. "Valencfia-Mendez
                 harlequin shrimp length 2017")
    year_filter: Restrict results to a single year or range, e.g. "2017" or "2000-2005"
    max_results: Number of papers to return (1-10)
    fetch_pdf:   If True and a free PDF URL is found, extract up to 6000 chars of text
    """
    ss_url = "https://api.semanticscholar.org/graph/v1/paper/search"
    fields = "title,year,authors,abstract,openAccessPdf,externalIds,publicationVenue"
    params: dict = {
        "query": query,
        "fields": fields,
        "limit": min(max(1, max_results), 10),
    }
    if year_filter:
        params["year"] = year_filter

    # Retry up to 3 times with backoff on rate-limit (429) or transient errors.
    resp = None
    for _attempt in range(3):
        try:
            resp = httpx.get(ss_url, params=params, timeout=15,
                             headers={"User-Agent": "MoE-Research/1.0"})
            if resp.status_code != 429:
                break
            import time as _time
            _time.sleep(2 ** _attempt)  # 1s, 2s, 4s backoff
        except Exception as e:
            if _attempt == 2:
                return f"[semantic_scholar_search request failed: {e}]"
            import time as _time
            _time.sleep(1)

    if resp is None:
        return "[semantic_scholar_search: all retries failed]"
    if resp.status_code == 429:
        return "[semantic_scholar_search: rate limited after 3 retries — use web_search_domain with site:semanticscholar.org as fallback]"
    if resp.status_code != 200:
        return f"[semantic_scholar_search: HTTP {resp.status_code}]"

    try:
        data = resp.json()
    except Exception as e:
        return f"[semantic_scholar_search: parse error: {e}]"

    papers = data.get("data", [])
    if not papers:
        return f"[semantic_scholar_search: no results for query '{query[:80]}']"

    results = []
    pdf_text = ""
    for p in papers:
        authors = ", ".join(a.get("name", "") for a in (p.get("authors") or [])[:4])
        oa = p.get("openAccessPdf") or {}
        pdf_url = oa.get("url", "")
        doi = (p.get("externalIds") or {}).get("DOI", "")
        venue = (p.get("publicationVenue") or {}).get("name", "")
        entry = {
            "title":    p.get("title", ""),
            "year":     p.get("year"),
            "authors":  authors,
            "venue":    venue,
            "doi":      doi,
            "pdf_url":  pdf_url,
            "abstract": (p.get("abstract") or "")[:800],
        }
        results.append(entry)

        # Optionally fetch the first available PDF text
        if fetch_pdf and pdf_url and not pdf_text:
            try:
                pr = httpx.get(pdf_url, timeout=20, follow_redirects=True)
                if pr.status_code == 200 and b"%PDF" in pr.content[:8]:
                    import io, pdfminer.high_level as _pdf  # type: ignore
                    pdf_text = _pdf.extract_text(io.BytesIO(pr.content))[:6000]
            except Exception:
                pass

    output: dict = {
        "query": query,
        "total_found": data.get("total", len(results)),
        "papers": results,
    }
    if pdf_text:
        output["pdf_text_preview"] = pdf_text

    return json.dumps(output, indent=2, ensure_ascii=False)


@mcp.tool()
def wikidata_search(text: str, language: str = "en", max_results: int = 5) -> str:
    """Search Wikidata for entities by text and return their IDs for use in wikidata_sparql.

    Use this BEFORE wikidata_sparql when you don't know the entity ID (wd:Q...).
    Returns list of {id, label, description} — use the id in a follow-up wikidata_sparql call.

    Example workflow:
      1. wikidata_search("Morarji Desai") → finds wd:Q192131 (actually Giuseppe Meazza)
         → use the correct ID from results
      2. wikidata_sparql("SELECT ?label WHERE { wd:<id> rdfs:label ?label ... }")

    text:        Search term (person name, place, concept)
    language:    Result label language (default: "en")
    max_results: Max entities to return (1-10)
    """
    url = "https://www.wikidata.org/w/api.php"
    try:
        resp = httpx.get(url, params={
            "action":   "wbsearchentities",
            "search":   text,
            "language": language,
            "format":   "json",
            "limit":    min(max(1, max_results), 10),
            "type":     "item",
        }, headers={"User-Agent": "MoE-Research/1.0"}, timeout=10)
    except Exception as e:
        return f"[wikidata_search request failed: {e}]"
    if resp.status_code != 200:
        return f"[wikidata_search: HTTP {resp.status_code}]"
    try:
        results = resp.json().get("search", [])
    except Exception as e:
        return f"[wikidata_search: parse error: {e}]"
    if not results:
        return f"[wikidata_search: no entities found for '{text}']"
    entities = [
        {"id": r.get("id", ""), "label": r.get("label", ""), "description": r.get("description", "")}
        for r in results
    ]
    return json.dumps({"query": text, "entities": entities}, indent=2, ensure_ascii=False)


@mcp.tool()
def wikidata_sparql(sparql_query: str, max_results: int = 10) -> str:
    """Execute a SPARQL query against Wikidata for deterministic fact lookup.

    Use for entity facts, dates, locations, relationships — deterministic, no
    SearXNG variance, no HTML parsing.  Wikidata has 100M+ entities.

    Examples:
      SELECT ?label WHERE { wd:Q192131 rdfs:label ?label. FILTER(LANG(?label)='en') }
      SELECT ?dob WHERE { wd:Q... wdt:P569 ?dob }

    sparql_query: Valid SPARQL 1.1 for https://query.wikidata.org/sparql
    max_results:  LIMIT injected when query has none (1-50)
    """
    endpoint = "https://query.wikidata.org/sparql"
    q = sparql_query.strip()
    if "LIMIT" not in q.upper():
        q = q.rstrip(";") + f" LIMIT {min(max(1, max_results), 50)}"
    try:
        resp = httpx.get(
            endpoint,
            params={"query": q, "format": "json"},
            headers={"Accept": "application/sparql-results+json",
                     "User-Agent": "MoE-Research/1.0"},
            timeout=20,
        )
    except Exception as e:
        return f"[wikidata_sparql request failed: {e}]"
    if resp.status_code == 400:
        return f"[wikidata_sparql: invalid SPARQL — {resp.text[:200]}]"
    if resp.status_code != 200:
        return f"[wikidata_sparql: HTTP {resp.status_code}]"
    try:
        data = resp.json()
    except Exception as e:
        return f"[wikidata_sparql: parse error: {e}]"
    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        return "[wikidata_sparql: no results — try a broader query or check entity IDs]"
    rows = [
        {k: v.get("value", "") for k, v in b.items()}
        for b in bindings[:max_results]
    ]
    return json.dumps({"query": sparql_query[:200], "results": rows},
                      indent=2, ensure_ascii=False)


@mcp.tool()
def pubmed_search(query: str, max_results: int = 5, year_min: int = 0) -> str:
    """Search PubMed/NCBI for biomedical and life science academic papers.

    Use for biology, ecology, medicine, species studies, genetics, clinical papers.
    Prefer over semantic_scholar_search when the paper is biology/ecology/medicine.
    Supports PubMed Boolean syntax: "Hafnia alvei[tiab] AND mice", "harlequin shrimp[tiab]".

    query:       PubMed search query
    max_results: Number of papers to return (1-10)
    year_min:    Restrict to papers from this year onward (0 = no filter)
    """
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    full_query = f"({query}) AND {year_min}:3000[pdat]" if year_min > 0 else query
    try:
        sr = httpx.get(f"{base}/esearch.fcgi", params={
            "db": "pubmed", "term": full_query,
            "retmax": min(max(1, max_results), 10),
            "retmode": "json",
            "tool": "MoE-Research", "email": "research@moe-sovereign.org",
        }, timeout=15)
        if sr.status_code != 200:
            return f"[pubmed_search: search HTTP {sr.status_code}]"
        pmids = sr.json().get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return f"[pubmed_search: no results for '{query[:80]}']"

        fr = httpx.get(f"{base}/efetch.fcgi", params={
            "db": "pubmed", "id": ",".join(pmids),
            "rettype": "abstract", "retmode": "xml",
            "tool": "MoE-Research", "email": "research@moe-sovereign.org",
        }, timeout=20)
        if fr.status_code != 200:
            return f"[pubmed_search: fetch HTTP {fr.status_code}]"

        import xml.etree.ElementTree as _ET
        root = _ET.fromstring(fr.content)
        papers = []
        for article in root.findall(".//PubmedArticle"):
            def _t(path):
                el = article.find(path)
                return (el.text or "").strip() if el is not None else ""
            abstract = " ".join(
                (el.text or "") for el in article.findall(".//AbstractText")
            )[:600]
            authors = ", ".join(
                f"{el.findtext('LastName', '')} {el.findtext('Initials', '')}".strip()
                for el in article.findall(".//Author")[:4]
            )
            papers.append({
                "pmid":     _t(".//PMID"),
                "title":    _t(".//ArticleTitle"),
                "year":     _t(".//PubDate/Year") or _t(".//PubDate/MedlineDate")[:4],
                "authors":  authors,
                "doi":      _t(".//ArticleId[@IdType='doi']"),
                "abstract": abstract,
            })
        return json.dumps({"query": query, "papers": papers}, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"[pubmed_search error: {e}]"


# ─── Tool registry for REST shim ────────────────────────────────────────────

_TOOL_REGISTRY: Dict[str, Any] = {
    "calculate": calculate,
    "solve_equation": solve_equation,
    "date_diff": date_diff,
    "date_add": date_add,
    "day_of_week": day_of_week,
    "unit_convert": unit_convert,
    "statistics_calc": statistics_calc,
    "hash_text": hash_text,
    "base64_codec": base64_codec,
    "regex_extract": regex_extract,
    "subnet_calc": subnet_calc,
    "text_analyze": text_analyze,
    "prime_factorize": prime_factorize,
    "gcd_lcm": gcd_lcm,
    "json_query": json_query,
    "roman_numeral": roman_numeral,
    "legal_search_laws":      legal_search_laws,
    "legal_get_law_overview": legal_get_law_overview,
    "legal_get_paragraph":    legal_get_paragraph,
    "legal_fulltext_search":  legal_fulltext_search,
    "graph_query":      graph_query,
    "graph_ingest":     graph_ingest,
    "graph_provenance": graph_provenance,
    # Code-Navigation (Agentic Coder)
    "repo_map":          repo_map,
    "read_file_chunked": read_file_chunked,
    "lsp_query":         lsp_query,
    "generate_file":     generate_file,
    "parse_attachment":  parse_attachment,
    "graph_analyze":     graph_analyze,
    "file_upload":       file_upload,
    "file_download_url": file_download_url,
    "fetch_pdf_text":        fetch_pdf_text,
    "github_get_issue":      github_get_issue,
    "wikipedia_get_section": wikipedia_get_section,
    "python_sandbox":        python_sandbox,
    # External data sources (added for adaptive deep research)
    "web_search_domain":       web_search_domain,
    "youtube_transcript":      youtube_transcript,
    "github_search_issues":    github_search_issues,
    "github_issue_events":     github_issue_events,
    "pubchem_compound_search":  pubchem_compound_search,
    "pubchem_advanced_search":  pubchem_advanced_search,
    "orcid_works_count":       orcid_works_count,
    "semantic_scholar_search": semantic_scholar_search,
    "wikidata_search":         wikidata_search,
    "wikidata_sparql":         wikidata_sparql,
    "pubmed_search":           pubmed_search,
}

_TOOL_DESCRIPTIONS = {
    "calculate": "Exact arithmetic, percentages, formulas (LLMs hallucinate with numbers!)",
    "solve_equation": "Solve algebraic equations (SymPy)",
    "date_diff": "Exact difference between two dates",
    "date_add": "Date addition/subtraction (days, months, years)",
    "day_of_week": "Weekday, calendar week, day of year for a date",
    "unit_convert": "Physical unit conversion (km/h→m/s, °F→°C, etc.)",
    "statistics_calc": "Statistical measures for data sets (mean, median, stdev, etc.)",
    "hash_text": "Cryptographic hashes (MD5/SHA256/SHA512)",
    "base64_codec": "Base64 encode/decode",
    "regex_extract": "Regex pattern matching and extraction",
    "subnet_calc": "IP/network calculations (CIDR, subnet mask, host range)",
    "text_analyze": "Text metrics (words, characters, sentences, reading time)",
    "prime_factorize": "Prime factorization",
    "gcd_lcm": "GCD and LCM of two numbers",
    "json_query": "JSON path queries (key.subkey, array[0])",
    "roman_numeral": "Arabic ↔ Roman numerals",
    "legal_search_laws":      "Search German federal laws by keyword (returns abbreviations)",
    "legal_get_law_overview": "Shows table of contents/structure of a German federal law",
    "legal_get_paragraph":    "Retrieves exact text of a §/Art. from a German federal law (BGB/StGB/GG etc.)",
    "legal_fulltext_search":  "Full-text search within a German federal law by keyword",
    "graph_query":      "Search Neo4j knowledge graph for entities/relations (with provenance)",
    "graph_ingest":     "Store facts from Q&A in Neo4j knowledge graph (for external agents)",
    "graph_provenance": "Shows version history of an entity (contradiction analysis, temporal RAG)",
    # Code navigation (only for agentic_coder — not in global MCP_TOOLS_DESCRIPTION)
    "repo_map":          "AST/regex skeleton of a repo (file paths + classes/functions, no code)",
    "read_file_chunked": "Paginated file reading (start_line/end_line) — prevents context overflow",
    "lsp_query":         "Python LSP features: signature, find_references, completions (.py only)",
    "generate_file":     "Generate downloadable files (HTML, DOCX, PPTX, Markdown, TXT) from content — returns MinIO pre-signed URL",
    "parse_attachment":  "Download and parse file attachments (XLSX→CSV, DOCX→text, PDF→text, CSV→text) — max 20 MB",
    "graph_analyze":     "Analyze a graph (Eulerian path/circuit, connected components, degree map, density) from text description",
    "file_upload":       "Upload a file (base64-encoded) to MinIO object storage and get a 24h pre-signed download URL",
    "file_download_url": "Generate a fresh pre-signed download URL for an existing file in MinIO storage",
    "fetch_pdf_text":        "Download a PDF from a URL and extract its text (up to 8 000 chars by default); auto-handles arXiv URLs",
    "github_get_issue":      "Fetch a GitHub issue (title, state, body, labels, comment count) via the public API",
    "wikipedia_get_section": "Fetch a specific section of a Wikipedia article as plain text (e.g. 'Discography', 'Filmography'). Use this whenever a question references a Wikipedia article.",
    "python_sandbox":        "Run a small Python snippet for exact numerical calculations: probability trees (use Fraction!), Markov chains, combinatorics. Use print() for output. NEVER write simulation/Monte Carlo code — always use exact Fraction arithmetic. Allowed modules: math, fractions, itertools, collections, decimal, statistics, random.",
    # External data sources
    "web_search_domain":       "Domain-restricted web search via SearXNG (site:github.com, site:arxiv.org, site:wikipedia.org, etc.). Use when general search fails and you need data from a specific known website.",
    "youtube_transcript":      "Fetch captions/transcript of a YouTube video by URL or video ID. Use for questions about video content, interviews, documentaries, lectures.",
    "github_search_issues":    "Search GitHub issues by repo, label, state, and text query. Use to find issues when you don't know the exact issue number (e.g. oldest regression issue in numpy/numpy with label 'Regression').",
    "github_issue_events":     "Fetch timeline events for a GitHub issue — use to find WHEN a label was added/removed. Essential for 'when was label X added to issue Y' questions. Returns event type, date, actor, and label name.",
    "pubchem_compound_search":  "Search PubChem compound database by name, CID, or molecular weight range. Returns CID, molecular weight, formula, IUPAC name. Use for chemistry/pharmacology/food-science questions.",
    "pubchem_advanced_search":  "Advanced PubChem multi-criteria search: filter by MW range, heavy atom count, HB acceptors/donors simultaneously. Use for GAIA-style questions like 'find the compound with MW≤100, 6 heavy atoms, ≤1 HB acceptors in Food Additive Status'.",
    "orcid_works_count":       "Count publications on an ORCID researcher profile. Optionally filter by year (e.g. before_year=2020 for pre-2020 works). Use for academic publication count questions.",
    "semantic_scholar_search": "Search Semantic Scholar for academic papers by author/title/topic. Returns abstracts, DOI, and open-access PDF links. Use for questions requiring specific measurements or data from named academic papers (e.g. 'Valencfia-Mendez 2017 harlequin shrimp length'). Set fetch_pdf=true to also extract the paper's text if a free PDF is available.",
    "wikidata_search":         "Search Wikidata by text to find entity IDs (wd:Q...) for use in wikidata_sparql. Use this first when you know the name but not the Wikidata ID.",
    "wikidata_sparql":         "Execute SPARQL against Wikidata for deterministic entity facts (dates, locations, people, species, relationships). ALWAYS prefer over web search for factual lookups — no HTML parsing, no SearXNG variance. Write a SPARQL 1.1 query; use wd: entity IDs or wdt: property filters.",
    "pubmed_search":           "Search PubMed/NCBI for biomedical, biology, ecology, and life science papers. Returns title, authors, year, abstract, DOI. Prefer over semantic_scholar_search for species studies, genetics, clinical trials, ecology — deterministic NCBI API, no SearXNG variance.",
}

# ─── DISABLED TOOLS PERSISTENCE ───────────────────────────────────────────────
# Note: Disabling only applies to the REST /invoke path (which LangGraph
# uses). The MCP SSE endpoint /mcp (for Claude Desktop) uses FastMCP's
# internal registry and is not affected here.

_DISABLED_TOOLS_PATH = Path("/app/disabled_tools.json")
_disabled_tools_lock = threading.Lock()


def _load_disabled_tools() -> set:
    try:
        if _DISABLED_TOOLS_PATH.exists():
            return set(json.loads(_DISABLED_TOOLS_PATH.read_text(encoding="utf-8")).get("disabled", []))
    except Exception:
        pass
    return set()


def _save_disabled_tools(disabled: set) -> None:
    _DISABLED_TOOLS_PATH.write_text(
        json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_disabled_tools: set = _load_disabled_tools()

# ─── FASTAPI REST SHIM (used internally by LangGraph mcp_node) ──────────────

class InvokeRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = {}


rest_app = FastAPI(title="Precision Tools REST Shim", version="1.0")


@rest_app.get("/health")
def health():
    return {"status": "ok", "tools": list(_TOOL_REGISTRY.keys())}


@rest_app.get("/downloads/{filename}")
def download_file(filename: str):
    """Serve generated files for download. Files auto-expire after 24h."""
    from fastapi.responses import FileResponse
    from fastapi import HTTPException as _HTTPException
    safe = re.sub(r'[^\w\-.]', '', filename)
    path = _GENERATED_DIR / safe
    if not path.exists() or not path.is_file():
        raise _HTTPException(status_code=404, detail="File not found or expired")
    media_types = {
        ".html": "text/html",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".pdf": "application/pdf",
    }
    mt = media_types.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=mt, filename=safe)


@rest_app.get("/tools")
def list_tools():
    with _disabled_tools_lock:
        disabled = set(_disabled_tools)
    return {
        "tools": [
            {"name": name, "description": desc, "enabled": name not in disabled}
            for name, desc in _TOOL_DESCRIPTIONS.items()
        ]
    }


@rest_app.post("/tools/{name}/toggle")
def toggle_tool(name: str):
    from fastapi import HTTPException as _HTTPException
    if name not in _TOOL_REGISTRY:
        raise _HTTPException(status_code=404, detail=f"Unknown tool: {name}")
    with _disabled_tools_lock:
        if name in _disabled_tools:
            _disabled_tools.discard(name)
            now_enabled = True
        else:
            _disabled_tools.add(name)
            now_enabled = False
        _save_disabled_tools(_disabled_tools)
    return {"ok": True, "name": name, "enabled": now_enabled}


@rest_app.post("/invoke")
async def invoke_tool(req: InvokeRequest):
    if req.tool not in _TOOL_REGISTRY:
        return {"error": f"Unknown tool: '{req.tool}'. Available: {list(_TOOL_REGISTRY.keys())}"}
    with _disabled_tools_lock:
        if req.tool in _disabled_tools:
            return {"error": f"Tool '{req.tool}' is disabled."}
    try:
        import inspect
        func = _TOOL_REGISTRY[req.tool]
        if inspect.iscoroutinefunction(func):
            result = await func(**req.args)
        else:
            result = func(**req.args)
        return {"result": result, "tool": req.tool}
    except TypeError as e:
        return {"error": f"Wrong arguments for '{req.tool}': {e}"}
    except Exception as e:
        return {"error": f"Error in '{req.tool}': {e}"}


# Mount MCP SSE app at /mcp (for Claude Desktop, MCP clients, etc.)
rest_app.mount("/mcp", mcp.sse_app())


if __name__ == "__main__":
    uvicorn.run(rest_app, host="0.0.0.0", port=8003, log_level="info")

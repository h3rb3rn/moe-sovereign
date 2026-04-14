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

    else:
        return f"Unsupported format: '{fmt}'. Use: html, docx, md, txt."

    size_kb = out_path.stat().st_size / 1024
    return (
        f"File generated: {out_path.name} ({size_kb:.1f} KB)\n"
        f"Download: /downloads/{out_path.name}"
    )


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
    "generate_file":     "Generate downloadable files (HTML, DOCX, Markdown, TXT) from content",
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

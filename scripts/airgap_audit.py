#!/usr/bin/env python3
"""airgap_audit.py — Scan for external dependencies and localize them.

Two resolution strategies:
  1. Binary assets (JS/CSS/fonts/images): downloaded to a local static dir
     and all references in source files are rewritten to local paths.
  2. Instruction text in Markdown/Python: the local MoE AI rewrites the
     surrounding paragraph to use offline-safe alternatives (Canvas API,
     system fonts, CSS animations, etc.).

Usage:
    python3 scripts/airgap_audit.py [OPTIONS] [DIRS ...]

Options:
    --fix              Apply fixes in-place (default: scan-only / dry-run)
    --static-dir DIR   Where to store downloaded assets
                       (default: admin_ui/static)
    --ai-url URL       Local MoE chat-completions endpoint
                       (default: http://localhost:8002/v1/chat/completions)
    --model MODEL      Model to use for AI rewrites
                       (default: moe-orchestrator)
    --ext LIST         Comma-separated file extensions to scan
                       (default: html,md,py,js,css,yml)
    --exclude DIR      Additional directory to exclude (repeatable)
    --report FILE      Write JSON report to FILE

Examples:
    # Scan three repos, dry-run
    python3 scripts/airgap_audit.py \\
        /opt/moe-sovereign \\
        /opt/moe-sovereign/moe-codex \\
        /opt/moe-sovereign/moe-libris

    # Fix in-place, store assets in custom dir
    python3 scripts/airgap_audit.py --fix \\
        --static-dir admin_ui/static \\
        /opt/moe-sovereign
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# URL detection patterns
# ---------------------------------------------------------------------------

# Matches loadable binary assets referenced in code
_ASSET_PATTERNS: list[tuple[str, re.Pattern]] = [
    # <script src="https://...">
    ("script_src",
     re.compile(r'<script\b[^>]*\bsrc=["\'](?P<url>https?://[^\s"\']+)["\']', re.I)),
    # <link href="https://...">
    ("link_href",
     re.compile(r'<link\b[^>]*\bhref=["\'](?P<url>https?://[^\s"\']+)["\']', re.I)),
    # CSS url("https://...")
    ("css_url",
     re.compile(r'\burl\(["\']?(?P<url>https?://[^\s"\'()]+)["\']?\)', re.I)),
    # CSS @import "https://..."
    ("css_import",
     re.compile(r'@import\s+["\'](?P<url>https?://[^\s"\']+)["\']', re.I)),
    # Python string that renders into an HTML tag (covers f-strings / heredocs)
    ("py_html_src",
     re.compile(r'src=["\'](?P<url>https?://(?:cdn\.|cdnjs\.|unpkg\.|jsdelivr\.|fonts\.g)[^\s"\']+)["\']',
                re.I)),
    ("py_html_href",
     re.compile(r'href=["\'](?P<url>https?://(?:cdn\.|cdnjs\.|unpkg\.|jsdelivr\.|fonts\.g)[^\s"\']+)["\']',
                re.I)),
]

# Matches plain-text CDN references (instructions, documentation snippets)
# in markdown / python docstrings — these need AI rewriting, not downloading
_TEXT_CDN_PATTERN = re.compile(
    r'(?P<url>https?://(?:cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com'
    r'|fonts\.googleapis\.com|fonts\.gstatic\.com)[^\s"\'<>\)\]]+)',
    re.I,
)

# Extensions that carry binary-asset references
_ASSET_FILE_EXTS = {".html", ".css"}
# Extensions where CDN refs may be in instruction prose (need AI rewrite)
_INSTRUCTION_FILE_EXTS = {".md", ".py"}
# Fully skip these (generated / vendored / compiled)
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "site", "dist", "build", ".mypy_cache",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    filepath:    str
    line_no:     int
    pattern:     str          # which regex matched
    url:         str
    context:     str          # surrounding line(s)
    kind:        str          # "asset" | "instruction"
    resolved:    bool  = False
    local_path:  str   = ""
    error:       str   = ""


@dataclass
class Report:
    scanned_files: int           = 0
    findings:      list[Finding] = field(default_factory=list)
    fixed:         int           = 0
    errors:        int           = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_to_local_name(url: str) -> str:
    """Derive a stable local filename from a URL."""
    path = url.split("?")[0].rstrip("/")
    basename = path.rsplit("/", 1)[-1] or "resource"
    # Prefix with a short hash to avoid collisions across CDNs
    h = hashlib.sha1(url.encode()).hexdigest()[:6]
    # Keep only safe characters in the name
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", basename)
    return f"{h}_{safe}" if safe else f"{h}_asset"


def _local_subdir(url: str) -> str:
    """Return 'js' or 'css' or 'fonts' based on URL extension."""
    low = url.lower().split("?")[0]
    if low.endswith(".js"):
        return "js"
    if low.endswith(".css"):
        return "css"
    if any(low.endswith(e) for e in (".woff", ".woff2", ".ttf", ".eot", ".otf")):
        return "fonts"
    if any(low.endswith(e) for e in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".ico")):
        return "img"
    return "vendor"


def _download_asset(url: str, static_root: Path) -> tuple[str, str]:
    """Download url into static_root/<subdir>/<name>. Returns (local_path, error)."""
    subdir = _local_subdir(url)
    name = _url_to_local_name(url)
    dest_dir = static_root / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name

    if dest.exists():
        return str(dest), ""

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "airgap-audit/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        dest.write_bytes(data)
        return str(dest), ""
    except Exception as exc:
        return "", str(exc)


def _relative_web_path(local_path: str, static_root: Path) -> str:
    """Convert an absolute local asset path to a web-root-relative /static/... path."""
    p = Path(local_path)
    try:
        rel = p.relative_to(static_root)
        # Walk up to find the 'static' dir name
        parts = list(static_root.parts)
        static_name = static_root.name  # e.g. 'static'
        return f"/{static_name}/{rel.as_posix()}"
    except ValueError:
        return local_path


# ---------------------------------------------------------------------------
# AI rewrite via local MoE
# ---------------------------------------------------------------------------

def _ai_rewrite_instruction(
    filepath: str,
    surrounding: str,
    url: str,
    ai_url: str,
    model: str,
) -> tuple[str, str]:
    """Ask the local MoE to rewrite a CDN-referencing instruction.

    Returns (rewritten_text, error).
    """
    prompt = (
        f"The following text in a prompt/skill file references an external CDN URL "
        f"that must be removed for airgap compliance.\n\n"
        f"File: {filepath}\n"
        f"External URL to remove: {url}\n\n"
        f"Current text:\n---\n{surrounding}\n---\n\n"
        "Rewrite this text to achieve the same purpose WITHOUT any external URL or CDN. "
        "Use only browser-native alternatives:\n"
        "- Vanilla JS / Canvas API / Web Animations API / CSS @keyframes\n"
        "- System fonts (Arial, Georgia, Helvetica, monospace, etc.)\n"
        "- No npm packages, no CDN, no external network resources\n\n"
        "Return ONLY the rewritten text block. Preserve formatting (markdown bold, "
        "code fences, bullet points) where present. Do not add explanations."
    )
    payload = json.dumps({
        "model":    model,
        "messages": [{"role": "user", "content": prompt}],
        "stream":   False,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            ai_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
        rewritten = data["choices"][0]["message"]["content"].strip()
        return rewritten, ""
    except Exception as exc:
        return "", str(exc)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def _scan_file(filepath: Path, report: Report) -> list[Finding]:
    """Scan a single file for external references. Returns new findings."""
    findings: list[Finding] = []
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return findings

    ext = filepath.suffix.lower()
    lines = text.splitlines()

    # For asset files: check both asset patterns AND text CDN patterns
    if ext in _ASSET_FILE_EXTS:
        for lineno, line in enumerate(lines, 1):
            for pat_name, pattern in _ASSET_PATTERNS:
                for m in pattern.finditer(line):
                    url = m.group("url")
                    if not _is_external_cdn(url):
                        continue
                    findings.append(Finding(
                        filepath=str(filepath),
                        line_no=lineno,
                        pattern=pat_name,
                        url=url,
                        context=line.strip(),
                        kind="asset",
                    ))

    # For instruction files: find plain-text CDN mentions
    if ext in _INSTRUCTION_FILE_EXTS:
        for lineno, line in enumerate(lines, 1):
            for m in _TEXT_CDN_PATTERN.finditer(line):
                url = m.group("url")
                # Skip if this looks like it's inside an actual HTML tag (already
                # handled by asset scanner above or not applicable here)
                context_line = line.strip()
                findings.append(Finding(
                    filepath=str(filepath),
                    line_no=lineno,
                    pattern="text_cdn",
                    url=url,
                    context=context_line,
                    kind="instruction",
                ))

    # Also check instruction-style refs in HTML (e.g. comments saying "from CDN")
    if ext in _ASSET_FILE_EXTS:
        for lineno, line in enumerate(lines, 1):
            if "cdn" in line.lower() and _TEXT_CDN_PATTERN.search(line):
                url_m = _TEXT_CDN_PATTERN.search(line)
                if url_m:
                    url = url_m.group("url")
                    # Only add if not already captured by asset patterns
                    already = any(f.line_no == lineno and f.url == url for f in findings)
                    if not already and _is_external_cdn(url):
                        findings.append(Finding(
                            filepath=str(filepath),
                            line_no=lineno,
                            pattern="text_cdn_in_html",
                            url=url,
                            context=line.strip(),
                            kind="instruction",
                        ))

    report.scanned_files += 1
    return findings


def _is_external_cdn(url: str) -> bool:
    """Return True if url is an external CDN/font host we should localize."""
    lower = url.lower()
    return any(h in lower for h in [
        "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com",
        "fonts.googleapis.com", "fonts.gstatic.com",
        "ajax.googleapis.com", "code.jquery.com",
        "stackpath.bootstrapcdn.com", "maxcdn.bootstrapcdn.com",
    ])


# ---------------------------------------------------------------------------
# Fixer
# ---------------------------------------------------------------------------

def _fix_asset(finding: Finding, static_root: Path) -> None:
    """Download the asset and rewrite the reference in-place."""
    local_path, err = _download_asset(finding.url, static_root)
    if err:
        finding.error = f"download failed: {err}"
        return

    web_path = _relative_web_path(local_path, static_root)
    _rewrite_url_in_file(Path(finding.filepath), finding.url, web_path)
    finding.resolved = True
    finding.local_path = local_path
    finding.context = f"→ {web_path}"


def _fix_instruction(
    finding: Finding,
    ai_url: str,
    model: str,
) -> None:
    """Use the AI to rewrite the surrounding text block."""
    filepath = Path(finding.filepath)
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        finding.error = str(exc)
        return

    lines = content.splitlines()
    lineno = finding.line_no - 1  # 0-indexed

    # Collect the surrounding paragraph (up to 8 lines around the match)
    start = max(0, lineno - 3)
    end   = min(len(lines), lineno + 5)
    surrounding = "\n".join(lines[start:end])

    rewritten, err = _ai_rewrite_instruction(
        str(filepath), surrounding, finding.url, ai_url, model,
    )
    if err:
        finding.error = f"AI rewrite failed: {err}"
        return

    new_lines = lines[:start] + rewritten.splitlines() + lines[end:]
    filepath.write_text("\n".join(new_lines), encoding="utf-8")
    finding.resolved = True
    finding.context = f"AI-rewritten ({len(rewritten)} chars)"


def _rewrite_url_in_file(filepath: Path, old_url: str, new_path: str) -> None:
    """Replace old_url with new_path in filepath (all occurrences)."""
    content = filepath.read_text(encoding="utf-8", errors="replace")
    # Escape for literal replacement in the source
    new_content = content.replace(old_url, new_path)
    if new_content != content:
        filepath.write_text(new_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main scanner loop
# ---------------------------------------------------------------------------

def scan_dirs(
    dirs: list[Path],
    extensions: set[str],
    extra_excludes: set[str],
) -> Report:
    report = Report()
    exclude_dirs = _SKIP_DIRS | extra_excludes

    for root in dirs:
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded dirs in-place
            dirnames[:] = [
                d for d in dirnames if d not in exclude_dirs
            ]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                if fpath.suffix.lower() in extensions:
                    new_findings = _scan_file(fpath, report)
                    report.findings.extend(new_findings)

    return report


def fix_report(
    report: Report,
    static_root: Path,
    ai_url: str,
    model: str,
) -> None:
    for finding in report.findings:
        if finding.resolved:
            continue
        if finding.kind == "asset":
            _fix_asset(finding, static_root)
        elif finding.kind == "instruction":
            _fix_instruction(finding, ai_url, model)

        if finding.resolved:
            report.fixed += 1
        elif finding.error:
            report.errors += 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _print_report(report: Report, verbose: bool = False) -> None:
    total = len(report.findings)
    assets = sum(1 for f in report.findings if f.kind == "asset")
    instructions = sum(1 for f in report.findings if f.kind == "instruction")
    resolved = sum(1 for f in report.findings if f.resolved)
    errors = sum(1 for f in report.findings if f.error)

    print(f"\n{'─'*60}")
    print(f"  Airgap Audit Report")
    print(f"{'─'*60}")
    print(f"  Scanned files   : {report.scanned_files}")
    print(f"  Total findings  : {total}  (assets: {assets} | instructions: {instructions})")
    print(f"  Resolved        : {resolved}")
    print(f"  Errors          : {errors}")
    print(f"{'─'*60}")

    if not report.findings:
        print("  No external dependencies found.")
        return

    # Group by file
    by_file: dict[str, list[Finding]] = {}
    for f in report.findings:
        by_file.setdefault(f.filepath, []).append(f)

    for fpath, ffindings in sorted(by_file.items()):
        print(f"\n  {fpath}")
        for f in ffindings:
            status = "✓" if f.resolved else ("✗" if f.error else "·")
            tag = f"[{f.kind[:3].upper()}]"
            print(f"    {status} L{f.line_no:4d} {tag}  {f.url[:80]}")
            if f.error and verbose:
                print(f"           ERROR: {f.error}")
            if f.resolved and f.local_path and verbose:
                print(f"           → {f.local_path}")

    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scan source files for external CDN/font dependencies and localize them."
    )
    ap.add_argument("dirs", nargs="*", default=["."],
                    help="Directories to scan (default: current dir)")
    ap.add_argument("--fix", action="store_true",
                    help="Apply fixes in-place (download assets, AI-rewrite instructions)")
    ap.add_argument("--static-dir", default="admin_ui/static",
                    help="Root dir for downloaded static assets")
    ap.add_argument("--ai-url", default="http://localhost:8002/v1/chat/completions",
                    help="Local MoE chat-completions endpoint")
    ap.add_argument("--model", default="moe-orchestrator",
                    help="Model to use for AI instruction rewrites")
    ap.add_argument("--ext", default="html,md,py,js,css,yml",
                    help="Comma-separated file extensions to scan")
    ap.add_argument("--exclude", action="append", default=[],
                    help="Additional directory names to exclude (repeatable)")
    ap.add_argument("--report", metavar="FILE",
                    help="Write JSON report to FILE")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    extensions = {"." + e.lstrip(".") for e in args.ext.split(",")}
    dirs = [Path(d).resolve() for d in args.dirs]
    static_root = Path(args.static_dir).resolve()
    extra_excludes = set(args.exclude)

    print(f"Scanning {len(dirs)} director(y|ies)…")
    for d in dirs:
        print(f"  {d}")
    if args.fix:
        print(f"Mode    : FIX IN-PLACE")
        print(f"Static  : {static_root}")
        print(f"AI URL  : {args.ai_url}")
        print(f"Model   : {args.model}")
    else:
        print("Mode    : SCAN ONLY (use --fix to apply changes)")

    report = scan_dirs(dirs, extensions, extra_excludes)

    if args.fix and report.findings:
        print(f"\nFixing {len(report.findings)} finding(s)…")
        fix_report(report, static_root, args.ai_url, args.model)

    _print_report(report, verbose=args.verbose)

    if args.report:
        out = Path(args.report)
        out.write_text(
            json.dumps(
                {
                    "scanned_files": report.scanned_files,
                    "findings": [asdict(f) for f in report.findings],
                    "fixed":  report.fixed,
                    "errors": report.errors,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"JSON report written to: {out}")

    unresolved = sum(1 for f in report.findings if not f.resolved)
    return 1 if (unresolved and not args.fix) else (1 if report.errors else 0)


if __name__ == "__main__":
    sys.exit(main())

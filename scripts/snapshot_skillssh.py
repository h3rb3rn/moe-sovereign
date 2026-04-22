#!/usr/bin/env python3
"""
snapshot_skillssh.py — Download the complete skills.sh/audits dataset and
save a normalised JSON snapshot used by the MoE Admin skills-audit feature.

Usage:
    python3 scripts/snapshot_skillssh.py [--output PATH]

The script pages through GET https://skills.sh/api/audits/{page} until
hasMore is False, then writes a flat dict keyed by skillId to the output
file (default: /app/skills/community/.skillssh_audits.json).

Run manually to refresh the snapshot, or let the admin-backend endpoint
POST /api/skills/community/refresh-external-audits call this logic.
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import httpx
    _CLIENT = "httpx"
except ImportError:
    import urllib.request as _urllib
    _CLIENT = "urllib"

DEFAULT_OUTPUT = Path("/app/skills/community/.skillssh_audits.json")
BASE_URL = "https://skills.sh"
INITIAL_URL = f"{BASE_URL}/audits"
PAGE_URL = f"{BASE_URL}/api/audits"
HEADERS = {"User-Agent": "MoE-Sovereign/1.0 (skills-audit-snapshot)"}
RATE_LIMIT_DELAY = 0.4   # seconds between requests (polite scraping)
MAX_PAGES = 30            # hard cap against infinite loops


def _extract_initial_page(html: str) -> list[dict]:
    """Pull the initialRows batch out of the RSC payload embedded in the HTML."""
    import re

    audits: list[dict] = []
    skill_id_re = re.compile(r'\\"skillId\\":\\"([a-z0-9][a-z0-9_-]+)\\"')
    gen_re      = re.compile(r'\\"agentTrustHub\\".*?\\"gemini_analysis\\".*?\\"verdict\\":\\"([A-Z_]+)\\"', re.DOTALL)
    socket_re   = re.compile(r'\\"alertCount\\":(\d+)')
    snyk_re     = re.compile(r'\\"overall_risk_level\\":\\"([A-Z]+)\\"')

    for block in re.split(r'\\"rank\\":\d+,', html)[1:]:
        m_id = skill_id_re.search(block)
        if not m_id:
            continue
        m_gen   = gen_re.search(block)
        m_sock  = socket_re.search(block)
        m_snyk  = snyk_re.search(block)
        audits.append({
            "skillId":       m_id.group(1),
            "gen_verdict":   m_gen.group(1)       if m_gen  else None,
            "socket_alerts": int(m_sock.group(1)) if m_sock else None,
            "snyk_risk":     m_snyk.group(1)      if m_snyk else None,
        })
    return audits


def _normalise(entry: dict) -> dict:
    """Convert a raw /api/audits JSON entry to the flat cache format."""
    ath    = entry.get("agentTrustHub") or {}
    socket = entry.get("socket") or {}
    snyk   = entry.get("snyk") or {}

    gen_verdict = (
        (ath.get("result") or {}).get("gemini_analysis", {}).get("verdict")
    )
    socket_alerts = (
        (socket.get("result") or {}).get("alertCount")
    )
    snyk_risk = (
        (snyk.get("result") or {}).get("overall_risk_level")
    )
    return {
        "gen_verdict":   gen_verdict,
        "socket_alerts": int(socket_alerts) if socket_alerts is not None else None,
        "snyk_risk":     snyk_risk,
    }


def _get(url: str) -> dict | str:
    if _CLIENT == "httpx":
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=HEADERS) as c:
            r = c.get(url)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.json() if "json" in ct else r.text
    else:
        req = _urllib.Request(url, headers=HEADERS)
        with _urllib.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            ct = resp.headers.get("content-type", "")
            return json.loads(body) if "json" in ct else body


def run(output: Path) -> dict:
    audits: dict[str, dict] = {}
    seen: set[str] = set()

    # Page 0 is embedded in the SSR HTML — fetch it via the main URL
    print(f"[0] Fetching initial RSC page from {INITIAL_URL} …", flush=True)
    html = _get(INITIAL_URL)
    if not isinstance(html, str):
        print("  WARNING: unexpected JSON response for initial page", file=sys.stderr)
        html = ""

    initial = _extract_initial_page(html)
    for entry in initial:
        skill_id = entry.pop("skillId")
        if skill_id not in seen:
            audits[skill_id] = entry
            seen.add(skill_id)
    print(f"  → {len(initial)} skills from RSC payload ({len(audits)} unique so far)")

    # Pages 1 … N from the JSON API
    page = 1
    while page <= MAX_PAGES:
        url = f"{PAGE_URL}/{page}"
        print(f"[{page}] GET {url} …", end=" ", flush=True)
        time.sleep(RATE_LIMIT_DELAY)
        try:
            data = _get(url)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            break

        if not isinstance(data, dict):
            print("unexpected response type, stopping.")
            break

        skills = data.get("skills", [])
        has_more = data.get("hasMore", False)
        new = 0
        for entry in skills:
            skill_id = entry.get("skillId") or entry.get("name", "")
            if not skill_id or skill_id in seen:
                continue
            audits[skill_id] = _normalise(entry)
            seen.add(skill_id)
            new += 1
        print(f"{new} new ({len(audits)} total)", flush=True)

        if not has_more:
            print("  → hasMore=False, done.")
            break
        page += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audits, indent=2, ensure_ascii=False))
    print(f"\nSnapshot saved → {output} ({len(audits)} skills)")
    return audits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Output JSON path (default: %(default)s)")
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()

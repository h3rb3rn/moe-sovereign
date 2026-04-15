"""
inject_results_into_docs.py — Post-benchmark documentation updater.

Reads eval_*.json result files, computes per-category scores, and updates:
  - docs/system/benchmarks.md          (April 2026 campaign table)
  - /home/philipp/whitepaper/de/sections/14_evaluation_and_lessons.tex
  - /home/philipp/whitepaper/en/sections/14_evaluation_and_lessons.tex

Usage:
    python3 benchmarks/inject_results_into_docs.py [--run-dir <path>]

The script auto-detects the latest parallel_run_* directory if --run-dir is
not specified.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from statistics import mean
from typing import Optional

SCRIPT_DIR  = pathlib.Path(__file__).parent
RESULTS_DIR = SCRIPT_DIR / "results"
DOCS_BENCH  = SCRIPT_DIR.parent / "docs" / "system" / "benchmarks.md"

WP_DE = pathlib.Path("/home/philipp/whitepaper/de/sections/14_evaluation_and_lessons.tex")
WP_EN = pathlib.Path("/home/philipp/whitepaper/en/sections/14_evaluation_and_lessons.tex")

# Category groups for the summary table
CATEGORIES = {
    "precision":    ["precision-mcp-subnet", "precision-mcp-math", "precision-mcp-date"],
    "compounding":  ["compounding-memory-3turn", "compounding-memory-5turn"],
    "routing":      ["routing-legal", "routing-medical", "routing-code-review"],
    "multi_expert": ["multi-expert-synthesis"],
}

# Display order for templates (short name → full template name)
TEMPLATE_DISPLAY = {
    "moe-reference-30b-balanced": "ref-30b",
    "moe-benchmark-n04-rtx":      "n04-rtx",
    "moe-benchmark-n07-n09":      "n07-n09",
    "moe-benchmark-n06-m10":      "n06-m10",
    "moe-benchmark-n11-m10":      "n11-m10",
}


def _find_latest_run_dir() -> Optional[pathlib.Path]:
    """Find the most recent parallel_run_* directory in results/."""
    dirs = sorted(RESULTS_DIR.glob("parallel_run_*"), reverse=True)
    return dirs[0] if dirs else None


def _load_eval(run_dir: pathlib.Path, template: str) -> Optional[dict]:
    """Load eval JSON for a given template from the run directory."""
    # eval files are written by evaluator.py using MOE_EVAL_RESULTS=latest_<tmpl>.json
    # Look in results/ for eval_*.json whose source_run matches this template
    for f in sorted(RESULTS_DIR.glob("eval_*.json"), reverse=True):
        try:
            d = json.loads(f.read_text())
            src = d.get("source_run", "")
            if template in src:
                return d
        except Exception:
            continue
    # Also check the run_dir's eval.log for embedded data (fallback: parse latest_<tmpl>.json
    # if evaluator hasn't run yet — evaluator patches results in place)
    latest = RESULTS_DIR / f"latest_{template}.json"
    if latest.exists():
        try:
            d = json.loads(latest.read_text())
            # Check if scores are already embedded
            for r in d.get("results", []):
                if r.get("score") is not None:
                    return {"results": d["results"]}
        except Exception:
            pass
    return None


def compute_scores(eval_data: dict) -> dict:
    """Return per-test and per-category scores from an eval result dict."""
    scores: dict[str, float] = {}
    for r in eval_data.get("results", []):
        tid = r.get("test_id", "")
        s   = r.get("score")
        if s is not None:
            scores[tid] = float(s)
    cat_scores: dict[str, float] = {}
    for cat, tests in CATEGORIES.items():
        vals = [scores[t] for t in tests if t in scores]
        if vals:
            cat_scores[cat] = round(mean(vals), 1)
    overall = round(mean(scores.values()), 1) if scores else None
    return {"tests": scores, "categories": cat_scores, "overall": overall}


def _fmt(v: Optional[float]) -> str:
    return f"{v:.1f}" if v is not None else "—"


def build_markdown_table(all_scores: dict[str, dict]) -> str:
    """Build the markdown score summary table for benchmarks.md."""
    rows = []
    header = "| Template | Precision | Compounding | Routing | Multi-Expert | **Average** |"
    sep    = "|---|---|---|---|---|---|"
    rows.append(header)
    rows.append(sep)
    for tmpl, short in TEMPLATE_DISPLAY.items():
        s = all_scores.get(tmpl)
        if s is None:
            row = f"| `{short}` | — | — | — | — | — |"
        else:
            row = (f"| `{short}` "
                   f"| {_fmt(s['categories'].get('precision'))} "
                   f"| {_fmt(s['categories'].get('compounding'))} "
                   f"| {_fmt(s['categories'].get('routing'))} "
                   f"| {_fmt(s['categories'].get('multi_expert'))} "
                   f"| **{_fmt(s['overall'])}** |")
        rows.append(row)
    return "\n".join(rows)


def build_per_test_table(all_scores: dict[str, dict]) -> str:
    """Build the per-test detail markdown table for benchmarks.md."""
    all_tests = [t for tests in CATEGORIES.values() for t in tests]
    short_names = list(TEMPLATE_DISPLAY.values())
    header = "| Test ID | Category | " + " | ".join(short_names) + " |"
    sep    = "|---|---|" + "|---|" * len(short_names)
    rows   = [header, sep]
    cat_map = {t: c for c, tests in CATEGORIES.items() for t in tests}
    for test_id in all_tests:
        cat = cat_map.get(test_id, "")
        vals = []
        for tmpl in TEMPLATE_DISPLAY:
            s = all_scores.get(tmpl, {}).get("tests", {}).get(test_id)
            vals.append(_fmt(s))
        rows.append(f"| {test_id} | {cat} | " + " | ".join(vals) + " |")
    return "\n".join(rows)


def update_benchmarks_md(all_scores: dict[str, dict]) -> None:
    """Update the placeholder tables in docs/system/benchmarks.md."""
    text = DOCS_BENCH.read_text()

    # Replace Score Summary table
    summary_table = build_markdown_table(all_scores)
    text = re.sub(
        r"(\| Template \| Precision \| Compounding \| Routing \| Multi-Expert \| \*\*Average\*\* \|.*?)(\n\n|\Z)",
        summary_table + "\n\n",
        text,
        flags=re.DOTALL,
    )

    # Replace Per-Test Detail table
    detail_table = build_per_test_table(all_scores)
    text = re.sub(
        r"(\| Test ID \| Category \|.*?)(\n\n|\Z)",
        detail_table + "\n\n",
        text,
        flags=re.DOTALL,
    )

    # Update Comparison table (before/after)
    ref_scores = all_scores.get("moe-reference-30b-balanced", {})
    def _patch_comparison(text: str) -> str:
        comp3 = _fmt(ref_scores.get("tests", {}).get("compounding-memory-3turn"))
        comp5 = _fmt(ref_scores.get("tests", {}).get("compounding-memory-5turn"))
        prec  = _fmt(ref_scores.get("categories", {}).get("precision"))
        avg   = _fmt(ref_scores.get("overall"))
        # Replace each — in the "15 April" column with the actual value
        text = re.sub(r"(compounding-memory-3turn \| 8\.2 \| )—", rf"\g<1>{comp3}", text)
        text = re.sub(r"(compounding-memory-5turn \| 0\.6 \| )—", rf"\g<1>{comp5}", text)
        text = re.sub(r"(Avg\.?\\s*precision\s+\| 8\.3 \| )—", rf"\g<1>{prec}", text)
        text = re.sub(r"(Ø\\s*Gesamt\s+\| 6\.8 \| )—",          rf"\g<1>{avg}", text)
        text = re.sub(r"(Overall avg\s+\| 6\.8 \| )—",           rf"\g<1>{avg}", text)
        # Update Delta column
        try:
            d3   = f"+{round(float(comp3) - 8.2, 1)}" if comp3 != "—" else "—"
            d5   = f"+{round(float(comp5) - 0.6, 1)}" if comp5 != "—" else "—"
            dpr  = f"+{round(float(prec)  - 8.3, 1)}" if prec  != "—" else "—"
            davg = f"+{round(float(avg)   - 6.8, 1)}" if avg   != "—" else "—"
        except ValueError:
            d3 = d5 = dpr = davg = "—"
        text = re.sub(r"(compounding-memory-3turn.*?\| )— \|",    rf"\g<1>{d3} |",  text)
        text = re.sub(r"(compounding-memory-5turn.*?\| )— \|",    rf"\g<1>{d5} |",  text)
        return text

    text = _patch_comparison(text)
    DOCS_BENCH.write_text(text)
    print(f"Updated: {DOCS_BENCH}")


def update_whitepaper_tex(wp_path: pathlib.Path, all_scores: dict[str, dict],
                           lang: str = "de") -> None:
    """Update placeholder '--' values in the LaTeX whitepaper dense-graph table."""
    text = wp_path.read_text()
    for tmpl, short in TEMPLATE_DISPLAY.items():
        s = all_scores.get(tmpl)
        if s is None:
            continue
        prec  = _fmt(s["categories"].get("precision"))
        comp  = _fmt(s["categories"].get("compounding"))
        rout  = _fmt(s["categories"].get("routing"))
        me    = _fmt(s["categories"].get("multi_expert"))
        avg   = _fmt(s["overall"])
        # Replace the row:  \code{short}  & -- & -- & -- & -- & -- \\
        pattern = rf"(\\code\{{{re.escape(short)}\}})\s*&\s*--\s*&\s*--\s*&\s*--\s*&\s*--\s*&\s*--"
        replacement = rf"\1 & {prec} & {comp} & {rout} & {me} & {avg}"
        text = re.sub(pattern, replacement, text)

    # Update comparison table in LaTeX (ref-30b values)
    ref_scores = all_scores.get("moe-reference-30b-balanced", {})
    comp3 = _fmt(ref_scores.get("tests", {}).get("compounding-memory-3turn"))
    comp5 = _fmt(ref_scores.get("tests", {}).get("compounding-memory-5turn"))
    avg   = _fmt(ref_scores.get("overall"))
    text = re.sub(r"(compounding-3turn.*?& 8\.2 &\s*)--",  rf"\g<1>{comp3}", text)
    text = re.sub(r"(compounding-5turn.*?& 0\.6 &\s*)--",  rf"\g<1>{comp5}", text)
    if lang == "de":
        text = re.sub(r"(\\O~Gesamt\s*&\s*6\.8\s*&\s*)--", rf"\g<1>{avg}", text)
    else:
        text = re.sub(r"(Overall avg\s*&\s*6\.8\s*&\s*)--", rf"\g<1>{avg}", text)
    wp_path.write_text(text)
    print(f"Updated: {wp_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inject benchmark results into documentation")
    parser.add_argument("--run-dir", type=pathlib.Path, default=None,
                        help="Path to parallel_run_* directory (auto-detected if omitted)")
    args = parser.parse_args()

    run_dir = args.run_dir or _find_latest_run_dir()
    if not run_dir:
        print("ERROR: No parallel_run_* directory found in results/", file=sys.stderr)
        return 1

    print(f"Run directory: {run_dir}")

    all_scores: dict[str, dict] = {}
    for tmpl in TEMPLATE_DISPLAY:
        ed = _load_eval(run_dir, tmpl)
        if ed:
            all_scores[tmpl] = compute_scores(ed)
            print(f"  {tmpl}: avg={_fmt(all_scores[tmpl]['overall'])}")
        else:
            print(f"  {tmpl}: no eval data found")

    if not all_scores:
        print("No eval data available — run evaluator.py first.", file=sys.stderr)
        return 1

    update_benchmarks_md(all_scores)
    if WP_DE.exists():
        update_whitepaper_tex(WP_DE, all_scores, lang="de")
    if WP_EN.exists():
        update_whitepaper_tex(WP_EN, all_scores, lang="en")

    print("Documentation updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

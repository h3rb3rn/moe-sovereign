"""
comparator.py -- MoE Benchmark Quality Gate

Compares two benchmark result files (baseline vs. candidate template) and
produces a structured Go/No-Go recommendation before a model update is
promoted to production.

Metrics computed:
  - Overall score delta (baseline vs. candidate)
  - Per-category score deltas
  - Regression list: tests that passed on baseline but failed on candidate
  - Token efficiency delta (tokens per correct answer)
  - Latency p50/p95 delta (from score_details if present)
  - Go/No-Go verdict: PROMOTE | HOLD | ROLLBACK

Verdict rules (configurable via env):
  PROMOTE  — candidate >= baseline - SCORE_REGRESSION_TOLERANCE AND no hard regressions
  HOLD     — candidate < baseline but no catastrophic drop
  ROLLBACK — candidate drops > SCORE_ROLLBACK_THRESHOLD or critical category regressions

Configuration via environment variables:
  BASELINE_FILE             Path to baseline results JSON  (required)
  CANDIDATE_FILE            Path to candidate results JSON (required)
  SCORE_REGRESSION_TOLERANCE  Allowed score drop (default 0.02 = 2%)
  SCORE_ROLLBACK_THRESHOLD    Hard rollback threshold (default 0.10 = 10%)
  CRITICAL_CATEGORIES         Comma-separated categories where regressions block promotion
                              (default: precision,routing)
  OUTPUT_FORMAT               "markdown" or "json" (default: markdown)

Usage:
  BASELINE_FILE=results/latest_baseline.json \\
  CANDIDATE_FILE=results/latest_candidate.json \\
  python benchmarks/comparator.py

  # Pipe markdown report into a file:
  python benchmarks/comparator.py > report.md
"""

from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASELINE_FILE   = os.environ.get("BASELINE_FILE", "")
CANDIDATE_FILE  = os.environ.get("CANDIDATE_FILE", "")
RESULTS_DIR     = pathlib.Path(__file__).parent / "results"

SCORE_REGRESSION_TOLERANCE = float(os.environ.get("SCORE_REGRESSION_TOLERANCE", "0.02"))
SCORE_ROLLBACK_THRESHOLD   = float(os.environ.get("SCORE_ROLLBACK_THRESHOLD",   "0.10"))
CRITICAL_CATEGORIES        = {
    c.strip() for c in os.environ.get("CRITICAL_CATEGORIES", "precision,routing").split(",") if c.strip()
}
OUTPUT_FORMAT = os.environ.get("OUTPUT_FORMAT", "markdown")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class TestResult:
    test_id:   str
    test_name: str
    category:  str
    score:     float
    passed:    bool
    tokens:    int
    latency_ms: Optional[float]

    @staticmethod
    def from_dict(d: dict) -> "TestResult":
        score_details = d.get("score_details") or {}
        # score_details may contain 'llm_score' (0-10) or 'correct' (bool)
        raw_score = d.get("score", 0.0)
        if isinstance(raw_score, bool):
            raw_score = 1.0 if raw_score else 0.0
        score = float(raw_score)
        passed = score >= 0.5
        tokens = 0
        latency_ms = None
        if isinstance(score_details, dict):
            tokens     = int(score_details.get("total_tokens", 0))
            latency_ms = score_details.get("latency_ms") or score_details.get("wall_clock_ms")
        return TestResult(
            test_id=d.get("test_id", ""),
            test_name=d.get("test_name", d.get("test_id", "")),
            category=d.get("category", "unknown"),
            score=score,
            passed=passed,
            tokens=tokens,
            latency_ms=float(latency_ms) if latency_ms is not None else None,
        )


@dataclass
class RunSummary:
    template:   str
    timestamp:  str
    results:    list[TestResult]

    @property
    def overall_score(self) -> float:
        if not self.results:
            return 0.0
        return statistics.mean(r.score for r in self.results)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def score_by_category(self) -> dict[str, float]:
        cats: dict[str, list[float]] = {}
        for r in self.results:
            cats.setdefault(r.category, []).append(r.score)
        return {cat: statistics.mean(scores) for cat, scores in cats.items()}

    def token_efficiency(self) -> Optional[float]:
        """Average tokens per test. Lower = more efficient."""
        valid = [r.tokens for r in self.results if r.tokens > 0]
        return statistics.mean(valid) if valid else None

    def latency_percentiles(self) -> dict[str, Optional[float]]:
        valid = sorted(r.latency_ms for r in self.results if r.latency_ms is not None)
        if not valid:
            return {"p50": None, "p95": None}
        p50_idx = int(len(valid) * 0.50)
        p95_idx = int(len(valid) * 0.95)
        return {"p50": valid[p50_idx], "p95": valid[min(p95_idx, len(valid) - 1)]}

    def by_id(self) -> dict[str, TestResult]:
        return {r.test_id: r for r in self.results}


def load_run(path: str | pathlib.Path) -> RunSummary:
    p = pathlib.Path(path)
    if not p.exists():
        print(f"ERROR: File not found: {p}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(p.read_text(encoding="utf-8"))
    results = [TestResult.from_dict(r) for r in data.get("results", [])]
    return RunSummary(
        template=data.get("template", str(p.stem)),
        timestamp=data.get("timestamp", ""),
        results=results,
    )


# --------------------------------------------------------------------------
# Comparison engine
# --------------------------------------------------------------------------

@dataclass
class ComparisonReport:
    baseline:  RunSummary
    candidate: RunSummary

    # Computed fields
    score_delta:    float = 0.0
    pass_rate_delta: float = 0.0
    regressions:    list[TestResult] = field(default_factory=list)
    improvements:   list[TestResult] = field(default_factory=list)
    category_deltas: dict[str, float] = field(default_factory=dict)
    token_delta:    Optional[float] = None
    latency_p50_delta: Optional[float] = None
    latency_p95_delta: Optional[float] = None
    verdict:        str = "HOLD"
    verdict_reason: str = ""

    def compute(self) -> None:
        self.score_delta     = self.candidate.overall_score - self.baseline.overall_score
        self.pass_rate_delta = self.candidate.pass_rate     - self.baseline.pass_rate

        # Per-category score deltas
        base_cats = self.baseline.score_by_category()
        cand_cats = self.candidate.score_by_category()
        for cat in set(base_cats) | set(cand_cats):
            b = base_cats.get(cat, 0.0)
            c = cand_cats.get(cat, 0.0)
            self.category_deltas[cat] = c - b

        # Regressions and improvements
        base_by_id = self.baseline.by_id()
        cand_by_id = self.candidate.by_id()
        for test_id, base_r in base_by_id.items():
            cand_r = cand_by_id.get(test_id)
            if cand_r is None:
                continue
            if base_r.passed and not cand_r.passed:
                self.regressions.append(cand_r)
            elif not base_r.passed and cand_r.passed:
                self.improvements.append(cand_r)

        # Token and latency deltas
        b_tok = self.baseline.token_efficiency()
        c_tok = self.candidate.token_efficiency()
        if b_tok is not None and c_tok is not None:
            self.token_delta = c_tok - b_tok

        b_lat = self.baseline.latency_percentiles()
        c_lat = self.candidate.latency_percentiles()
        if b_lat["p50"] and c_lat["p50"]:
            self.latency_p50_delta = c_lat["p50"] - b_lat["p50"]
        if b_lat["p95"] and c_lat["p95"]:
            self.latency_p95_delta = c_lat["p95"] - b_lat["p95"]

        # Verdict
        self._decide_verdict()

    def _decide_verdict(self) -> None:
        critical_regressions = [r for r in self.regressions if r.category in CRITICAL_CATEGORIES]
        catastrophic_drop = self.score_delta < -SCORE_ROLLBACK_THRESHOLD

        if catastrophic_drop:
            self.verdict = "ROLLBACK"
            self.verdict_reason = (
                f"Score dropped {self.score_delta:.1%} — exceeds rollback threshold "
                f"({SCORE_ROLLBACK_THRESHOLD:.1%})"
            )
            return

        if critical_regressions:
            self.verdict = "ROLLBACK"
            cats = ", ".join(sorted({r.category for r in critical_regressions}))
            self.verdict_reason = (
                f"{len(critical_regressions)} regression(s) in critical categories: {cats}"
            )
            return

        if self.score_delta < -SCORE_REGRESSION_TOLERANCE:
            self.verdict = "HOLD"
            self.verdict_reason = (
                f"Score dropped {self.score_delta:.1%} — exceeds tolerance "
                f"({SCORE_REGRESSION_TOLERANCE:.1%}). No critical regressions."
            )
            return

        self.verdict = "PROMOTE"
        improve_txt = ""
        if self.improvements:
            improve_txt = f" +{len(self.improvements)} improvement(s)."
        self.verdict_reason = (
            f"Score delta {self.score_delta:+.1%} within tolerance. "
            f"{len(self.regressions)} regression(s).{improve_txt}"
        )


# --------------------------------------------------------------------------
# Report renderers
# --------------------------------------------------------------------------

_VERDICT_EMOJI = {"PROMOTE": "✅", "HOLD": "⚠️", "ROLLBACK": "🚫"}


def render_markdown(report: ComparisonReport) -> str:
    b = report.baseline
    c = report.candidate
    lat_b = b.latency_percentiles()
    lat_c = c.latency_percentiles()
    tok_b = b.token_efficiency()
    tok_c = c.token_efficiency()
    emoji = _VERDICT_EMOJI.get(report.verdict, "")

    def fmt_delta(v: Optional[float], fmt: str = "+.2%", suffix: str = "") -> str:
        if v is None:
            return "n/a"
        return f"{v:{fmt}}{suffix}"

    lines = [
        "# MoE Benchmark Quality Gate Report",
        "",
        f"**Baseline:**  `{b.template}` ({b.timestamp})",
        f"**Candidate:** `{c.template}` ({c.timestamp})",
        "",
        f"## Verdict: {emoji} {report.verdict}",
        "",
        f"> {report.verdict_reason}",
        "",
        "## Score Summary",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "|--------|----------|-----------|-------|",
        f"| Overall Score | {b.overall_score:.3f} | {c.overall_score:.3f} | {fmt_delta(report.score_delta)} |",
        f"| Pass Rate | {b.pass_rate:.1%} | {c.pass_rate:.1%} | {fmt_delta(report.pass_rate_delta, '+.1%')} |",
        f"| Avg Tokens | {tok_b or 'n/a'} | {tok_c or 'n/a'} | {fmt_delta(report.token_delta, '+.0f', ' tok')} |",
        f"| Latency p50 (ms) | {lat_b['p50'] or 'n/a'} | {lat_c['p50'] or 'n/a'} | {fmt_delta(report.latency_p50_delta, '+.0f', 'ms')} |",
        f"| Latency p95 (ms) | {lat_b['p95'] or 'n/a'} | {lat_c['p95'] or 'n/a'} | {fmt_delta(report.latency_p95_delta, '+.0f', 'ms')} |",
        "",
        "## Category Deltas",
        "",
        "| Category | Delta |",
        "|----------|-------|",
    ]
    for cat, delta in sorted(report.category_deltas.items(), key=lambda x: x[1]):
        flag = " ⚠️" if cat in CRITICAL_CATEGORIES and delta < 0 else ""
        lines.append(f"| {cat} | {delta:+.3f}{flag} |")

    lines += [
        "",
        f"## Regressions ({len(report.regressions)})",
        "",
    ]
    if report.regressions:
        lines += [
            "| Test ID | Name | Category |",
            "|---------|------|----------|",
        ]
        for r in sorted(report.regressions, key=lambda x: x.category):
            crit_flag = " **[CRITICAL]**" if r.category in CRITICAL_CATEGORIES else ""
            lines.append(f"| `{r.test_id}` | {r.test_name} | {r.category}{crit_flag} |")
    else:
        lines.append("_None_")

    lines += [
        "",
        f"## Improvements ({len(report.improvements)})",
        "",
    ]
    if report.improvements:
        lines += [
            "| Test ID | Name | Category |",
            "|---------|------|----------|",
        ]
        for r in sorted(report.improvements, key=lambda x: x.category):
            lines.append(f"| `{r.test_id}` | {r.test_name} | {r.category} |")
    else:
        lines.append("_None_")

    lines += [
        "",
        "---",
        "_Generated by `benchmarks/comparator.py`_",
    ]
    return "\n".join(lines)


def render_json(report: ComparisonReport) -> str:
    return json.dumps({
        "verdict":       report.verdict,
        "verdict_reason": report.verdict_reason,
        "baseline":      report.baseline.template,
        "candidate":     report.candidate.template,
        "score_delta":   report.score_delta,
        "pass_rate_delta": report.pass_rate_delta,
        "regressions": [
            {"test_id": r.test_id, "category": r.category}
            for r in report.regressions
        ],
        "improvements": [
            {"test_id": r.test_id, "category": r.category}
            for r in report.improvements
        ],
        "category_deltas": report.category_deltas,
        "token_delta":      report.token_delta,
        "latency_p50_delta": report.latency_p50_delta,
        "latency_p95_delta": report.latency_p95_delta,
    }, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def compare(baseline_path: str, candidate_path: str) -> ComparisonReport:
    """Loads, compares, and returns a completed ComparisonReport."""
    baseline  = load_run(baseline_path)
    candidate = load_run(candidate_path)
    report = ComparisonReport(baseline=baseline, candidate=candidate)
    report.compute()
    return report


def main() -> None:
    if not BASELINE_FILE or not CANDIDATE_FILE:
        print(
            "Usage: BASELINE_FILE=<path> CANDIDATE_FILE=<path> python comparator.py",
            file=sys.stderr,
        )
        # Auto-detect: if two arguments are passed on the command line, use them
        if len(sys.argv) == 3:
            baseline_path  = sys.argv[1]
            candidate_path = sys.argv[2]
        else:
            sys.exit(1)
    else:
        baseline_path  = BASELINE_FILE
        candidate_path = CANDIDATE_FILE

    report = compare(baseline_path, candidate_path)

    if OUTPUT_FORMAT == "json":
        print(render_json(report))
    else:
        print(render_markdown(report))

    # Exit code encodes verdict for CI/CD pipelines
    exit_codes = {"PROMOTE": 0, "HOLD": 1, "ROLLBACK": 2}
    sys.exit(exit_codes.get(report.verdict, 1))


if __name__ == "__main__":
    main()

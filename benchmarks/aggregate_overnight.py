"""Aggregate overnight benchmark results across epochs into a comparison report.

Usage:
    python3 benchmarks/aggregate_overnight.py --run-dir benchmarks/results/overnight_YYYYMMDD-HHMMSS
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def _extract_scalar(prom_response: dict) -> float:
    """Extract a single scalar from a Prometheus instant query response."""
    try:
        results = prom_response.get("data", {}).get("result", [])
        if results:
            return float(results[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    return 0.0


def _extract_vec(prom_response: dict) -> dict:
    """Extract label→value dict from a Prometheus vector response."""
    out = {}
    try:
        for r in prom_response.get("data", {}).get("result", []):
            labels = r.get("metric", {})
            key = next(iter(labels.values()), "total") if labels else "total"
            out[key] = float(r["value"][1])
    except (KeyError, TypeError):
        pass
    return out


def load_snapshot(path: Path) -> dict:
    """Load a pre/post epoch metrics snapshot."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    metrics = data.get("metrics", {})
    flat = {}
    for query, response in metrics.items():
        if isinstance(response, dict) and "data" in response:
            flat[query] = response
    return flat


def load_eval(path: Path) -> dict:
    """Load an evaluator result JSON."""
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load_results(path: Path) -> dict:
    """Load a runner result JSON."""
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def compute_epoch(epoch: int, run_dir: Path) -> dict:
    """Compute metrics for a single epoch."""
    pre = load_snapshot(run_dir / f"pre_epoch_{epoch}.json")
    post = load_snapshot(run_dir / f"post_epoch_{epoch}.json")
    eval_data = load_eval(run_dir / f"epoch_{epoch}_eval.json")
    results = load_results(run_dir / f"epoch_{epoch}_results.json")

    # Scores from evaluator
    scores_by_cat = {}
    test_scores = {}
    if eval_data and "results" in eval_data:
        for r in eval_data["results"]:
            cat = r.get("category", "unknown")
            score = r.get("combined_score", r.get("det_score", 0))
            scores_by_cat.setdefault(cat, []).append(score)
            test_scores[r.get("test_id", "")] = score

    avg_by_cat = {c: round(sum(s) / len(s), 2) for c, s in scores_by_cat.items()} if scores_by_cat else {}
    all_scores = [s for ss in scores_by_cat.values() for s in ss]
    avg_score = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0

    # Token usage from results
    total_tokens = 0
    total_duration = 0
    if results and "results" in results:
        for r in results["results"]:
            for turn in r.get("turns", []):
                total_tokens += turn.get("prompt_tokens", 0) + turn.get("completion_tokens", 0)
                total_duration += turn.get("wall_clock_s", 0)

    # Metrics deltas (post - pre)
    def delta(metric_key):
        pre_v = _extract_scalar(pre.get(metric_key, {}))
        post_v = _extract_scalar(post.get(metric_key, {}))
        return round(post_v - pre_v, 1)

    def post_val(metric_key):
        return _extract_scalar(post.get(metric_key, {}))

    # Cache hit rate
    hits = post_val("sum(increase(moe_cache_hits_total[90d]))")
    misses = post_val("sum(increase(moe_cache_misses_total[90d]))")
    cache_hit_rate = round(hits / (hits + misses), 4) if (hits + misses) > 0 else 0

    # Confidence distribution
    conf = _extract_vec(post.get("sum by (level) (increase(moe_expert_confidence_total[90d]))", {}))

    return {
        "epoch": epoch,
        "scores": avg_by_cat,
        "test_scores": test_scores,
        "avg_score": avg_score,
        "total_tokens": total_tokens,
        "total_duration_s": round(total_duration, 1),
        "graph_entities": post_val("moe_graph_entities_total"),
        "graph_relations": post_val("moe_graph_relations_total"),
        "graph_entities_delta": delta("moe_graph_entities_total"),
        "graph_relations_delta": delta("moe_graph_relations_total"),
        "synthesis_nodes": post_val("moe_graph_synthesis_nodes_total"),
        "ontology_gaps": post_val("moe_ontology_gaps_total"),
        "cache_hit_rate": cache_hit_rate,
        "compressions": post_val("sum(increase(moe_history_compressed_total[90d]))"),
        "unlimited_history": post_val("sum(increase(moe_history_unlimited_total[90d]))"),
        "corrections_stored": post_val("sum(increase(moe_corrections_stored_total[90d]))"),
        "corrections_injected": post_val("sum(increase(moe_corrections_injected_total[90d]))"),
        "judge_refinements": post_val("sum(increase(moe_judge_refinement_total[90d]))"),
        "expert_failures": post_val("sum(increase(moe_expert_failures_total[90d]))"),
        "synthesis_persisted": post_val("sum(increase(moe_synthesis_persisted_total[90d]))"),
        "linting_runs": post_val("sum(increase(moe_linting_runs_total[90d]))"),
        "confidence": conf,
        "response_p50": post_val("histogram_quantile(0.50, rate(moe_response_duration_seconds_bucket[1h]))"),
        "response_p95": post_val("histogram_quantile(0.95, rate(moe_response_duration_seconds_bucket[1h]))"),
    }


def generate_report_md(report: dict, path: Path) -> None:
    """Write a human-readable Markdown report."""
    epochs = report["per_epoch"]
    lines = [
        f"# Overnight Intelligence Benchmark Report",
        f"",
        f"**Template:** {report['template']}  ",
        f"**Epochs:** {report['epochs']}  ",
        f"**Started:** {report['started_at']}  ",
        f"**Finished:** {report['finished_at']}  ",
        f"**Dataset:** moe_eval_overnight_v1.json (12 tests)  ",
        f"",
        f"## Epoch-by-Epoch Summary",
        f"",
        f"| Epoch | Avg Score | Tokens | Duration | Entities | Relations | Cache Hit | Compressions | Corrections | Failures |",
        f"|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in epochs:
        lines.append(
            f"| {e['epoch']} | **{e['avg_score']}** | {e['total_tokens']:,} "
            f"| {e['total_duration_s']:.0f}s | {e['graph_entities']:.0f} (+{e['graph_entities_delta']:.0f}) "
            f"| {e['graph_relations']:.0f} (+{e['graph_relations_delta']:.0f}) "
            f"| {e['cache_hit_rate']*100:.1f}% | {e['compressions']:.0f} "
            f"| {e['corrections_stored']:.0f} stored / {e['corrections_injected']:.0f} injected "
            f"| {e['expert_failures']:.0f} |"
        )

    lines += [
        "",
        "## Score Trends by Category",
        "",
    ]
    # Collect all categories
    cats = set()
    for e in epochs:
        cats.update(e["scores"].keys())
    cats = sorted(cats)

    header = "| Epoch | " + " | ".join(cats) + " |"
    sep = "|---|" + "|".join(["---"] * len(cats)) + "|"
    lines.append(header)
    lines.append(sep)
    for e in epochs:
        row = f"| {e['epoch']} | " + " | ".join(
            str(e["scores"].get(c, "–")) for c in cats
        ) + " |"
        lines.append(row)

    lines += [
        "",
        "## Intelligence Subsystem Metrics",
        "",
        "| Metric | Epoch 1 | Epoch 5 | Epoch 10 | Trend |",
        "|---|---|---|---|---|",
    ]

    def ep(n):
        return next((e for e in epochs if e["epoch"] == n), {})

    e1, e5, e10 = ep(1), ep(5), ep(10)
    if e1 and e10:
        def trend(v1, v10):
            if v1 == 0 and v10 == 0:
                return "–"
            if v1 == 0:
                return "↑ new"
            delta_pct = ((v10 - v1) / v1) * 100 if v1 != 0 else 0
            return f"{'↑' if delta_pct > 0 else '↓'} {abs(delta_pct):.0f}%"

        metrics = [
            ("Graph Entities", "graph_entities"),
            ("Graph Relations", "graph_relations"),
            ("Synthesis Nodes", "synthesis_nodes"),
            ("Cache Hit Rate", "cache_hit_rate"),
            ("Compressions", "compressions"),
            ("Corrections Stored", "corrections_stored"),
            ("Corrections Injected", "corrections_injected"),
            ("Judge Refinements", "judge_refinements"),
            ("Expert Failures", "expert_failures"),
            ("Ontology Gaps", "ontology_gaps"),
            ("Linting Runs", "linting_runs"),
            ("Response P50", "response_p50"),
        ]
        for label, key in metrics:
            v1 = e1.get(key, 0)
            v5 = e5.get(key, 0) if e5 else "–"
            v10 = e10.get(key, 0)
            fmt = lambda v: f"{v*100:.1f}%" if key == "cache_hit_rate" else (f"{v:.1f}s" if "response" in key else f"{v:,.0f}" if isinstance(v, (int, float)) else str(v))
            lines.append(f"| {label} | {fmt(v1)} | {fmt(v5)} | {fmt(v10)} | {trend(v1, v10)} |")

    lines += [
        "",
        "## Key Findings",
        "",
        "*(Auto-generated — review and annotate manually)*",
        "",
    ]

    # Auto-detect patterns
    if epochs:
        first_score = epochs[0].get("avg_score", 0)
        last_score = epochs[-1].get("avg_score", 0)
        if last_score > first_score * 1.1:
            lines.append(f"- **Score improved** from {first_score} to {last_score} (+{((last_score-first_score)/first_score*100):.0f}%)")
        elif last_score < first_score * 0.9:
            lines.append(f"- **Score degraded** from {first_score} to {last_score} — investigate compression loss or instability")
        else:
            lines.append(f"- **Score stable** ({first_score} → {last_score})")

        if epochs[-1].get("expert_failures", 0) > 0:
            lines.append(f"- **{epochs[-1]['expert_failures']:.0f} expert failures** detected — check stability")
        else:
            lines.append(f"- **Zero expert failures** — system stable across all epochs")

        first_entities = epochs[0].get("graph_entities", 0)
        last_entities = epochs[-1].get("graph_entities", 0)
        if last_entities > first_entities:
            lines.append(f"- **Graph grew** from {first_entities:.0f} to {last_entities:.0f} entities (+{last_entities-first_entities:.0f})")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written: {path}")


def main():
    parser = argparse.ArgumentParser(description="Aggregate overnight benchmark epochs")
    parser.add_argument("--run-dir", required=True, help="Path to overnight run directory")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"ERROR: {run_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Detect epochs
    epoch_files = sorted(run_dir.glob("epoch_*_results.json"))
    epoch_nums = []
    for f in epoch_files:
        try:
            n = int(f.stem.split("_")[1])
            epoch_nums.append(n)
        except (IndexError, ValueError):
            pass

    if not epoch_nums:
        print("No epoch result files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(epoch_nums)} epochs: {epoch_nums}")

    per_epoch = []
    for n in epoch_nums:
        print(f"  Processing epoch {n}...")
        per_epoch.append(compute_epoch(n, run_dir))

    report = {
        "template": per_epoch[0].get("test_scores", {}).get("template", "moe-m10-gremium-deep"),
        "epochs": len(epoch_nums),
        "started_at": datetime.now().isoformat(),
        "finished_at": datetime.now().isoformat(),
        "per_epoch": per_epoch,
    }

    # Write JSON
    json_path = run_dir / "overnight_report.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"JSON report: {json_path}")

    # Write Markdown
    md_path = run_dir / "overnight_report.md"
    generate_report_md(report, md_path)


if __name__ == "__main__":
    main()

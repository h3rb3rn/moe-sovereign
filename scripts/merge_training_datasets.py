#!/usr/bin/env python3
"""scripts/merge_training_datasets.py — merges multiple per-role training
data sources (LUMI-G-generated role_sft, OpenRouter-generated role_sft,
Loom-sandbox-verified coder examples) into ONE final, shuffled dataset per
role, ready to upload to LUMI-G scratch under the
`dataset_expert_${ROLE}_*.jsonl` naming convention that
slurm/lumig_expert_ensemble_pipeline.slurm's dataset auto-discovery expects
(`ls "${DATASET_DIR}/dataset_expert_${ROLE}_"*.jsonl | head -n1`).

Deliberately a single combined-then-shuffled-then-trained-once dataset per
role, not per-source sequential fine-tuning passes -- see the
"Nachträgliches Finetunen" discussion in this project's LUMI-G
full-finetuning plan: training on multiple data sources sequentially risks
catastrophic forgetting and doubles Stage 1-3 GPU-hours for no quality
benefit over one combined pass.

Every input file is expected to contain one {"text": "<ChatML>"} JSON object
per line -- the exact format both scripts/generate_diverse_training_seeds.py
(--mode role_sft, LUMI-G) and scripts/generate_role_sft_openrouter.py
produce, and the format scripts/curate_coder_expert_dataset.py's Loom
output already uses. A malformed line or one missing "text" is skipped and
logged, never silently dropped without a trace and never fabricated.

Usage:
    python3 scripts/merge_training_datasets.py --role coder \\
        --input datasets/role_sft_coder_glm45air.jsonl \\
        --input datasets/role_sft_openrouter/coder_kimi_k3.jsonl \\
        --input datasets/coder_expert_memory_ordering_sft.jsonl \\
        --output datasets/merged/dataset_expert_coder_merged.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import List, Tuple


def load_and_validate(path: str) -> Tuple[List[str], int, int]:
    """Reads one input JSONL file. Returns (valid "text" strings, line
    count, malformed/skipped count). Never raises on a single bad line --
    a bad line is logged to stderr and skipped, matching this project's
    "don't fabricate, don't abort the whole batch over one bad record"
    convention (see e.g. scripts/generate_loom_seed_examples.py's per-pair
    error handling).
    """
    texts: List[str] = []
    total = 0
    skipped = 0
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [{path}:{line_no}] SKIPPED -- malformed JSON: {e}", file=sys.stderr)
                skipped += 1
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("text"), str) or not obj["text"].strip():
                print(f"  [{path}:{line_no}] SKIPPED -- missing/empty \"text\" field "
                      f"(wrong format for this pipeline?)", file=sys.stderr)
                skipped += 1
                continue
            texts.append(obj["text"])
    return texts, total, skipped


def merge(input_paths: List[str], seed: int) -> Tuple[List[str], dict]:
    """Loads every input file, deduplicates on exact text match (guards
    against accidentally re-running the same generation twice into two
    different source files), shuffles with the given seed for
    reproducibility, and returns (final texts, per-source stats dict).
    """
    stats = {"per_source": {}, "total_raw": 0, "total_skipped": 0, "duplicates_removed": 0}
    seen: set = set()
    combined: List[str] = []
    for path in input_paths:
        if not Path(path).exists():
            print(f"WARNING: input file does not exist, skipping entirely: {path}", file=sys.stderr)
            stats["per_source"][path] = {"raw": 0, "skipped": 0, "kept": 0, "error": "file not found"}
            continue
        texts, total, skipped = load_and_validate(path)
        kept = 0
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).hexdigest()
            if h in seen:
                stats["duplicates_removed"] += 1
                continue
            seen.add(h)
            combined.append(t)
            kept += 1
        stats["per_source"][path] = {"raw": total, "skipped": skipped, "kept": kept}
        stats["total_raw"] += total
        stats["total_skipped"] += skipped

    rng = random.Random(seed)
    rng.shuffle(combined)
    stats["final_count"] = len(combined)
    return combined, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--role", required=True, help="role name, only used for the printed summary and default output naming")
    parser.add_argument("--input", action="append", required=True, dest="inputs",
                         help="one input JSONL file; repeat --input for each source. A missing file is "
                              "warned about and skipped, not a fatal error (a source that hasn't finished "
                              "generating yet shouldn't block merging what already exists).")
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=42, help="shuffle seed, for reproducible merges")
    parser.add_argument("--min-count", type=int, default=1,
                         help="abort (nonzero exit) if the final merged count is below this -- a safety "
                              "net against silently shipping a near-empty dataset as if it were complete")
    args = parser.parse_args()

    combined, stats = merge(args.inputs, args.seed)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for text in combined:
            f.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")

    print(f"=== Merge summary for role={args.role} ===")
    for path, s in stats["per_source"].items():
        if "error" in s:
            print(f"  {path}: {s['error']}")
        else:
            print(f"  {path}: {s['kept']}/{s['raw']} kept ({s['skipped']} skipped)")
    print(f"  Duplicates removed (exact text match across sources): {stats['duplicates_removed']}")
    print(f"  Total raw lines across all sources: {stats['total_raw']}")
    print(f"  Final merged + shuffled count: {stats['final_count']} -> {output_path}")

    if stats["final_count"] < args.min_count:
        print(f"ERROR: final count {stats['final_count']} is below --min-count {args.min_count} -- "
              f"aborting rather than uploading a near-empty dataset as if it were complete.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""scripts/curate_coder_expert_dataset.py — curate data/loom_training_examples.jsonl
into an SFT dataset for the coder expert (moe-expert-coder-4b), targeting
LUMI-G post-training Candidate 1 (acquire-release memory-ordering reasoning,
docs/experiments/lumig_posttraining_candidates.md).

Each output example teaches the *correction* pattern the merger retry loop
already exercises live in production (graph/synthesis.py, ~line 1246-1252):
given a prior attempt's real Loom concurrency-violation diagnostic (or none,
for a first-try success), produce a corrected/correct Rust `loom::model`
test. The target completion is always the actual sandbox-verified `passed:
true` source from data/loom_training_examples.jsonl -- never a fabricated
"correct" answer.

Status/labeling note (AGENTS.md SS9): the system/user instruction text below
is a generic, synthetic task description (not lifted from a real user
prompt or benchmark task) -- it exists to give the model a consistent frame
for the assistant-turn completion. Label any dataset produced by this
script as "planned/research SFT material", not as validated production
training data, until it has gone through an actual LUMI-G training +
evaluation pass.

Output format: one JSON object per line, single field "text" containing a
fully-rendered ChatML conversation (system + user + assistant), matching
the "text"/dataset_text_field="text" contract of
scripts/train_expert_slm_pipeline.py (SFTTrainer trains on this string
as-is -- no template is applied at training time, so it must already be
rendered here).

Usage:
    python3 scripts/curate_coder_expert_dataset.py \\
        --input data/loom_training_examples.jsonl \\
        --output datasets/coder_expert_memory_ordering_sft.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SYSTEM_PROMPT = (
    "You are moe-expert-coder-4b, the MoE Sovereign systems-programming expert "
    "(category: code_reviewer). You write correct, idiomatic Rust, with careful "
    "attention to concurrency correctness: atomic memory ordering, ownership, "
    "and lifetimes."
)

_USER_TASK = (
    "Write a Rust `#[test]` function that uses the `loom` concurrency-model "
    "crate (`loom::model(...)`) to verify a producer thread publishing a value "
    "to a consumer thread through `loom::sync::atomic` variables. The test "
    "must assert the consumer never observes a stale/uninitialized payload, "
    "and it must actually pass under Loom's interleaving exploration -- not "
    "just compile."
)

# Mirrors graph/synthesis.py's own merger-retry injection text verbatim
# (~line 1246-1252) so this dataset trains the exact correction pattern the
# live retry loop already exercises, not a paraphrase of it.
_RETRY_CONTEXT_TEMPLATE = (
    "\n\nYour previous answer's Rust code compiles but Loom found a "
    "concurrency/memory-ordering violation when actually running it. "
    "Fix the synchronization and provide a corrected, complete answer:\n{output_tail}"
)

_OUTPUT_TAIL_MAX_CHARS = 1500  # matches graph/synthesis.py's own truncation


def _group_by_request_id(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["request_id"]].append(record)
    return groups


def _select_target_and_context(
    records: List[Dict[str, Any]],
) -> Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    """Pick the training target (first sandbox-verified `passed: true` record)
    and, if one exists, the immediately-preceding failed attempt in the same
    group to use as correction context.

    Returns None when the group has no passed:true record at all -- there is
    then no correct target to train on, and this script never trains on a
    bare negative example without a corrected answer to pair it with.
    """
    ordered = sorted(records, key=lambda r: r.get("attempt", 0))
    target = next((r for r in ordered if r.get("passed") is True), None)
    if target is None:
        return None

    prior_failures = [
        r for r in ordered
        if r.get("passed") is False and r.get("attempt", 0) < target.get("attempt", 0)
    ]
    context = max(prior_failures, key=lambda r: r.get("attempt", 0)) if prior_failures else None
    return target, context


def _render_chatml(system: str, user: str, assistant: str) -> str:
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant}<|im_end|>\n"
    )


def build_training_example(target: Dict[str, Any], context: Optional[Dict[str, Any]]) -> str:
    user = _USER_TASK
    if context is not None:
        output_tail = (context.get("output_tail") or "")[-_OUTPUT_TAIL_MAX_CHARS:]
        user += _RETRY_CONTEXT_TEMPLATE.format(output_tail=output_tail)

    assistant = f"```rust\n{target['source']}\n```"
    return _render_chatml(_SYSTEM_PROMPT, user, assistant)


def curate(records: List[Dict[str, Any]]) -> List[str]:
    """Returns the list of rendered "text" strings, one per request_id group
    that has a valid (sandbox-verified passing) target."""
    texts: List[str] = []
    for _request_id, group in _group_by_request_id(records).items():
        selection = _select_target_and_context(group)
        if selection is None:
            continue
        target, context = selection
        texts.append(build_training_example(target, context))
    return texts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="data/loom_training_examples.jsonl")
    parser.add_argument("--output", default="datasets/coder_expert_memory_ordering_sft.jsonl")
    args = parser.parse_args()

    input_path = Path(args.input)
    records = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    texts = curate(records)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for text in texts:
            f.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")

    print(f"Wrote {len(texts)} training examples from {len(records)} raw records "
          f"({len(_group_by_request_id(records))} request_id groups) -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

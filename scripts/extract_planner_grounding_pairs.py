#!/usr/bin/env python3
"""scripts/extract_planner_grounding_pairs.py — curate a planner SFT dataset
against LUMI-G post-training Candidate 2 (planner task fabrication,
docs/experiments/lumig_posttraining_candidates.md): the planner
(moe-sovereign-student:4b) repeatedly invents thematically unrelated tasks
(observed bias: network/security/compliance topics) instead of grounding
its plan in the actual request.

Data-provenance note: the candidate document's own fabrication instances
are timestamped against production conversation logs
(services/conversation_log.py, per-user JSONL under CONVERSATION_LOG_DIR).
Mining those logs for the literal historical (fabricated) plan JSON was
deliberately NOT done here -- it requires touching another user's audit
trail / raw production traffic outside this script's narrow, reviewable
scope. Instead, every example in this dataset is built from two safe,
already-public sources only:
  1. The scientific benchmark's own task prompts
     (benchmarks/datasets/sovereign_scientific_benchmark_v1.json) -- not
     sensitive, already part of this repo.
  2. A short, hand-written adversarial prompt list that reproduces the
     documented fabrication *triggers* (trivial pings/arithmetic/small talk
     -- see Candidate 2's "ping"/"What is 2+2?" observations) without
     needing any production log lookup at all.

For every prompt, the assistant target is a single, deliberately minimal,
topically-grounded plan (a JSON array matching the planner's own output
contract, graph/planner.py) -- built deterministically from the prompt/task
metadata itself, never from an LLM call. This directly targets the
*grounding* failure (plans about a topic absent from the prompt), not GAP 3
Candidate 5's separate under-decomposition problem, which is intentionally
out of scope here (see docs/experiments/lumig_posttraining_candidates.md
and the "Nicht im Scope" section of this plan).

Status/labeling note (AGENTS.md SS9): this is planned/research SFT material,
not validated training data -- it has not gone through a LUMI-G training +
evaluation pass. The system message used here is a short placeholder, not
the full production planner prompt (graph/planner.py's _planner_role is
tens of KB and would make every example inconsistent/wasteful to include).

Output format: one JSON object per line, field "messages"
([{"role": ..., "content": ...}, ...]), matching train_planner_sft.py's
expected dataset shape.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_SYSTEM_PROMPT = (
    "You are the MoE Sovereign planner. Decompose the user's request into a JSON "
    "array of task objects. Each object has \"task\" (string) and \"category\" "
    "(string). Never invent a task about a topic absent from the request."
)

# benchmark category -> planner category vocabulary (config.py EXPERT_MODELS
# keys / the "precision_tools" pseudo-category). See graph/expert.py and
# .env's EXPERT_MODELS for the authoritative live mapping.
_CATEGORY_MAP = {
    "systems_programming": "code_reviewer",
    "compounding_knowledge": "compounding_knowledge",
    "precision": "precision_tools",
    "reasoning": "general",
    "governance": "governance",
}

# Reproduces the exact documented fabrication triggers (Candidate 2: "ping"
# and "What is 2+2?" -> fake DNS/HTTP/gRPC tasks) plus a few structurally
# similar trivial prompts, each paired with a correct minimal grounded plan.
# The Docker example is lifted verbatim from graph/planner.py's own few-shot
# prompt (a safe, already-in-repo, non-sensitive source).
_ADVERSARIAL_EXAMPLES: List[Dict[str, Any]] = [
    {"prompt": "ping", "category": "general", "task": "Respond to the connectivity check"},
    {"prompt": "What is 2+2?", "category": "precision_tools", "task": "Calculate 2+2",
     "mcp_tool": "calculate", "mcp_args": {"expression": "2+2"}},
    {"prompt": "What is 10*10?", "category": "precision_tools", "task": "Calculate 10*10",
     "mcp_tool": "calculate", "mcp_args": {"expression": "10*10"}},
    {"prompt": "Hello, are you there?", "category": "general", "task": "Greet the user and confirm availability"},
    {"prompt": "What is Docker?", "category": "general",
     "task": "Explain what Docker is and what it is used for"},
    {"prompt": "What is the capital of France?", "category": "general", "task": "State the capital of France"},
    {"prompt": "Convert 5 miles to kilometers", "category": "precision_tools",
     "task": "Convert 5 miles to kilometers", "mcp_tool": "unit_convert",
     "mcp_args": {"value": 5, "from_unit": "miles", "to_unit": "kilometers"}},
    {"prompt": "What is 15% of 200?", "category": "precision_tools", "task": "Calculate 15% of 200",
     "mcp_tool": "calculate", "mcp_args": {"expression": "0.15*200"}},
    {"prompt": "Say hi", "category": "general", "task": "Greet the user briefly"},
    {"prompt": "thanks", "category": "general", "task": "Acknowledge the user's thanks"},
]

_WHITESPACE_RE = re.compile(r"\s+")


def grounded_task_description(prompt: str, max_chars: int = 160) -> str:
    """Deterministically derives a short, topically-faithful task restatement
    from the prompt itself -- never an LLM call, never an unrelated topic.
    This *is* the grounding constraint: the task description can only ever
    contain words already present in the request.
    """
    first_line = _WHITESPACE_RE.sub(" ", prompt.strip().splitlines()[0]).strip()
    truncated = first_line[:max_chars].rstrip()
    if len(first_line) > max_chars:
        truncated += "..."
    return f"Address the following request: {truncated}"


def build_plan_for_benchmark_task(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    category = _CATEGORY_MAP.get(task["category"], "general")
    return [{"task": grounded_task_description(task["prompt"]), "category": category}]


def build_plan_for_adversarial_example(example: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan_task: Dict[str, Any] = {"task": example["task"], "category": example["category"]}
    if "mcp_tool" in example:
        plan_task["mcp_tool"] = example["mcp_tool"]
        plan_task["mcp_args"] = example["mcp_args"]
    return [plan_task]


def render_messages(prompt: str, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
        ]
    }


def _normalize_prompt(task: Dict[str, Any]) -> Dict[str, Any]:
    """multi_turn tasks (type == "multi_turn") carry a "turns" list instead
    of a top-level "prompt" -- concatenate each turn's prompt text so every
    task exposes the same "prompt" field the rest of this script relies on.
    Returns a shallow copy; never mutates the loaded dataset in place.
    """
    if "prompt" in task:
        return task
    turns = task.get("turns", [])
    normalized = dict(task)
    normalized["prompt"] = "\n".join(t.get("prompt", "") for t in turns)
    return normalized


def load_benchmark_tasks(dataset_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    tasks = [_normalize_prompt(t) for t in data.get("test_cases", [])]
    missing_category = [t["id"] for t in tasks if t.get("category") not in _CATEGORY_MAP]
    if missing_category:
        raise ValueError(
            f"Benchmark task(s) with unmapped category, add to _CATEGORY_MAP first: {missing_category}"
        )
    return tasks


def curate(benchmark_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    examples = []
    for task in benchmark_tasks:
        plan = build_plan_for_benchmark_task(task)
        examples.append(render_messages(task["prompt"], plan))
    for example in _ADVERSARIAL_EXAMPLES:
        plan = build_plan_for_adversarial_example(example)
        examples.append(render_messages(example["prompt"], plan))
    return examples


# --- Candidate 4: verschachteltes/escaptes JSON bei struktureller Persistierung ---
#
# graph/planner.py's own prompt already states the intended fix for this
# exact failure mode (the "KNOWLEDGE STORAGE / MEMORY REQUESTS" rule): a
# multi-entity/multi-rule persistence request must become a single flat
# task with a plain-prose restatement, never a task field re-encoding the
# structure as (nested/escaped) JSON. Both of this benchmark's multi_turn
# tasks (sci-graphrag-01-topology-cascade, sci-graphrag-02-paraconsistent-
# reconciliation) are exactly this pattern -- see docs/experiments/
# lumig_posttraining_candidates.md Candidate 4's two observations.
_PERSISTENCE_ADVERSARIAL_EXAMPLES: List[Dict[str, str]] = [
    {
        "prompt": (
            "Register the following team roster in the knowledge graph: "
            "Alice is the on-call lead for the payments service; Bob owns the "
            "billing database migration; Carol reviews all security-sensitive "
            "pull requests."
        ),
        "category": "compounding_knowledge",
    },
    {
        "prompt": (
            "Persist this policy update: Directive 7-A requires quarterly "
            "access reviews for all admin roles. Directive 7-B, effective "
            "immediately, extends 7-A to service accounts as well."
        ),
        "category": "governance",
    },
]


def build_plan_for_persistence_task(prompt: str, category: str) -> List[Dict[str, Any]]:
    """The flat-JSON-safe target for a multi-entity/multi-rule persistence
    request: one task, plain prose, matching graph/planner.py's own
    "KNOWLEDGE STORAGE / MEMORY REQUESTS" rule -- never a nested/escaped
    JSON re-encoding of the structure, which is exactly Candidate 4's
    observed failure mode.
    """
    restated = _WHITESPACE_RE.sub(" ", prompt.strip())
    return [{"task": f"Acknowledge the following information and confirm it is noted: {restated}",
             "category": category}]


def curate_json_structure(benchmark_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    examples = []
    for task in benchmark_tasks:
        if task.get("type") != "multi_turn":
            continue
        category = _CATEGORY_MAP.get(task["category"], "general")
        plan = build_plan_for_persistence_task(task["prompt"], category)
        examples.append(render_messages(task["prompt"], plan))
    for example in _PERSISTENCE_ADVERSARIAL_EXAMPLES:
        plan = build_plan_for_persistence_task(example["prompt"], example["category"])
        examples.append(render_messages(example["prompt"], plan))
    return examples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--benchmark-dataset", default="benchmarks/datasets/sovereign_scientific_benchmark_v1.json")
    parser.add_argument("--output", default=None, help="default depends on --mode")
    parser.add_argument("--mode", choices=["grounding", "json_structure"], default="grounding",
                         help="grounding: Candidate 2 (task fabrication). "
                              "json_structure: Candidate 4 (nested/escaped JSON on multi-entity persistence).")
    args = parser.parse_args()

    benchmark_tasks = load_benchmark_tasks(Path(args.benchmark_dataset))
    if args.mode == "grounding":
        examples = curate(benchmark_tasks)
        adversarial_count = len(_ADVERSARIAL_EXAMPLES)
        source_count = len(benchmark_tasks)
        default_output = "datasets/planner_grounding_sft.jsonl"
    else:
        examples = curate_json_structure(benchmark_tasks)
        adversarial_count = len(_PERSISTENCE_ADVERSARIAL_EXAMPLES)
        source_count = sum(1 for t in benchmark_tasks if t.get("type") == "multi_turn")
        default_output = "datasets/planner_json_structure_sft.jsonl"

    output_path = Path(args.output or default_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for example in examples:
            f.write(json.dumps(example, ensure_ascii=False) + "\n")

    print(f"Wrote {len(examples)} examples ({source_count} benchmark-task-derived + "
          f"{adversarial_count} adversarial) -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""scripts/generate_diverse_training_seeds.py — offline batch generation via
DeepSeek-V4-Flash (vLLM, LUMI-G only) to diversify the two LUMI-G
post-training datasets prepared for the benchmark-derived candidates in
docs/experiments/lumig_posttraining_candidates.md:

  --mode loom       Candidate 1 (memory-ordering): novel Rust producer/
                     consumer `loom::model` scenario pairs (broken + fixed),
                     beyond the 4 hand-written archetypes in
                     scripts/generate_loom_seed_examples.py.
  --mode grounding  Candidate 2 (planner task fabrication) / Candidate 4
                     (nested/escaped JSON on multi-entity persistence):
                     diverse, category-tagged example user requests, beyond
                     the hand-written examples in
                     scripts/extract_planner_grounding_pairs.py.
  --mode role_sft   Full-finetuning data generation (all 10 MoE Sovereign
                     roles: Planner, 8 Experts, Judge) for the LUMI-G
                     post-training plan in ~/.claude/plans/zazzy-beaming-
                     koala.md Phase 2 -- a teacher model, prompted with the
                     target role's own system prompt, invents a realistic
                     (user_request, ideal_assistant_response) pair in one
                     shot. Output is "text" (ChatML), the exact format
                     scripts/train_expert_slm_pipeline.py's
                     dataset_text_field="text" expects for ALL 10 roles
                     including judge -- confirmed by reading that script
                     directly: it has no per-role format branching, and
                     scripts/train_judge_lora.py (Alpaca instruction/input/
                     output format) is a separate, older script the current
                     production pipeline (slurm/lumig_expert_ensemble_
                     pipeline.slurm) does not actually call for judge.

Architecture note (why the teacher LLM only generates *raw material*, never
labels): DeepSeek-V4-Flash's job here is diversity of natural-language
input, not correctness grading. Ground truth for Candidate 1 still comes
exclusively from the real rust-loom-sandbox (see
scripts/generate_loom_seed_examples.py --llm-scenarios-file, which re-runs
every generated pair through the sandbox and keeps only sandbox-verified
determinate outcomes); ground truth for Candidate 2/4 still comes from the
deterministic, rule-based plan construction in
scripts/extract_planner_grounding_pairs.py --llm-requests-file (the category
is fixed by which prompt we asked for, never inferred from the model's own
output). This script's output is never used as a training target directly.

Model note: deepseek-ai/DeepSeek-V4-Flash was the first choice here but is a
confirmed dead end on this cluster's vLLM build -- its native blockwise-FP8
checkpoint (quant_method=fp8, fmt=e4m3) raises
"deepseek_v4_fp8 quantization is currently not supported in rocm" at engine
init (verified live, job 21676155). Default is now Qwen/Qwen3.5-35B-A3B: a
current (not the older Qwen2.5 line), unquantized MoE checkpoint (no
quantization_config in its config.json, so it loads as plain BF16, sidestepping
the whole class of exotic-quant-on-ROCm failures) that this project's own
slurm/lumig_job2_distillation.slurm already designates as the distillation
teacher -- reusing it here needs no new model-compatibility risk. Run
slurm/lumig_job5_enrichment_smoketest.slurm (tiny --count, single node)
BEFORE the full shard jobs to confirm the model loads and generates at all.

Usage (inside the LUMI-G Singularity container, one process per node):
    python3 scripts/generate_diverse_training_seeds.py --mode loom \\
        --count 100 --output "$OUT_DIR/loom_shard0.jsonl"
    python3 scripts/generate_diverse_training_seeds.py --mode grounding \\
        --count-per-category 125 --output "$OUT_DIR/grounding_shard0.jsonl"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Delimiter-based (not JSON) output format for anything that embeds
# multi-line source code in a field. Root-caused live (job 21798250,
# role_sft/coder smoke test, GLM-4.5-Air, full raw completions in
# role_sft_coder_smoketest.jsonl.debug.log): models reliably produce
# real, on-topic content but do NOT reliably JSON-escape multi-line code
# (raw newlines/quotes inside a JSON string value break json.loads() even
# though the content itself is fine) -- this is the actual root cause of
# the long-standing "--mode loom always parses to 0" bug, previously
# mis-attributed to --max-tokens being too small. JSON stays fine for
# --mode grounding, whose values are short one-line strings with no
# embedded code -- already empirically proven robust (24/24 twice).
_LOOM_GENERATION_PROMPT = """You are an expert in Rust concurrent systems programming and the `loom` concurrency-model-checking crate (loom = "0.7.2").

Design ONE new, original Rust producer/consumer or lock-free synchronization scenario, distinct from all of: a flag-guarded payload publish, a generation-counter handoff, a lazy-init multi-field config, and a single-slot SPSC ring buffer. Draw inspiration from ideas such as: a seqlock, double-checked initialization, a hazard-pointer retire, a work-stealing deque steal, an RCU-style snapshot publish, a ticket-lock handoff, or a once-cell double publish -- or invent your own.

Output EXACTLY in this plain-text format, with no other text before the first marker or after the last line of fixed source, and no markdown code fences around the markers themselves:
===SCENARIO_NAME===
<snake_case_name, one line>
===BROKEN_SOURCE===
<full Rust source, using loom::sync::atomic (AtomicUsize/AtomicBool)/Arc/thread, with exactly one #[test] fn that calls loom::model(...) and a deliberately too-weak Ordering::Relaxed on the publishing store and/or the consuming load, guarded by an assert_eq! that Loom's interleaving exploration will find a counterexample for>
===FIXED_SOURCE===
<the identical test with ONLY the ordering corrected -- Ordering::Release on the publish, Ordering::Acquire on the consuming load -- so the same assert_eq! now holds under every interleaving>
===END===

Hard constraints: edition 2021; do not include a [package]/Cargo.toml, only the lib.rs body; use ONLY loom::sync::atomic::{AtomicUsize, AtomicBool, Ordering}, loom::sync::Arc, loom::thread; never use std::process, std::fs, std::net, extern "C", #[link], or include!; exactly one #[test] fn per source; both broken_source and fixed_source must be complete, independently compilable Rust files (each with its own `use` statements). Write the raw Rust code directly after each marker -- do not wrap it in ```rust fences and do not JSON-escape it."""

_GROUNDING_CATEGORY_DESCRIPTIONS: Dict[str, str] = {
    "general": "small talk, greetings, trivial factual questions, or simple everyday questions with no need for a specialized tool or expert",
    "precision_tools": "exact calculations, unit conversions, date/time arithmetic, percentages, or other tasks requiring precise deterministic computation",
    "code_reviewer": "reviewing, writing, debugging, or explaining source code in any programming language",
    "compounding_knowledge": "storing, persisting, or registering a single structured fact or relationship for later retrieval",
    "governance": "compliance, policy, regulatory, or organizational-governance questions",
    "research": "requests needing current external information such as news, prices, or recent events",
    "security": "security review, vulnerability assessment, or defensive security guidance",
    "technical_support": "troubleshooting technical/IT problems or explaining how a system/tool works",
}

_PERSISTENCE_CATEGORY_DESCRIPTIONS: Dict[str, str] = {
    "compounding_knowledge": "storing multiple related entities or facts in ONE request (e.g. a team roster with several people and their roles, a system topology with several interdependent services, a multi-item inventory list)",
    "governance": "persisting a multi-clause policy, directive, or regulation update in ONE request, where a later clause amends or references an earlier one",
}

_GROUNDING_GENERATION_TEMPLATE = """Generate {n} diverse, realistic example user requests that a person might send to an AI assistant, all clearly and unambiguously belonging to this domain: {description}.

Vary the phrasing, length (from one short sentence to a few sentences), and specificity. Do NOT write requests that belong to a different domain.

Output STRICT JSON only: a JSON array of exactly {n} strings, no prose, no markdown code fences."""

# Copied deliberately, not imported, from the production sources of truth
# below -- this script runs standalone inside the LUMI-G Singularity
# container (no guarantee the repo root is on sys.path the way the app
# expects), matching the existing pattern in
# scripts/curate_coder_expert_dataset.py's own inlined _SYSTEM_PROMPT.
# Keep in sync by hand if the originals change:
#   prompts.py: DEFAULT_EXPERT_PROMPTS[<key>], DEFAULT_PLANNER_ROLE
#   services/inference.py: JUDGE_SYSTEM_PROMPT
_ROLE_SYSTEM_PROMPTS: Dict[str, str] = {
    "coder": (
        "You are a high-assurance systems-programming and code-synthesis expert (moe-expert-coder-4b) "
        "specialized in Rust, C++, Python, and Go. Produce precise, compiler-checked code and minimal "
        "atomic diffs. Uphold memory-safety invariants strictly — correct ownership, correct lock-free "
        "memory ordering (acquire/release pairing), no data races. Flag any construct you cannot verify "
        "as sound rather than guessing."
    ),
    "precision": (
        "You are a formal-reasoning, SMT constraint formulation, and numerical-precision expert "
        "(moe-expert-precision-4b). Decompose quantitative problems into explicit, tool-verifiable "
        "calculation steps and always ground exact numbers in a deterministic calculation tool rather "
        "than estimating in prose. Never guess a numeric result you can compute exactly; show your work "
        "and validate the output before presenting it."
    ),
    "graphrag": (
        "You are a knowledge-graph navigation and GraphRAG retrieval specialist (moe-expert-graphrag-4b). "
        "Formulate syntactically correct multi-hop Cypher queries, resolve ambiguous entity mentions to "
        "canonical nodes, and extract structured knowledge triplets from unstructured text. Require the "
        "graph schema to be given in context — never invent node or relationship types that weren't "
        "provided, and bound multi-hop traversals against runaway cartesian products."
    ),
    "governance": (
        "You are a regulatory policy reasoning and privacy-by-design expert (moe-expert-governance-4b) for "
        "GDPR, the EU AI Act, BSI IT-Grundschutz, ISO 27001, and HIPAA. Ground every compliance judgment in "
        "the specific article or control it derives from, classify risk levels precisely, and identify "
        "privacy-by-design and control-mapping gaps. Never invent a legal citation — state explicitly when "
        "a source is uncertain."
    ),
    "research": (
        "You are an evidence-grounded literature review, technical trade-off synthesis, and citation "
        "verification expert (moe-expert-research-4b). Ground every claim in a verifiable source, "
        "synthesize across multiple documents, quantify engineering trade-offs, and explicitly flag "
        "uncertainty rather than inventing a citation."
    ),
    "security": (
        "You are a cybersecurity, static vulnerability analysis, and hardening expert "
        "(moe-expert-security-4b). Identify memory-safety flaws, injection vectors, and SSRF/CWE-classified "
        "vulnerabilities with the exact CWE ID; scan for exposed secrets and credentials; build STRIDE-based "
        "threat models across trust boundaries; and produce concrete hardening manifests "
        "(seccomp, AppArmor, Kubernetes policies)."
    ),
    "datainfra": (
        "You are a database engineering, query optimization, and data-infrastructure expert "
        "(moe-expert-datainfra-4b) specialized in PostgreSQL, DuckDB, and ClickHouse. "
        "Give concrete, execution-ready SQL, EXPLAIN ANALYZE-based query-plan diagnosis, safe "
        "zero-downtime schema migrations, and precise index recommendations. Verify syntax before answering."
    ),
    "omni": (
        "You are the cross-domain synthesis and interface-harmonization expert (moe-expert-omni-4b). "
        "Reconcile and integrate outputs from other specialists (coder, security, data infrastructure, "
        "governance, precision, graph retrieval) into one coherent, consistent result. "
        "Do not attempt deep mathematical proofs, raw kernel/driver code, or standalone regulatory audits "
        "yourself — state explicitly when a dedicated specialist is actually required instead."
    ),
    "planner": (
        "You are the orchestrator of a Mixture-of-Experts system.\n"
        "Decompose the following request into 1–4 subtasks.\n\n"
        "Mandatorily extract all numerical constraints and technical parameters from the request "
        "(e.g. model sizes, MTU values, protocol overheads, chemical doses, bitrates). "
        "Integrate these as IMMUTABLE_CONSTANTS directly into each subtask description for the experts, "
        "so experts cannot hallucinate default values."
    ),
    "judge": (
        "You are Sovereign Judge 27B (Qwen3.8-27B fine-tuned), the primary "
        "evaluation and synthesis authority in the MoE Sovereign compound AI "
        "platform. Evaluate input quality, factual consistency, code invariants, "
        "and safety with maximum precision across up to 258,000 context tokens."
    ),
}

# Delimiter-based, not JSON -- see the comment above _LOOM_GENERATION_PROMPT:
# an assistant_response for a code-heavy role (coder/datainfra/security) is
# exactly the kind of multi-line, quote-and-backslash-heavy content models
# do not reliably JSON-escape (root-caused live, job 21798250).
_ROLE_SFT_GENERATION_TEMPLATE = """{system_prompt}

Invent ONE new, realistic training example for fine-tuning a smaller model to perform exactly this role. Write a specific, concrete user request that clearly falls within this domain (not a generic placeholder), then write the complete, ideal expert response you would give to it -- fully in character with the role above, showing real reasoning/work, not a stub.

Output EXACTLY in this plain-text format, with no other text before the first marker or after ===END===, and no markdown code fences around the markers themselves (code fences INSIDE the response text, e.g. for a code snippet, are fine):
===USER_REQUEST===
<a specific, realistic user message, one or more lines>
===ASSISTANT_RESPONSE===
<the complete, ideal expert response to it -- may be multiple paragraphs and include code blocks>
===END==="""

def _find_balanced_spans(text: str, open_ch: str, close_ch: str) -> List[str]:
    """Find every top-level, bracket-balanced `open_ch...close_ch` span in
    text, tracking string-literal state (with escape handling) so
    brackets/braces inside quoted strings never confuse depth counting.
    Unlike a single greedy regex (e.g. `\\[.*\\]` or `\\{.*\\}`), this does
    not collapse multiple JSON values -- or a real value plus unrelated
    preceding/following bracket/brace characters from model reasoning text
    -- into one unparseable blob. Returns candidate substrings in the order
    they appear in text. Used for both `[...]` arrays and `{...}` objects.
    """
    candidates: List[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != open_ch:
            i += 1
            continue
        depth = 0
        in_string = False
        escape = False
        j = i
        while j < n:
            c = text[j]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
            else:
                if c == '"':
                    in_string = True
                elif c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[i:j + 1])
                        break
            j += 1
        i = j + 1
    return candidates


def _find_bracket_balanced_arrays(text: str) -> List[str]:
    return _find_balanced_spans(text, "[", "]")


def _parse_delimited_fields(text: str, field_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract fields from a `===FIELD_NAME===\\n<content>` delimited
    completion. Used instead of JSON for anything that embeds multi-line
    source code: models reliably produce real, on-topic content but do NOT
    reliably JSON-escape raw newlines/quotes inside a string value --
    root-caused live in job 21798250 (role_sft/coder smoke test, GLM-4.5-Air:
    real Rust code generated, but malformed as JSON on every one of 5
    attempts). A plain marker scan needs no escaping at all.

    Returns None if any marker is missing or any extracted field is empty.
    Tolerates reasoning/preamble text before the first marker and markdown
    fences around the whole completion.

    Searches for each marker's LAST occurrence, working backward from the
    final field to the first (each earlier marker's search is bounded to
    end before the next field's found position, which also guarantees
    correct ordering without a separate check). This matters when a model
    botches its first attempt and restarts: root-caused live
    (deepseek/deepseek-v4-pro-0813, comparing --role research against
    z-ai/glm-5.3) -- one completion wrote a placeholder "..." attempt,
    visibly reconsidered in plain reasoning text, then produced a second,
    real attempt. Matching the FIRST occurrence of each marker (the
    original implementation) captured the placeholder block plus all the
    leftover reasoning text as one field's content; matching the LAST
    occurrence picks the model's final, presumably-corrected attempt.
    """
    positions: list = [None] * len(field_names)
    last_marker = f"==={field_names[-1]}==="
    idx = text.rfind(last_marker)
    if idx == -1:
        return None
    positions[-1] = (idx, field_names[-1], last_marker)
    search_end = idx
    for i in range(len(field_names) - 2, -1, -1):
        marker = f"==={field_names[i]}==="
        idx = text.rfind(marker, 0, search_end)
        if idx == -1:
            return None
        positions[i] = (idx, field_names[i], marker)
        search_end = idx

    end_idx = text.find("===END===", positions[-1][0])
    result: Dict[str, str] = {}
    for i, (idx, name, marker) in enumerate(positions):
        content_start = idx + len(marker)
        if i + 1 < len(positions):
            content_end = positions[i + 1][0]
        elif end_idx > content_start:
            content_end = end_idx
        else:
            content_end = len(text)
        content = text[content_start:content_end].strip()
        if not content:
            return None
        result[name] = content
    return result


# Minimum plausible length for a field that claims to be a full, compilable
# Rust source file or a real expert response -- guards against exactly the
# failure mode caught live in job 21798693's loom retest: one of 2
# completions parsed "successfully" with all three fields equal to the
# 4-character garbage string "`, `" (non-empty, so it passed the earlier
# bare-truthiness check, but obviously not real content). Loom's own
# downstream rust-loom-sandbox re-verification would eventually have caught
# this specific case (it won't compile), but role_sft has no such
# independent downstream check -- catching obviously-too-short garbage here,
# at the only point that has the raw completion, is cheaper and more
# reliable than hoping every consumer re-validates length itself.
_MIN_SOURCE_LEN = 50
_MIN_RESPONSE_LEN = 20


def parse_loom_output(text: str) -> Optional[Dict[str, str]]:
    """Best-effort extraction of the scenario_name/broken_source/fixed_source
    fields from a raw model completion. Returns None on any parse/shape
    failure -- an unparseable generation is simply discarded, never
    fabricated into a plausible-looking fallback.
    """
    fields = _parse_delimited_fields(text, ["SCENARIO_NAME", "BROKEN_SOURCE", "FIXED_SOURCE"])
    if fields is None:
        return None
    if len(fields["BROKEN_SOURCE"]) < _MIN_SOURCE_LEN or len(fields["FIXED_SOURCE"]) < _MIN_SOURCE_LEN:
        return None
    return {
        "scenario_name": fields["SCENARIO_NAME"],
        "broken_source": fields["BROKEN_SOURCE"],
        "fixed_source": fields["FIXED_SOURCE"],
    }


def parse_grounding_output(text: str, expected_count: int) -> List[str]:
    """Best-effort extraction of a JSON array of request strings. Returns
    whatever valid non-empty strings were found -- a partial or padded
    array from the model still yields usable examples, it just yields
    fewer than expected_count.

    Tries every bracket-balanced candidate span in the completion (a model
    that emits reasoning/preamble containing its own bracket characters
    before the real answer array previously broke the single-greedy-regex
    version: `\\[.*\\]` spans from the FIRST `[` to the LAST `]` in the
    whole text, swallowing everything in between into one string that fails
    json.loads() -- observed live with zai-org/GLM-4.5-Air, where 7/8
    grounding categories parsed to 0 despite real, on-topic generations).
    Keeps whichever candidate yields the most usable string items; ties
    favor the later candidate, since a genuine final answer more often
    follows reasoning than precedes it.
    """
    best: List[str] = []
    for candidate in _find_bracket_balanced_arrays(text):
        try:
            arr = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(arr, list):
            continue
        items = [item.strip() for item in arr if isinstance(item, str) and item.strip()][:expected_count]
        if len(items) >= len(best):
            best = items
    return best


def _load_llm(model: str, tensor_parallel_size: int, max_model_len: int, gpu_memory_utilization: float):
    # Imported lazily: vllm is a LUMI-G-only training/inference dependency,
    # not installed in the local dev environment this script is authored in.
    from vllm import LLM  # noqa: PLC0415

    return LLM(
        model=model,
        dtype="auto",  # let vLLM read quantization_config (native fp8) from the checkpoint itself
        tensor_parallel_size=tensor_parallel_size,
        trust_remote_code=True,
        max_model_len=max_model_len,
        gpu_memory_utilization=gpu_memory_utilization,
    )


def _log_parse_failure(debug_path: Path, raw_text: str) -> None:
    """Append a bounded excerpt of an unparseable raw completion to a debug
    sidecar file. Added after job 21794616 (role_sft/coder smoke test, GLM-
    4.5-Air) returned 0/5 parsed with no way to inspect why -- silent parse
    failures with no diagnostic trail are exactly the "looks done, wasn't
    verified" pattern this plan's own Phase 4 self-critique already flagged.
    Truncated to keep the sidecar file bounded even across many failures.
    """
    with open(debug_path, "a", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(raw_text[:4000] + ("... [truncated]" if len(raw_text) > 4000 else "") + "\n")


def run_loom_mode(args: argparse.Namespace) -> None:
    from vllm import SamplingParams  # noqa: PLC0415

    llm = _load_llm(args.model, args.tensor_parallel_size, args.max_model_len, args.gpu_memory_utilization)
    sampling = SamplingParams(temperature=0.9, top_p=0.95, max_tokens=args.max_tokens)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = output_path.with_suffix(output_path.suffix + ".debug.log")
    written = 0
    total = 0
    # Chunked generation (not one single args.count-wide call): flushes
    # progress to disk continuously so a SLURM timeout mid-run still leaves
    # every already-completed batch on disk, not just an all-or-nothing result.
    with open(output_path, "a", encoding="utf-8") as f:
        for start in range(0, args.count, args.batch_size):
            batch_n = min(args.batch_size, args.count - start)
            outputs = llm.generate([_LOOM_GENERATION_PROMPT] * batch_n, sampling)
            for out in outputs:
                total += 1
                raw_text = out.outputs[0].text
                parsed = parse_loom_output(raw_text)
                if parsed is not None:
                    f.write(json.dumps(parsed, ensure_ascii=False) + "\n")
                    written += 1
                else:
                    _log_parse_failure(debug_path, raw_text)
            f.flush()
            print(f"[loom] batch {start // args.batch_size + 1}: "
                  f"{written}/{total} parsed so far", flush=True)
    print(f"[loom] done: {written}/{total} scenario pairs parsed -> {output_path}")


def run_grounding_mode(args: argparse.Namespace) -> None:
    from vllm import SamplingParams  # noqa: PLC0415

    descriptions = _PERSISTENCE_CATEGORY_DESCRIPTIONS if args.persistence else _GROUNDING_CATEGORY_DESCRIPTIONS
    style = "persistence" if args.persistence else "grounding"

    llm = _load_llm(args.model, args.tensor_parallel_size, args.max_model_len, args.gpu_memory_utilization)
    sampling = SamplingParams(temperature=1.0, top_p=0.95, max_tokens=args.max_tokens)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = output_path.with_suffix(output_path.suffix + ".debug.log")
    written = 0
    with open(output_path, "a", encoding="utf-8") as f:
        for category, description in descriptions.items():
            remaining = args.count_per_category
            while remaining > 0:
                batch_n = min(args.batch_size, remaining)
                prompt = _GROUNDING_GENERATION_TEMPLATE.format(n=batch_n, description=description)
                outputs = llm.generate([prompt], sampling)
                raw_text = outputs[0].outputs[0].text
                requests = parse_grounding_output(raw_text, batch_n)
                for request_text in requests:
                    f.write(json.dumps({"category": category, "style": style, "prompt": request_text},
                                        ensure_ascii=False) + "\n")
                    written += 1
                f.flush()
                print(f"[grounding:{style}] {category}: got {len(requests)}/{batch_n} this batch "
                      f"({written} total so far)", flush=True)
                # A batch that yields nothing usable means the model isn't
                # cooperating with this prompt shape -- stop retrying rather
                # than spin forever on zero-progress batches.
                if not requests:
                    _log_parse_failure(debug_path, raw_text)
                    print(f"[grounding:{style}] {category}: batch produced 0 usable requests, "
                          f"moving to next category", flush=True)
                    break
                remaining -= len(requests)
    print(f"[grounding:{style}] done: {written} tagged requests -> {output_path}")


def parse_role_sft_output(text: str) -> Optional[Dict[str, str]]:
    """Best-effort extraction of the user_request/assistant_response fields
    from a raw model completion. Returns None on any parse/shape failure --
    an unparseable generation is simply discarded, never fabricated into a
    plausible-looking fallback.
    """
    fields = _parse_delimited_fields(text, ["USER_REQUEST", "ASSISTANT_RESPONSE"])
    if fields is None:
        return None
    if len(fields["ASSISTANT_RESPONSE"]) < _MIN_RESPONSE_LEN:
        return None
    return {
        "user_request": fields["USER_REQUEST"],
        "assistant_response": fields["ASSISTANT_RESPONSE"],
    }


def render_chatml(system: str, user: str, assistant: str) -> str:
    """Render a (system, user, assistant) triple as ChatML text -- the exact
    format scripts/train_expert_slm_pipeline.py's dataset_text_field="text"
    expects (mirrors scripts/curate_coder_expert_dataset.py's _render_chatml,
    duplicated rather than imported for the same standalone-container reason
    documented at _ROLE_SYSTEM_PROMPTS above).
    """
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant}<|im_end|>\n"
    )


def run_role_sft_mode(args: argparse.Namespace) -> None:
    from vllm import SamplingParams  # noqa: PLC0415

    system_prompt = _ROLE_SYSTEM_PROMPTS[args.role]
    llm = _load_llm(args.model, args.tensor_parallel_size, args.max_model_len, args.gpu_memory_utilization)
    sampling = SamplingParams(temperature=1.0, top_p=0.95, max_tokens=args.max_tokens)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = output_path.with_suffix(output_path.suffix + ".debug.log")
    written = 0
    total = 0
    prompt = _ROLE_SFT_GENERATION_TEMPLATE.format(system_prompt=system_prompt)
    with open(output_path, "a", encoding="utf-8") as f:
        for start in range(0, args.count, args.batch_size):
            batch_n = min(args.batch_size, args.count - start)
            outputs = llm.generate([prompt] * batch_n, sampling)
            for out in outputs:
                total += 1
                raw_text = out.outputs[0].text
                parsed = parse_role_sft_output(raw_text)
                if parsed is not None:
                    text = render_chatml(system_prompt, parsed["user_request"], parsed["assistant_response"])
                    f.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
                    written += 1
                else:
                    _log_parse_failure(debug_path, raw_text)
            f.flush()
            print(f"[role_sft:{args.role}] batch {start // args.batch_size + 1}: "
                  f"{written}/{total} parsed so far", flush=True)
    print(f"[role_sft:{args.role}] done: {written}/{total} training examples -> {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["loom", "grounding", "role_sft"], required=True)
    parser.add_argument("--model", default="Qwen/Qwen3.5-35B-A3B")
    parser.add_argument("--tensor-parallel-size", type=int, default=8)
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9, help="raise (e.g. 0.95-0.97) for tight-fit teacher models whose weights alone exceed vLLM's 90%% default budget per GPU")
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=16, help="generations per vLLM.generate() call, for incremental flushing")
    parser.add_argument("--output", required=True)
    # loom mode
    parser.add_argument("--count", type=int, default=100, help="loom mode: total scenario-pair generations to attempt")
    # grounding mode
    parser.add_argument("--count-per-category", type=int, default=100, help="grounding mode: target requests per category")
    parser.add_argument("--persistence", action="store_true", help="grounding mode: use the multi-entity persistence category set (Candidate 4) instead of the single-topic set (Candidate 2)")
    # role_sft mode
    parser.add_argument("--role", choices=sorted(_ROLE_SYSTEM_PROMPTS.keys()),
                         help="role_sft mode: which of the 10 MoE Sovereign roles to generate training data for")
    args = parser.parse_args()

    if args.mode == "role_sft" and not args.role:
        parser.error("--mode role_sft requires --role")

    if args.mode == "loom":
        run_loom_mode(args)
    elif args.mode == "grounding":
        run_grounding_mode(args)
    else:
        run_role_sft_mode(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

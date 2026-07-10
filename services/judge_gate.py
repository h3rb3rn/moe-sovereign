"""
services/judge_gate.py — Skip the judge when it cannot add value.

The judge call is skipped when (a) only one expert produced a result, or
(b) all expert results are near-identical (SequenceMatcher ratio above
threshold). Self-judging (judge model == only expert model) adds tokens
without discriminative power.

Flag: MOE_JUDGE_GATE=1 (default off).
Threshold: MOE_JUDGE_GATE_SIM (default 0.85).
"""

import os
from difflib import SequenceMatcher


def should_skip_judge(expert_outputs: list) -> tuple:
    """(skip: bool, reason: str, chosen_index: int)

    expert_outputs: list of answer strings (order = expert order).
    chosen_index: which output to use when skipping (longest one).
    """
    if os.getenv("MOE_JUDGE_GATE", "0") != "1":
        return (False, "", -1)
    outs = [o for o in expert_outputs if isinstance(o, str) and o.strip()]
    if not outs:
        return (False, "", -1)
    if len(outs) == 1:
        return (True, "single_expert", expert_outputs.index(outs[0]))
    sim_threshold = float(os.getenv("MOE_JUDGE_GATE_SIM", "0.85"))
    base = outs[0]
    for other in outs[1:]:
        # Compare on the first 4000 chars — enough signal, bounded cost.
        if SequenceMatcher(None, base[:4000], other[:4000]).ratio() < sim_threshold:
            return (False, "", -1)
    longest = max(outs, key=len)
    return (True, "consensus", expert_outputs.index(longest))

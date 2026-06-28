"""
generate_judge_dataset.py — Large-scale paraconsistent Judge training data generator

Runs on LUMI-G inside the lumi-multitorch container.
Uses Qwen2.5-32B-Instruct via a local vLLM server (started by the SLURM script)
to generate synthetic paraconsistent bilattice training samples from the
RouteLLM seed dataset (109k prompts).

Output format: Alpaca JSONL, compatible with train_judge_lora.py

Usage:
    python3 generate_judge_dataset.py \\
        --seed_file /scratch/.../data/routellm_seeds.jsonl \\
        --output_file /scratch/.../data/paraconsistent_large.jsonl \\
        --target 10000 \\
        --offset 0 \\
        --api_url http://localhost:8080/v1
"""

import os
import sys
import json
import time
import random
import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate_judge_dataset")

try:
    import httpx
except ImportError:
    logger.error("httpx not found. Install with: pip install httpx")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Domain mapping from RouteLLM sources
# ---------------------------------------------------------------------------
DOMAIN_KEYWORDS = {
    "code_reviewer":    ["code", "python", "function", "bug", "debug", "script", "program", "algorithm"],
    "math":             ["math", "equation", "calcul", "integral", "derivative", "algebra", "geometry", "probability"],
    "legal_advisor":    ["law", "legal", "contract", "court", "regulation", "compliance", "rights"],
    "science":          ["physics", "chemistry", "biology", "research", "experiment", "hypothesis"],
    "data_analysis":    ["data", "statistic", "dataset", "analysis", "model", "predict", "csv"],
    "creative_writing": ["story", "poem", "fiction", "creative", "write", "narrative", "character"],
    "reasoning":        ["reason", "logic", "argument", "deduce", "infer", "explain why", "analyse"],
    "technical_support":["error", "install", "configure", "setup", "issue", "problem", "fix"],
}

def guess_domain(prompt: str) -> str:
    pl = prompt.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in pl for kw in keywords):
            return domain
    return "general"


# ---------------------------------------------------------------------------
# vLLM client
# ---------------------------------------------------------------------------
def call_llm(client: httpx.Client, api_url: str, model: str,
             system: str, user: str, temperature: float = 0.4,
             max_tokens: int = 1024) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    for attempt in range(4):
        try:
            resp = client.post(
                f"{api_url}/chat/completions",
                json=payload,
                timeout=180.0,
            )
            if resp.status_code == 200:
                msg = resp.json()["choices"][0]["message"]
                content = msg.get("content") or msg.get("reasoning_content") or ""
                return content.strip()
            logger.warning("HTTP %d on attempt %d: %s", resp.status_code, attempt + 1, resp.text[:200])
        except Exception as exc:
            logger.warning("Request failed attempt %d: %s", attempt + 1, exc)
        time.sleep(3 * (attempt + 1))
    return ""


# ---------------------------------------------------------------------------
# Sample generation
# ---------------------------------------------------------------------------
def generate_sample(client: httpx.Client, api_url: str, model: str,
                    prompt: str, domain: str,
                    existing_answer_a: str = "",
                    existing_answer_b: str = "") -> dict | None:
    """
    Generate one paraconsistent training sample.
    If existing_answer_a and existing_answer_b are provided (from RouteLLM dataset),
    we skip the proponent/skeptic calls and go directly to the judge call.
    """

    if existing_answer_a and existing_answer_b:
        answer_a = existing_answer_a
        answer_b = existing_answer_b
    else:
        # Step 1: Proponent answer
        prop_sys = (
            f"You are a specialized expert in '{domain}'. "
            "Answer the user query accurately and comprehensively. "
            "End your response with 'CONFIDENCE: high'."
        )
        answer_a = call_llm(client, api_url, model, prop_sys, prompt, temperature=0.3, max_tokens=800)
        if not answer_a:
            return None

        # Step 2: Skeptic critique
        sk_sys = (
            "You are an adversarial Skeptic in a formal debate. "
            "Critique the Proponent's answer. Identify logical fallacies, errors, "
            "omissions, or hidden assumptions. Be critical, objective, and precise."
        )
        sk_user = f"[User Query]\n{prompt}\n\n[Proponent Answer]\n{answer_a}"
        answer_b = call_llm(client, api_url, model, sk_sys, sk_user, temperature=0.4, max_tokens=800)
        if not answer_b:
            return None

    # Step 3: Judge paraconsistent arbitration
    judge_sys = (
        f"You are a paraconsistent logic judge evaluating conflicting expert claims in '{domain}'.\n"
        "Apply Belnap-Dunn four-valued logic (T=True, F=False, I=Inconsistent, U=Unknown) "
        "to evaluate each disputed point.\n\n"
        "Respond with:\n"
        "1. A JSON conflict map inside <conflict_map> XML tags:\n"
        "<conflict_map>\n"
        "{\n"
        '  "points_of_dispute": [\n'
        '    {"point": "<description>", "evidence_a": "...", "evidence_b": "...", '
        '"bilattice_value": "<T|F|I|U>", "confidence": "<high|medium|low>"}\n'
        "  ]\n"
        "}\n"
        "</conflict_map>\n\n"
        "2. A final synthesis verdict:\n"
        "VERDICT: <A|B|SYNTHESIS> — <concise rationale>"
    )
    judge_user = f"CLAIM A (Proponent):\n{answer_a}\n\nCLAIM B (Skeptic/Opponent):\n{answer_b}"
    verdict = call_llm(client, api_url, model, judge_sys, judge_user, temperature=0.2, max_tokens=1200)
    if not verdict:
        return None

    # Build Alpaca training sample
    instruction = (
        f"Perform a paraconsistent bilattice evaluation (Belnap-Dunn logic) on the "
        f"following conflicting claims for category '{domain}' and generate a structured conflict map.\n\n"
        f"CLAIM A:\n{answer_a}\n\n"
        f"CLAIM B:\n{answer_b}"
    )

    return {
        "instruction": instruction,
        "input": "",
        "output": verdict,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--seed_file",   required=True, help="Input JSONL with seed prompts")
    p.add_argument("--output_file", required=True, help="Output JSONL file")
    p.add_argument("--api_url",     default="http://localhost:8080/v1", help="vLLM API base URL")
    p.add_argument("--model",       default="qwen", help="Model name as registered in vLLM")
    p.add_argument("--target",      type=int, default=10000, help="Target number of samples to generate")
    p.add_argument("--offset",      type=int, default=0,     help="Skip first N seeds (for parallel shards)")
    p.add_argument("--use_existing_answers", action="store_true",
                   help="Use gpt4_response/mixtral_response from RouteLLM as claim A/B directly")
    p.add_argument("--seed",        type=int, default=42)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)

    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Count existing samples to allow resuming
    existing = 0
    if output_path.exists():
        with open(output_path) as f:
            existing = sum(1 for line in f if line.strip())
        logger.info("Resuming — %d samples already in output file.", existing)

    # Load seeds
    logger.info("Loading seeds from %s ...", args.seed_file)
    seeds = []
    with open(args.seed_file) as f:
        for line in f:
            line = line.strip()
            if line:
                seeds.append(json.loads(line))
    logger.info("Loaded %d seed entries.", len(seeds))

    # Shuffle and apply offset
    random.shuffle(seeds)
    seeds = seeds[args.offset:]
    logger.info("After offset=%d: %d seeds to process.", args.offset, len(seeds))

    remaining = args.target - existing
    if remaining <= 0:
        logger.info("Target already reached (%d samples). Exiting.", existing)
        return

    logger.info("Need %d more samples to reach target %d.", remaining, args.target)

    # Wait for vLLM server to be ready
    logger.info("Waiting for vLLM API at %s ...", args.api_url)
    with httpx.Client() as probe:
        for _ in range(60):
            try:
                r = probe.get(f"{args.api_url}/models", timeout=5.0)
                if r.status_code == 200:
                    models = r.json().get("data", [])
                    logger.info("vLLM ready. Models: %s", [m["id"] for m in models])
                    # Use first model name if default placeholder
                    if models and args.model == "qwen":
                        args.model = models[0]["id"]
                        logger.info("Using model: %s", args.model)
                    break
            except Exception:
                pass
            logger.info("  vLLM not ready yet, retrying in 10s ...")
            time.sleep(10)
        else:
            logger.error("vLLM did not become ready in time. Aborting.")
            sys.exit(1)

    generated = existing
    skipped = 0

    with httpx.Client() as client, open(output_path, "a") as out_f:
        for i, seed in enumerate(seeds):
            if generated - existing >= remaining:
                break

            prompt = seed.get("prompt", "").strip()
            if not prompt or len(prompt) < 20:
                skipped += 1
                continue

            domain = guess_domain(prompt)

            # Use pre-existing answers if available (RouteLLM format)
            ans_a = ""
            ans_b = ""
            if args.use_existing_answers:
                ans_a = seed.get("gpt4_response", "").strip()
                ans_b = seed.get("mixtral_response", "").strip()
                if not ans_a or not ans_b or len(ans_a) < 50 or len(ans_b) < 50:
                    # Fall back to generating from scratch
                    ans_a = ans_b = ""

            logger.info("[%d/%d] Generating sample for domain=%s | prompt=%s...",
                        generated + 1, args.target, domain, prompt[:60])

            sample = generate_sample(
                client, args.api_url, args.model,
                prompt, domain, ans_a, ans_b
            )

            if sample:
                out_f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                out_f.flush()
                generated += 1
                logger.info("  ✅ Sample %d saved (total so far: %d)", i + 1, generated)
            else:
                skipped += 1
                logger.warning("  ⚠️  Failed to generate sample for: %s...", prompt[:60])

            # Small cooldown between samples
            time.sleep(0.5)

    logger.info("=== Generation complete ===")
    logger.info("Generated: %d new samples", generated - existing)
    logger.info("Skipped:   %d", skipped)
    logger.info("Total in file: %d", generated)


if __name__ == "__main__":
    main()

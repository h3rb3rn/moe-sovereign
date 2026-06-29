"""
generate_judge_dataset_async.py — High-throughput async paraconsistent Judge training data generator

Runs on LUMI-G inside the lumi-multitorch container.
Uses Qwen2.5-32B-Instruct via a local vLLM server to generate paraconsistent
bilattice training samples from the RouteLLM seed dataset.
Processes requests concurrently to maximize GPU utilization under vLLM.

Usage:
    python3 generate_judge_dataset_async.py \\
        --seed_file /scratch/.../data/routellm_seeds.jsonl \\
        --output_file /scratch/.../data/paraconsistent_large.jsonl \\
        --target 30000 \\
        --offset 0 \\
        --concurrency 32 \\
        --api_url http://localhost:8080/v1
"""

import os
import sys
import json
import time
import random
import argparse
import logging
import asyncio
from pathlib import Path
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate_judge_dataset_async")

# Domain mapping from RouteLLM sources
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

async def call_llm(client: httpx.AsyncClient, api_url: str, model: str,
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
            resp = await client.post(
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
        await asyncio.sleep(3 * (attempt + 1))
    return ""

async def generate_sample(client: httpx.AsyncClient, api_url: str, model: str,
                          prompt: str, domain: str,
                          ans_a: str = "", ans_b: str = "") -> dict | None:
    if not ans_a or not ans_b:
        # Generate Claim A
        prop_sys = (
            f"You are a specialized expert in '{domain}'. "
            "Answer the user query accurately and comprehensively. "
            "End your response with 'CONFIDENCE: high'."
        )
        ans_a = await call_llm(client, api_url, model, prop_sys, prompt, temperature=0.3, max_tokens=800)
        if not ans_a:
            return None

        # Generate Claim B
        sk_sys = (
            "You are an adversarial Skeptic in a formal debate. "
            "Critique the Proponent's answer. Identify logical fallacies, errors, "
            "omissions, or hidden assumptions. Be critical, objective, and precise."
        )
        sk_user = f"[User Query]\n{prompt}\n\n[Proponent Answer]\n{ans_a}"
        ans_b = await call_llm(client, api_url, model, sk_sys, sk_user, temperature=0.4, max_tokens=800)
        if not ans_b:
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
    judge_user = f"CLAIM A (Proponent):\n{ans_a}\n\nCLAIM B (Skeptic/Opponent):\n{ans_b}"
    verdict = await call_llm(client, api_url, model, judge_sys, judge_user, temperature=0.2, max_tokens=1200)
    if not verdict:
        return None

    instruction = (
        f"Perform a paraconsistent bilattice evaluation (Belnap-Dunn logic) on the "
        f"following conflicting claims for category '{domain}' and generate a structured conflict map.\n\n"
        f"CLAIM A:\n{ans_a}\n\n"
        f"CLAIM B:\n{ans_b}"
    )

    return {
        "instruction": instruction,
        "input": "",
        "output": verdict,
    }

async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed_file",   required=True, help="Input JSONL with seed prompts")
    parser.add_argument("--output_file", required=True, help="Output JSONL file")
    parser.add_argument("--api_url",     default="http://localhost:8080/v1", help="vLLM API base URL")
    parser.add_argument("--model",       default="qwen", help="Model name as registered in vLLM")
    parser.add_argument("--target",      type=int, default=10000, help="Target number of samples to generate")
    parser.add_argument("--offset",      type=int, default=0,     help="Skip first N seeds")
    parser.add_argument("--concurrency", type=int, default=32,    help="Max concurrent tasks")
    parser.add_argument("--use_existing_answers", action="store_true")
    parser.add_argument("--seed",        type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Parse existing output file to build the deduplication set
    existing_claims = set()
    existing_count = 0
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        inst = d.get("instruction", "")
                        if "CLAIM A:\n" in inst:
                            claim_a = inst.split("CLAIM A:\n", 1)[1].split("\n\nCLAIM B:", 1)[0].strip()
                            existing_claims.add(claim_a[:150])
                            existing_count += 1
                    except Exception:
                        pass
        logger.info("Parsed %d existing samples from output. Unique claim keys stored: %d", existing_count, len(existing_claims))

    # 2. Load and filter seeds
    logger.info("Loading seeds from %s ...", args.seed_file)
    seeds = []
    with open(args.seed_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                seeds.append(json.loads(line))
    logger.info("Loaded %d seed entries.", len(seeds))

    # Shuffle and apply offset
    random.shuffle(seeds)
    seeds = seeds[args.offset:]
    logger.info("After offset=%d: %d seeds to choose from.", args.offset, len(seeds))

    remaining = args.target - existing_count
    if remaining <= 0:
        logger.info("Target already reached (%d samples). Exiting.", existing_count)
        return

    logger.info("Need %d more samples to reach target %d.", remaining, args.target)

    # 3. Wait for vLLM to be ready
    logger.info("Waiting for vLLM API at %s ...", args.api_url)
    async with httpx.AsyncClient() as probe:
        for _ in range(60):
            try:
                r = await probe.get(f"{args.api_url}/models", timeout=5.0)
                if r.status_code == 200:
                    models = r.json().get("data", [])
                    logger.info("vLLM ready. Models: %s", [m["id"] for m in models])
                    if models and args.model == "qwen":
                        args.model = models[0]["id"]
                        logger.info("Using model: %s", args.model)
                    break
            except Exception:
                pass
            logger.info("  vLLM not ready yet, retrying in 10s ...")
            await asyncio.sleep(10)
        else:
            logger.error("vLLM did not become ready in time. Aborting.")
            sys.exit(1)

    # 4. Filter out seeds that have already been generated
    filtered_seeds = []
    for s in seeds:
        gpt4_resp = s.get("gpt4_response", "").strip()
        if gpt4_resp:
            # Check prefix match in existing set
            if gpt4_resp[:150] in existing_claims:
                continue
        filtered_seeds.append(s)

    logger.info("Filtered seeds: %d prompts ready to process after excluding duplicates.", len(filtered_seeds))

    # 5. Process concurrently
    sem = asyncio.Semaphore(args.concurrency)
    file_lock = asyncio.Lock()
    generated_count = existing_count
    skipped_count = 0

    async def worker(seed):
        nonlocal generated_count, skipped_count
        prompt = seed.get("prompt", "").strip()
        if not prompt or len(prompt) < 20:
            skipped_count += 1
            return

        domain = guess_domain(prompt)
        ans_a = ""
        ans_b = ""
        if args.use_existing_answers:
            ans_a = seed.get("gpt4_response", "").strip()
            ans_b = seed.get("mixtral_response", "").strip()
            if not ans_a or not ans_b or len(ans_a) < 50 or len(ans_b) < 50:
                ans_a = ans_b = ""

        async with sem:
            # Quick target exit check
            if generated_count >= args.target:
                return

            sample = await generate_sample(client, args.api_url, args.model, prompt, domain, ans_a, ans_b)

            if sample:
                async with file_lock:
                    if generated_count >= args.target:
                        return
                    with open(output_path, "a", encoding="utf-8") as out_f:
                        out_f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                        out_f.flush()
                    generated_count += 1
                    logger.info("  ✅ Sample %d saved (Prompt: %s...)", generated_count, prompt[:50])
            else:
                skipped_count += 1
                logger.warning("  ⚠️  Failed to generate sample for prompt: %s...", prompt[:50])

    # Start Async HTTP Client
    limits = httpx.Limits(max_keepalive_connections=args.concurrency, max_connections=args.concurrency * 2)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = []
        for seed in filtered_seeds:
            if generated_count >= args.target:
                break
            tasks.append(asyncio.create_task(worker(seed)))

        # Wait for all tasks to complete or target reached
        while tasks:
            tasks = [t for t in tasks if not t.done()]
            if generated_count >= args.target:
                logger.info("Target %d reached. Cancelling remaining tasks...", args.target)
                for t in tasks:
                    t.cancel()
                break
            await asyncio.sleep(1)

    logger.info("=== Generation complete ===")
    logger.info("Total generated: %d / %d", generated_count, args.target)
    logger.info("Skipped in this run: %d", skipped_count)

if __name__ == "__main__":
    asyncio.run(main_async())

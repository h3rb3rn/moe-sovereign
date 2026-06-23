import os
import sys
import json
import httpx
import asyncio
import argparse

# Add project root to python path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

SEEDS_FILE = os.getenv("SEEDS_FILE", os.path.join(PROJECT_ROOT, "router_dataset_seed.json"))
OUTPUT_FILE = os.getenv("DATASET_OUTPUT_FILE", os.path.join(PROJECT_ROOT, "data/paraconsistent_training_data.jsonl"))
API_URL = os.getenv("MOE_API_URL", "http://localhost:8002/v1/chat/completions")
API_TOKEN = os.getenv("SYSTEM_API_KEY", "")
MODEL_NAME = os.getenv("DATASET_GENERATOR_MODEL", "qwen-3.6-35b-sovereign@AIHUB")

async def call_llm(client, system_prompt, user_prompt, model=MODEL_NAME):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3
    }
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    for attempt in range(3):
        try:
            response = await client.post(API_URL, json=payload, headers=headers, timeout=120.0)
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
            else:
                print(f"LLM Call failed: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Exception during LLM Call: {e}")
        await asyncio.sleep(2)
    return ""

async def compile_sample(client, item, dry_run=False):
    prompt = item["prompt"]
    domains = item.get("expert_domains", ["general"])
    category = domains[0] if domains else "general"
    
    if dry_run:
        # Mock answers for dry-run
        prop_ans = f"This is the proponent answer for {prompt}. We should do X."
        sk_critique = f"Critique for {prompt}: Doing X is problematic. We should do Y instead."
        judge_verdict = (
            "<conflict_map>\n"
            "{\n"
            '  "points_of_dispute": [\n'
            '    {"point": "Method selection", "evidence_a": "Doing X", "evidence_b": "Doing Y", "bilattice_value": "I"}\n'
            "  ]\n"
            "}\n"
            "</conflict_map>\n"
            "VERDICT: SYNTHESIS - Combine X and Y."
        )
    else:
        # 1. Proponent Initial Answer
        prop_sys = f"You are a specialized expert in '{category}'. Answer the user query accurately and comprehensively. End your response with 'CONFIDENCE: high'."
        prop_ans = await call_llm(client, prop_sys, prompt)
        if not prop_ans:
            return None
            
        # 2. Skeptic Critique
        sk_sys = (
            f"You are an adversarial Skeptic/Opponent in a formal debate. "
            f"Critique the Proponent's answer to the query. Identify logical fallacies, errors, omissions, or assumptions. "
            f"Be critical, objective, and precise."
        )
        sk_user = f"[User Query]\n{prompt}\n\n[Proponent Initial Answer]\n{prop_ans}"
        sk_critique = await call_llm(client, sk_sys, sk_user)
        if not sk_critique:
            return None
            
        # 3. Judge Paraconsistent Arbitration
        judge_sys = (
            f"Two experts in '{category}' produced conflicting claims.\n"
            f"Perform a paraconsistent bilattice evaluation (Belnap-Dunn logic: T, F, I, U) to arbitrate the dispute.\n\n"
            f"Format your response with a JSON conflict map inside XML tags and a final synthesis verdict:\n"
            f"<conflict_map>\n"
            f"{{\n"
            f"  \"points_of_dispute\": [\n"
            f"    {{\"point\": \"<claim description>\", \"evidence_a\": \"...\", \"evidence_b\": \"...\", \"bilattice_value\": \"<T|F|I|U>\"}}\n"
            f"  ]\n"
            f"}}\n"
            f"</conflict_map>\n"
            f"VERDICT: <A|B|SYNTHESIS> — <rationale>"
        )
        judge_user = f"CLAIM A:\n{prop_ans}\n\nCLAIM B:\n{sk_critique}"
        judge_verdict = await call_llm(client, judge_sys, judge_user)
        if not judge_verdict:
            return None

    # Format the training pair for SFT fine-tuning
    training_sample = {
        "instruction": (
            f"Perform a paraconsistent bilattice evaluation (Belnap-Dunn logic) on the following conflicting claims "
            f"for category '{category}' and generate a structured conflict map.\n\n"
            f"CLAIM A:\n{prop_ans}\n\n"
            f"CLAIM B:\n{sk_critique}"
        ),
        "input": "",
        "output": judge_verdict
    }
    return training_sample

async def main():
    parser = argparse.ArgumentParser(description="Compile paraconsistent logic dataset for Judge training")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run using mock LLM responses")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of prompts to compile")
    args = parser.parse_args()
    
    if not os.path.exists(SEEDS_FILE):
        print(f"Seeds file not found at {SEEDS_FILE}")
        return
        
    with open(SEEDS_FILE, "r", encoding="utf-8") as f:
        seeds = json.load(f)
        
    # Filter for interesting/complex domains
    interesting_domains = {"medical_consult", "legal_advisor", "science", "coding", "math", "reasoning"}
    filtered_seeds = [
        item for item in seeds
        if any(d in interesting_domains for d in item.get("expert_domains", []))
    ]
    
    print(f"Found {len(filtered_seeds)} eligible seed prompts from interesting domains.")
    target_seeds = filtered_seeds[:args.limit]
    print(f"Processing top {len(target_seeds)} prompts (limit={args.limit})...")
    
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    
    compiled_count = 0
    async with httpx.AsyncClient() as client:
        for idx, item in enumerate(target_seeds):
            print(f"[{idx+1}/{len(target_seeds)}] Compiling: {item['prompt'][:60]}...")
            sample = await compile_sample(client, item, dry_run=args.dry_run)
            if sample:
                with open(OUTPUT_FILE, "a", encoding="utf-8") as out_f:
                    out_f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                compiled_count += 1
                print(f"  Successfully compiled and saved sample #{compiled_count}")
            await asyncio.sleep(1)
            
    print(f"Finished. Successfully compiled {compiled_count} paraconsistent training samples into {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())

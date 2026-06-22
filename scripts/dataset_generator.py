import os
import sys
import json
import httpx
import asyncio

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

SEEDS_FILE = os.getenv("SEEDS_FILE", os.path.join(PROJECT_ROOT, "router_dataset_seed.json"))
OUTPUT_FILE = os.getenv("DATASET_OUTPUT_FILE", os.path.join(PROJECT_ROOT, "synthetic_router_dataset.json"))
API_URL = os.getenv("MOE_API_URL", "http://localhost:8002/v1/chat/completions")
API_TOKEN = os.getenv("SYSTEM_API_KEY", "")
MODEL_NAME = os.getenv("DATASET_GENERATOR_MODEL", "qwen-3.6-35b-sovereign@AIHUB")
# Allow splitting the expert-domain groups across parallel runs (e.g. one per backend)
GROUP_START = int(os.getenv("GROUP_START", "0"))
GROUP_END = int(os.getenv("GROUP_END", "999999"))


async def generate_variants(client, category_str, prompts, count=15):
    """Generates synthetic prompts and their optimal templates for a specific expert category combination."""
    prompt_list_str = "\n".join(f"- {p}" for p in prompts[:10])
    
    # Extract expert list for JSON output example
    cats = [c.strip() for c in category_str.split(",") if c.strip()]
    experts_json_parts = []
    for exp in cats:
        experts_json_parts.append(f'          "{exp}": {{\n            "system_prompt": "Custom expert prompt for {exp}..."\n          }}')
    experts_example = ",\n".join(experts_json_parts)
    
    system_instruction = (
        "You are an AI data generator. Your task is to generate realistic, diverse, and natural user prompts "
        "in either German or English that would be routed to a specific combination of AI expert models in a Mixture of Experts system, "
        "along with the optimal routing template configuration for each generated prompt.\n\n"
        f"Target Expert Categories: {category_str}\n"
        "Here are some examples of prompts that require these exact experts:\n"
        f"{prompt_list_str}\n\n"
        f"Generate exactly {count} NEW, diverse, and realistic user prompts (mix of German and English as in the examples) "
        "that would require the exact same expert categories. For each generated prompt, define custom, prompt-specific system prompts "
        "for the planner, the judge, and every selected expert category.\n\n"
        "Output ONLY a valid JSON list of objects matching the following schema:\n"
        "[\n"
        "  {\n"
        '    "prompt": "new prompt 1",\n'
        '    "optimal_template": {\n'
        '      "planner_prompt": "Custom planner prompt instructions for prompt 1...",\n'
        '      "judge_prompt": "Custom judge prompt instructions for prompt 1...",\n'
        '      "experts": {\n'
        f"{experts_example}\n"
        '      }\n'
        '    }\n'
        '  },\n'
        '  ...\n'
        "]\n"
        "No explanations, no markdown, no formatting other than valid JSON."
    )
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Generate {count} new prompts for {category_str}. Output raw JSON list."}
        ],
        "temperature": 0.7
    }
    
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    for attempt in range(3):
        try:
            # We set a large timeout of 400s in case the GPU is under load (exceeds the 300s native-passthrough timeout)
            response = await client.post(API_URL, json=payload, headers=headers, timeout=400.0)
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    if "\n" in content:
                        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    else:
                        content = content.replace("```", "").strip()
                
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    # Search for a list inside the dict
                    for val in parsed.values():
                        if isinstance(val, list):
                            return val
                if isinstance(parsed, list):
                    return parsed
                print(f"Warning: model did not return list for {category_str}, attempt {attempt+1}. Output: {content[:200]}")
            else:
                print(f"Error for {category_str}: HTTP {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Failed to generate for {category_str} (attempt {attempt+1}): {e}")
        await asyncio.sleep(5)
    return []

async def main():
    if not os.path.exists(SEEDS_FILE):
        print(f"Seeds file not found at {SEEDS_FILE}")
        return
        
    with open(SEEDS_FILE, "r", encoding="utf-8") as f:
        seeds = json.load(f)
        
    print(f"Loaded {len(seeds)} seed prompts.")
    
    from services.dynamic_router import _generate_fallback_structured_prompts

    # Group seeds by expert_domains
    groups = {}
    for item in seeds:
        cat_key = ",".join(sorted(item["expert_domains"]))
        if cat_key not in groups:
            groups[cat_key] = []
        groups[cat_key].append(item["prompt"])
        
    print(f"Found {len(groups)} unique expert combinations.")
    
    synthetic_dataset = []
    
    async with httpx.AsyncClient() as client:
        # We process strictly sequentially (batch_size=1) to prevent VRAM queuing and ensure high stability
        cat_keys = list(groups.keys())[GROUP_START:GROUP_END]
        for idx, cat_key in enumerate(cat_keys):
            print(f"[{idx+1}/{len(cat_keys)}] Processing: {cat_key}...")
            prompts = groups[cat_key]
            
            new_prompts = await generate_variants(client, cat_key, prompts, count=15)
            print(f"  Generated {len(new_prompts)} prompts.")
            
            cats = [c.strip() for c in cat_key.split(",") if c.strip()]
            
            # Add seed prompts
            for p in groups[cat_key]:
                tpl = _generate_fallback_structured_prompts(p, cats)
                synthetic_dataset.append({
                    "prompt": p,
                    "expert_domains": cats,
                    "moe_mode": "default",
                    "optimal_template": {
                        "planner_model": "qwen-3.6-35b-sovereign",
                        "judge_model": "qwen-3.6-35b-sovereign",
                        "planner_prompt": tpl["planner_prompt"],
                        "judge_prompt": tpl["judge_prompt"],
                        "planner_num_ctx": 32768,
                        "judge_num_ctx": 32768,
                        "enable_cache": True,
                        "enable_graphrag": False,
                        "enable_web_research": False,
                        "experts": {
                            exp: {
                                "system_prompt": tpl["experts"][exp]["system_prompt"],
                                "context_window": 32768,
                                "models": [{"model": "default-model", "endpoint": "default", "role": "primary"}]
                            }
                            for exp in cats
                        }
                    }
                })
                
            # Add newly generated prompts
            for item in new_prompts:
                if isinstance(item, dict) and "prompt" in item and "optimal_template" in item:
                    prompt_text = item["prompt"]
                    opt_temp = item["optimal_template"]
                    
                    if isinstance(opt_temp, dict):
                        if "planner_model" not in opt_temp:
                            opt_temp["planner_model"] = "qwen-3.6-35b-sovereign"
                        if "judge_model" not in opt_temp:
                            opt_temp["judge_model"] = "qwen-3.6-35b-sovereign"
                        if "planner_num_ctx" not in opt_temp:
                            opt_temp["planner_num_ctx"] = 32768
                        if "judge_num_ctx" not in opt_temp:
                            opt_temp["judge_num_ctx"] = 32768
                        if "enable_cache" not in opt_temp:
                            opt_temp["enable_cache"] = True
                        if "enable_graphrag" not in opt_temp:
                            opt_temp["enable_graphrag"] = False
                        if "enable_web_research" not in opt_temp:
                            opt_temp["enable_web_research"] = False
                        
                        if "experts" not in opt_temp or not isinstance(opt_temp["experts"], dict):
                            opt_temp["experts"] = {}
                        for exp in cats:
                            if exp not in opt_temp["experts"] or not isinstance(opt_temp["experts"][exp], dict):
                                opt_temp["experts"][exp] = {"system_prompt": f"You are a specialized expert in {exp}."}
                            if "context_window" not in opt_temp["experts"][exp]:
                                opt_temp["experts"][exp]["context_window"] = 32768
                            if "models" not in opt_temp["experts"][exp]:
                                opt_temp["experts"][exp]["models"] = [{"model": "default-model", "endpoint": "default", "role": "primary"}]
                                
                    synthetic_dataset.append({
                        "prompt": prompt_text.strip(),
                        "expert_domains": cats,
                        "moe_mode": "default",
                        "optimal_template": opt_temp
                    })
            
            # Write checkpoint of dataset periodically so we don't lose progress
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(synthetic_dataset, f, ensure_ascii=False, indent=2)
            
            await asyncio.sleep(2) # cooling
                    
    print(f"Total dataset size: {len(synthetic_dataset)} samples.")
    print(f"Dataset successfully saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())

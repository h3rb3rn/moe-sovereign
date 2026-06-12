import sys
import os
import re
import httpx
import asyncio
import json

# Add project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_ui.database import init_db, close_db, upsert_model_metadata

# Derive OLLAMA endpoints from env (same source as dynamic_router.py / config.py)
_inference_servers_raw = os.getenv("INFERENCE_SERVERS", "[]")
try:
    _inference_servers = json.loads(_inference_servers_raw)
except Exception:
    _inference_servers = []
OLLAMA_ENDPOINTS = [
    s["url"].rstrip("/v1").rstrip("/")
    for s in _inference_servers
    if s.get("api_type") == "ollama" and s.get("enabled", True)
]
# All cloud endpoints are derived from INFERENCE_SERVERS (same as dynamic_router.py).
# Configure them via MoE Admin UI — no separate env vars.
CLOUD_ENDPOINTS = [
    {
        "name":  s["name"],
        "url":   s["url"].rstrip("/"),
        "token": s.get("token", ""),
    }
    for s in _inference_servers
    if s.get("api_type", "ollama") != "ollama" and s.get("enabled", True)
]

def parse_model_params(model_name: str) -> tuple[float, int, str, list[str], dict, bool]:
    """Parse model name to extract parameter size, context window, family, strengths, benchmarks, and explicit context flag."""
    name_lower = model_name.lower()
    
    # 1. Family detection
    family = "other"
    if "qwen" in name_lower:
        family = "qwen"
    elif "llama" in name_lower:
        family = "llama"
    elif "gemma" in name_lower:
        family = "gemma"
    elif "phi" in name_lower:
        family = "phi"
    elif "deepseek" in name_lower:
        family = "deepseek"
    elif "mistral" in name_lower or "nemo" in name_lower:
        family = "mistral"
    elif "glm" in name_lower:
        family = "glm"
    
    # 2. Parameter size detection
    param_size = 7.0  # default fallback
    # Search for patterns like 3b, 8b, 14b, 32b, 35b, 70b, 671b
    size_match = re.search(r'(\d+(?:\.\d+)?)[bm]', name_lower)
    if size_match:
        val = float(size_match.group(1))
        # If it's in Millions, convert to Billions
        if "m" in size_match.group(0):
            param_size = val / 1000.0
        else:
            param_size = val
    else:
        # Fallbacks for known models
        if "frontier" in name_lower or "large" in name_lower:
            param_size = 70.0
        elif "mini" in name_lower or "micro" in name_lower:
            param_size = 3.0
            
    # 3. Context window detection
    context_window = 4096  # default fallback
    has_explicit_ctx = False
    
    # Check for explicit ctx suffixes (e.g. ctx4k, ctx8k, 32k, 128k)
    ctx_match = re.search(r'ctx(\d+)k', name_lower)
    if not ctx_match:
        ctx_match = re.search(r'-(\d+)k\b', name_lower)
    if not ctx_match:
        # Avoid matching parameter sizes like "32b" by matching "32k"
        ctx_match = re.search(r'\b(\d+)k\b', name_lower)
        
    if ctx_match:
        context_window = int(ctx_match.group(1)) * 1024
        has_explicit_ctx = True
    elif "128k" in name_lower or "ctx128k" in name_lower:
        context_window = 131072
        has_explicit_ctx = True
    elif "32k" in name_lower:
        context_window = 32768
        has_explicit_ctx = True
    elif "8k" in name_lower:
        context_window = 8192
        has_explicit_ctx = True
    elif "1m" in name_lower:
        context_window = 1048576
        has_explicit_ctx = True
    else:
        # Family-based context window defaults
        if family == "qwen":
            context_window = 32768
        elif family == "llama" and ("3.1" in name_lower or "3.2" in name_lower or "3.3" in name_lower):
            context_window = 131072
        elif family == "gemma" and "gemma3" in name_lower:
            context_window = 32768
        elif family == "deepseek" and ("r1" in name_lower or "v3" in name_lower or "v4" in name_lower):
            context_window = 131072
            
    # 4. Strengths detection
    strengths = []
    if "coder" in name_lower or "code" in name_lower:
        strengths.extend(["code", "reasoning", "technical"])
    if "math" in name_lower:
        strengths.extend(["math", "reasoning"])
    if "r1" in name_lower or "qwq" in name_lower or "thinking" in name_lower or "reasoning" in name_lower:
        strengths.extend(["reasoning", "math", "code"])
    if "vl" in name_lower or "vision" in name_lower:
        strengths.extend(["vision", "multimodal"])
    if "legal" in name_lower:
        strengths.extend(["legal", "compliance"])
    if "mail" in name_lower or "classify" in name_lower:
        strengths.extend(["classification", "concise"])
    
    if not strengths:
        strengths = ["general"]
    else:
        # Deduplicate
        strengths = list(set(strengths))
        
    # 5. Benchmark scores estimation based on family, size and type
    # Base baseline scores
    mmlu = 55.0
    gsm8k = 40.0
    human_eval = 45.0
    gpqa = 25.0
    
    # Scale based on parameter size
    if param_size < 4.0:
        mmlu, gsm8k, human_eval, gpqa = 62.0, 50.0, 52.0, 28.0
    elif param_size < 10.0:
        mmlu, gsm8k, human_eval, gpqa = 72.0, 75.0, 68.0, 32.0
    elif param_size < 25.0:
        mmlu, gsm8k, human_eval, gpqa = 76.0, 82.0, 75.0, 36.0
    elif param_size < 40.0:
        mmlu, gsm8k, human_eval, gpqa = 80.0, 88.0, 82.0, 42.0
    else:  # Large models (70B+)
        mmlu, gsm8k, human_eval, gpqa = 85.0, 93.0, 88.0, 50.0
        
    # Boost if it is a reasoning/thinking model
    if "r1" in name_lower or "qwq" in name_lower or "thinking" in name_lower:
        gsm8k = min(99.0, gsm8k + 15.0)
        human_eval = min(98.0, human_eval + 15.0)
        gpqa = min(90.0, gpqa + 20.0)
        
    # Boost coder models for HumanEval
    if "coder" in name_lower or "code" in name_lower:
        human_eval = min(98.0, human_eval + 15.0)
        
    # Boost math models for GSM8K
    if "math" in name_lower:
        gsm8k = min(99.0, gsm8k + 15.0)
        
    benchmarks = {
        "mmlu": round(mmlu, 1),
        "gsm8k": round(gsm8k, 1),
        "human_eval": round(human_eval, 1),
        "gpqa": round(gpqa, 1)
    }
    
    return param_size, context_window, family, strengths, benchmarks, has_explicit_ctx

async def fetch_local_models(client: httpx.AsyncClient) -> set[str]:
    models = set()
    for endpoint in OLLAMA_ENDPOINTS:
        try:
            url = f"{endpoint}/api/tags"
            response = await client.get(url, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                for m in data.get("models", []):
                    # Local models in DB are cached as name@node
                    # Node name is derived from endpoint
                    node_name = "N04-RTX" if "224" in endpoint else "N11-M10"
                    models.add(f"{m['name']}@{node_name}")
        except Exception as e:
            print(f"Error fetching from {endpoint}: {e}")
    return models

async def fetch_cloud_models(client: httpx.AsyncClient) -> set[str]:
    models = set()
    for cloud in CLOUD_ENDPOINTS:
        try:
            url = f"{cloud['url']}/models"
            headers = {"Authorization": f"Bearer {cloud['token']}"}
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                for m in data.get("data", []):
                    # Cloud models already have the @endpoint format from the server
                    models.add(m["id"])
        except Exception as e:
            print(f"Error fetching cloud models from {cloud['name']}: {e}")
    return models

async def research_model_metadata_online(client: httpx.AsyncClient, model_name: str) -> dict:
    """Query Hugging Face Hub API to research real model configuration and context length."""
    query = model_name.replace(":", "-").replace("_", "-").replace(".", "-").lower()
    try:
        url = f"https://huggingface.co/api/models?search={query}&limit=3"
        res = await client.get(url, timeout=5.0)
        if res.status_code == 200:
            models = res.json()
            for m in models:
                repo_id = m.get("modelId", "")
                if query.replace("-", "") in repo_id.lower().replace("/", "").replace("-", ""):
                    # Fetch config.json directly
                    config_url = f"https://huggingface.co/api/models/{repo_id}/config"
                    cfg_res = await client.get(config_url, timeout=5.0)
                    if cfg_res.status_code == 200:
                        cfg = cfg_res.json()
                        context = (
                            cfg.get("max_position_embeddings") or 
                            cfg.get("model_max_length") or 
                            cfg.get("seq_length") or
                            cfg.get("max_seq_len")
                        )
                        return {
                            "repo_id": repo_id,
                            "context_window": context,
                            "family": cfg.get("model_type"),
                            "tags": m.get("tags", [])
                        }
    except Exception as e:
        print(f"HF online search failed for {model_name}: {e}")
    return {}

async def main():
    print("Initializing Database...")
    await init_db()
    
    all_models = set()
    async with httpx.AsyncClient() as client:
        print("Polling local Ollama endpoints...")
        local_models = await fetch_local_models(client)
        all_models.update(local_models)
        print(f"Discovered {len(local_models)} local models.")
        
        print("Polling cloud API models...")
        cloud_models = await fetch_cloud_models(client)
        all_models.update(cloud_models)
        print(f"Discovered {len(cloud_models)} cloud models.")
        
        print(f"Total active models discovered: {len(all_models)}")
        
        upsert_count = 0
        for model_id in all_models:
            try:
                # Parse components from model id
                if "@" in model_id:
                    base_name, endpoint = model_id.split("@", 1)
                else:
                    base_name, endpoint = model_id, ""
                    
                param_size, context, family, strengths, benchmarks, has_explicit_ctx = parse_model_params(base_name)
                
                # Refine with online research
                print(f"Researching online metadata for: {base_name}...")
                online_meta = await research_model_metadata_online(client, base_name)
                if online_meta:
                    if online_meta.get("context_window"):
                        if has_explicit_ctx:
                            print(f"  -> Skipping online context window override due to explicit context tag in name ({context})")
                        else:
                            try:
                                context = int(online_meta["context_window"])
                                print(f"  -> Found online context window: {context}")
                            except Exception:
                                pass
                    if online_meta.get("family"):
                        family = online_meta["family"]
                        print(f"  -> Found online model family: {family}")
                
                await upsert_model_metadata(
                    model_id=model_id,
                    base_model=base_name,
                    parameter_size_b=param_size,
                    context_window=context,
                    family=family,
                    strengths=strengths,
                    benchmark_scores=benchmarks
                )
                upsert_count += 1
            except Exception as e:
                print(f"Failed to index {model_id}: {e}")
            
    print(f"Successfully upserted {upsert_count} models to model_metadata.")
    print("Closing Database Connection...")
    await close_db()
    print("Metadata indexing job completed.")

if __name__ == "__main__":
    asyncio.run(main())

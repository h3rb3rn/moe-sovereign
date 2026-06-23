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

def _parse_size_str(size_str: str) -> float:
    """Parse Ollama parameter_size strings like '30.5B', '7B', '8x7B' → billions."""
    s = size_str.strip().upper()
    moe = re.match(r'(\d+)X(\d+(?:\.\d+)?)B', s)
    if moe:
        return float(moe.group(1)) * float(moe.group(2))
    single = re.search(r'(\d+(?:\.\d+)?)B', s)
    if single:
        return float(single.group(1))
    return 0.0


async def fetch_local_models_with_metadata(client: httpx.AsyncClient) -> dict[str, dict]:
    """Probe all Ollama inference servers and fetch per-model metadata via /api/show.

    /api/show returns exact GGUF metadata (context_length, family, parameter_size)
    directly from the model tensor header — more reliable than name-heuristics or
    HuggingFace lookups. Falls back gracefully when a server is unreachable.

    Returns: {model_id@node: {context_window, family, parameter_size_b, quantization}}
    """
    result: dict[str, dict] = {}
    ollama_servers = [
        s for s in _inference_servers
        if s.get("api_type", "ollama") == "ollama" and s.get("enabled", True)
    ]
    for server in ollama_servers:
        node_name = server["name"]
        base = server["url"].rstrip("/v1").rstrip("/")
        try:
            r = await client.get(f"{base}/api/tags", timeout=5.0)
            if r.status_code != 200:
                continue
            raw_models = r.json().get("models", [])
        except Exception as e:
            print(f"Error listing models from {node_name} ({base}): {e}")
            continue

        print(f"{node_name}: {len(raw_models)} models")
        for m in raw_models:
            model_name = m.get("name", "")
            if not model_name:
                continue
            model_id = f"{model_name}@{node_name}"
            try:
                rs = await client.post(
                    f"{base}/api/show", json={"model": model_name}, timeout=8.0
                )
                if rs.status_code == 200:
                    show   = rs.json()
                    dtails = show.get("details", {})
                    mi     = show.get("model_info", {})
                    # Context window from GGUF tensor header (most accurate source)
                    ctx = next(
                        (int(v) for k, v in mi.items()
                         if "context_length" in k.lower() and isinstance(v, (int, float))),
                        None,
                    )
                    size_b = _parse_size_str(dtails.get("parameter_size", ""))
                    family = (dtails.get("family") or
                              (dtails.get("families") or [""])[0] or "").lower()
                    result[model_id] = {
                        "context_window":   ctx,
                        "family":           family,
                        "parameter_size_b": size_b,
                        "quantization":     dtails.get("quantization_level", ""),
                    }
                    continue
            except Exception as e:
                print(f"  /api/show failed for {model_name}: {e}")
            # Fallback: add without metadata (name-heuristics applied in main())
            result[model_id] = {}
    return result


async def fetch_local_models(client: httpx.AsyncClient) -> set[str]:
    """Legacy wrapper — returns just the model@node IDs. Use fetch_local_models_with_metadata for full data."""
    meta = await fetch_local_models_with_metadata(client)
    return set(meta.keys())

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

async def fetch_ollama_library_metadata(client: httpx.AsyncClient, model_name: str) -> dict:
    """Query Ollama.com search API for model description and capability tags.

    Returns {description, tags: list[str], pull_count} or {}.
    Tags are inferred from the description text (code/vision/agentic/reasoning).
    """
    base_name = model_name.split(":")[0].split("@")[0]
    try:
        r = await client.get(
            "https://ollama.com/api/search",
            params={"q": base_name, "limit": 5},
            headers={"Accept": "application/json"},
            timeout=8.0,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        models = data if isinstance(data, list) else data.get("models", [])
        for m in models:
            name = m.get("name", "")
            if not name:
                continue
            # Accept if base names overlap (e.g. "north-mini-code" ↔ "north-mini-code-1.0")
            n1 = base_name.lower().replace("-", "").replace("_", "").replace(".", "")
            n2 = name.lower().replace("-", "").replace("_", "").replace(".", "")
            if n1 not in n2 and n2 not in n1:
                continue
            desc = (m.get("description") or "").lower()
            tags: list[str] = []
            if any(w in desc for w in ["code", "coder", "programming", "developer"]):
                tags.append("code")
            if any(w in desc for w in ["vision", "image", "multimodal", "vlm", "visual"]):
                tags.append("vision")
            if any(w in desc for w in ["agent", "tool call", "function call", "agentic"]):
                tags.append("agentic")
            if any(w in desc for w in ["reasoning", "thinking", "chain-of-thought", "r1"]):
                tags.append("reasoning")
            return {
                "description": m.get("description", ""),
                "tags": tags,
                "pull_count": m.get("pulls", 0) or m.get("pullCount", 0),
            }
    except Exception as e:
        print(f"Ollama library search failed for {model_name}: {e}")
    return {}


async def fetch_openrouter_models(client: httpx.AsyncClient) -> dict[str, dict]:
    """Fetch OpenRouter models metadata (context window, tags) from their public API.

    Returns: {model_id: {context_window, tags}}
    """
    result: dict[str, dict] = {}
    try:
        r = await client.get("https://openrouter.ai/api/v1/models", timeout=10.0)
        if r.status_code == 200:
            data = r.json()
            models = data.get("data", [])
            for m in models:
                m_id = m.get("id")
                if not m_id:
                    continue
                context = m.get("context_length", 4096)
                desc = (m.get("description") or "").lower()
                tags: list[str] = []
                if any(w in desc for w in ["code", "coder", "programming", "developer"]):
                    tags.append("code")
                if any(w in desc for w in ["vision", "image", "multimodal", "vlm", "visual"]):
                    tags.append("vision")
                if any(w in desc for w in ["agent", "tool call", "function call", "agentic"]):
                    tags.append("agentic")
                if any(w in desc for w in ["reasoning", "thinking", "chain-of-thought", "r1"]):
                    tags.append("reasoning")
                result[m_id] = {
                    "context_window": context,
                    "tags": tags,
                }
    except Exception as e:
        print(f"Error fetching OpenRouter models: {e}")
    return result


async def research_model_metadata_online(client: httpx.AsyncClient, model_name: str) -> dict:
    """Query Ollama Library first, then HuggingFace Hub as fallback.

    Priority: Ollama.com (always has context about Ollama models) →
              HuggingFace config.json (accurate context window for HF models).
    """
    # 1. Ollama Library
    ollama_meta = await fetch_ollama_library_metadata(client, model_name)
    if ollama_meta:
        print(f"  → Ollama Library: {ollama_meta.get('description','')[:60]!r}")

    # 2. HuggingFace (for context_window and family)
    query = model_name.replace(":", "-").replace("_", "-").replace(".", "-").lower()
    hf_meta: dict = {}
    try:
        url = f"https://huggingface.co/api/models?search={query}&limit=3"
        res = await client.get(url, timeout=5.0)
        if res.status_code == 200:
            hf_models = res.json()
            for m in hf_models:
                repo_id = m.get("modelId", "")
                if query.replace("-", "") in repo_id.lower().replace("/", "").replace("-", ""):
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
                        hf_meta = {
                            "repo_id":        repo_id,
                            "context_window": context,
                            "family":         cfg.get("model_type"),
                            "hf_tags":        m.get("tags", []),
                        }
                        print(f"  → HuggingFace: {repo_id!r} ctx={context}")
                    break
    except Exception as e:
        print(f"HF online search failed for {model_name}: {e}")

    # Merge: HF wins for context_window/family, Ollama wins for description/tags
    merged: dict = {}
    if hf_meta.get("context_window"):
        merged["context_window"] = hf_meta["context_window"]
    if hf_meta.get("family"):
        merged["family"] = hf_meta["family"]
    if ollama_meta.get("tags"):
        merged["tags"] = ollama_meta["tags"]
    elif hf_meta.get("hf_tags"):
        # Derive tags from HF pipeline tags
        hft = [t.lower() for t in hf_meta["hf_tags"]]
        merged["tags"] = [
            t for t, kw in [
                ("code",      ["code", "coder"]),
                ("vision",    ["vision", "image-text", "visual"]),
                ("reasoning", ["reasoning"]),
            ]
            if any(k in tag for k in kw for tag in hft)
        ]
    return merged

async def main():
    print("Initializing Database...")
    await init_db()

    async with httpx.AsyncClient() as client:
        # ── Local Ollama models: /api/show gives GGUF-exact metadata ──────────
        print("\nPolling local Ollama endpoints via /api/show...")
        local_meta = await fetch_local_models_with_metadata(client)
        print(f"Discovered {len(local_meta)} local models with GGUF metadata.")

        # ── Cloud models from configured INFERENCE_SERVERS ────────────────────
        print("\nPolling cloud API models...")
        cloud_models = await fetch_cloud_models(client)
        print(f"Discovered {len(cloud_models)} cloud models.")

        # ── OpenRouter free/vision/agentic tags ───────────────────────────────
        print("\nFetching OpenRouter model metadata...")
        or_meta = await fetch_openrouter_models(client)

        all_model_ids = set(local_meta.keys()) | cloud_models
        print(f"\nTotal models to index: {len(all_model_ids)}")
        upsert_count = 0

        for model_id in sorted(all_model_ids):
            try:
                base_name = model_id.split("@", 1)[0] if "@" in model_id else model_id
                is_local  = model_id in local_meta
                show_data = local_meta.get(model_id, {})

                # Seed from /api/show (local) or name-heuristics (cloud)
                if is_local and show_data.get("context_window"):
                    # GGUF values are authoritative — don't override with online guesses
                    param_size       = show_data.get("parameter_size_b", 0.0)
                    context          = show_data["context_window"]
                    family           = show_data.get("family", "")
                    has_explicit_ctx = True
                    _, _, _, strengths, benchmarks, _ = parse_model_params(base_name)
                else:
                    param_size, context, family, strengths, benchmarks, has_explicit_ctx = \
                        parse_model_params(base_name)

                # Tags: strengths + OpenRouter
                tags: list[str] = []
                or_entry = or_meta.get(model_id) or or_meta.get(base_name) or {}
                tags.extend(or_entry.get("tags") or [])
                if "vision"    in strengths and "vision"    not in tags: tags.append("vision")
                if any(s in strengths for s in ("reasoning", "math")) and "reasoning" not in tags:
                    tags.append("reasoning")
                if "code"      in strengths and "code"      not in tags: tags.append("code")
                tags = list(dict.fromkeys(tags))

                # Online enrichment: Ollama Library → HuggingFace
                print(f"Researching online metadata for: {base_name}...")
                online = await research_model_metadata_online(client, base_name)
                if online.get("context_window") and not has_explicit_ctx:
                    context = int(online["context_window"])
                if online.get("family") and not family:
                    family = online["family"]
                for t in (online.get("tags") or []):
                    if t not in tags:
                        tags.append(t)
                if or_entry.get("context_window") and not has_explicit_ctx:
                    context = or_entry["context_window"]

                await upsert_model_metadata(
                    model_id         = model_id,
                    base_model       = base_name,
                    parameter_size_b = param_size,
                    context_window   = context,
                    family           = family or "other",
                    strengths        = strengths,
                    benchmark_scores = benchmarks,
                    tags             = tags,
                )
                upsert_count += 1
            except Exception as e:
                print(f"Failed to index {model_id}: {e}")

    print(f"\nSuccessfully upserted {upsert_count} models to model_metadata.")
    print("Closing Database Connection...")
    await close_db()
    print("Metadata indexing job completed.")

if __name__ == "__main__":
    asyncio.run(main())

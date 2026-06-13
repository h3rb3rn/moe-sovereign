import os
import re
import json
import time
import random
import logging
import uuid
from typing import Optional, List, Dict
import httpx
import numpy as np
import onnxruntime as ort
import chromadb
from chromadb.utils import embedding_functions

import state
from admin_ui.database import _get_pool, log_dynamic_template_feedback
from config import MOE_USERDB_URL, URL_MAP, API_TYPE_MAP, TOKEN_MAP, INFERENCE_SERVERS_LIST

logger = logging.getLogger("MOE-SOVEREIGN")

# Classes mapped to ONNX model outputs
EXPERT_CLASSES = [
    "code_reviewer",
    "creative_writer",
    "creative_writing",
    "data_analysis",
    "general",
    "legal_advisor",
    "mail_classify",
    "math",
    "precision_tools",
    "reasoning",
    "research",
    "science",
    "technical_support",
    "tool_agent"
]

COMPLEXITY_CLASSES = ["trivial", "moderate", "complex", "memory_recall"]
GATE_CLASSES = ["web_research", "graphrag"]

# Endpoints configuration — fully derived from admin-configured INFERENCE_SERVERS.
# No hardcoded URLs or tokens here; add/remove endpoints via MoE Admin UI instead.
#
# Local Ollama nodes: native API ("/api/tags", "/api/ps") requires stripping the
# OpenAI-compat "/v1" suffix that INFERENCE_SERVERS URLs carry.
OLLAMA_ENDPOINTS = {
    name: (url[:-len("/v1")] if url.endswith("/v1") else url)
    for name, url in URL_MAP.items()
    if API_TYPE_MAP.get(name) == "ollama"
}

# Cloud/OpenAI-compatible endpoints: all non-ollama entries in INFERENCE_SERVERS.
# Each carries its own URL and token — no additional env vars needed.
# Deployments without any cloud endpoint configured will simply skip cloud discovery.
CLOUD_ENDPOINTS: list[dict] = [
    {
        "name":  s["name"],
        "url":   s["url"].rstrip("/"),
        "token": s.get("token", ""),
    }
    for s in INFERENCE_SERVERS_LIST
    if s.get("api_type", "ollama") != "ollama" and s.get("enabled", True)
]

# Heuristic helpers to generate training targets and fallbacks
_MEMORY_RECALL_RE = re.compile(
    r'\b(was habe ich (gesagt|erwähnt|genannt)|what did i (say|tell|mention)|'
    r'ich habe (gesagt|erwähnt|genannt|dir gesagt)|i (said|told you|mentioned)|'
    r'wie hieß|wie war|du hast|you said|you told me|'
    r'aus unserem (gespräch|chat|dialog)|in our (conversation|chat|session)|'
    r'erinner(e|st) dich|kannst du dich erinnern|remember when|remember what|'
    r'ich habe (dir )?vorhin|weißt du noch|do you remember|'
    r'welche (datenbank|port|ip|adresse|name|version|limit|schlüssel|key|team)'
    r'\s+(habe ich|hatte ich|hab ich|have i|did i)\b)\b',
    re.I,
)

_COMPLEX_MARKERS = re.compile(
    r'\b(vergleiche?n?|analysiere?n?|erkläre? warum|untersuche?n?|bewerte?n?|evaluiere?n?|'
    r'entwirf|entwickle?n?|plane?n?|implementiere?n?|refaktoriere?n?|optimiere?n?|'
    r'unterschied|vor- und nachteile?|pros? and cons?|step[- ]by[- ]step|'
    r'schritt für schritt|warum|wie genau|inwiefern|welche auswirkungen|'
    r'compare|analyze|explain why|evaluate|design|implement|optimize)\b',
    re.I,
)

_RESEARCH_MARKERS = re.compile(
    r'\b(paper|article|study|studies|journal|publication|published|according to|'
    r'researcher|professor|author|et al\.?|arxiv|doi|isbn|pubchem|orcid|'
    r'dataset|classification|compound|species|genus|wikipedia|'
    r'museum|collection|archive|standard|regulation|nonnative|invasive|'
    r'transcript|video|episode|season|series|channel)\b',
    re.I,
)

_TRIVIAL_MARKERS = re.compile(
    r'^(was ist|what is|wer ist|who is|wann ist|when is|wo ist|where is|'
    r'wie viel|how much|wie viele|how many|nenne|list|zeige mir|show me|'
    r'übersetze?|translate)\b',
    re.I,
)

_DOMAIN_MARKERS = re.compile(
    r'\b(§+\s*\d+|bgh|bverfg|awmf|s3-leitlinie?|icd-\d+|dosierung|wirkstoff|'
    r'differentialdiagnose?|subnetz|cidr|bgp|ospf|ldap|oauth|openid|'
    r'integral|ableitung|differentialgleichung|eigenwert|fourier|'
    r'sql|cypher|neo4j|docker|kubernetes|terraform|ansible)\b',
    re.I,
)

_CODE_MARKERS = re.compile(
    r'```|`[^`]+`|\bdef \b|\bclass \b|\bfunction\b|\bimport \b|'
    r'\{["\']|\[\s*\{|<[a-z]+>|#!/',
    re.I,
)

_WEB_INTENT_RE = re.compile(
    r'\b(aktuell|news|wetter|latest|recent|web search|online|search the web|internet|'
    r'google|heute|today|recent events|current affairs|suche im web|aktuellste)\b',
    re.I,
)

def get_complexity(query: str) -> str:
    n = len(query.split())
    if _MEMORY_RECALL_RE.search(query):
        return "memory_recall"
    if n >= 80 or _COMPLEX_MARKERS.search(query) or _RESEARCH_MARKERS.search(query):
        return "complex"
    if n <= 15 and _TRIVIAL_MARKERS.search(query):
        return "trivial"
    if n <= 8 and not _DOMAIN_MARKERS.search(query) and not _CODE_MARKERS.search(query):
        return "trivial"
    has_domain = bool(_DOMAIN_MARKERS.search(query))
    has_code = bool(_CODE_MARKERS.search(query))
    if has_domain or has_code:
        return "moderate"
    return "moderate"

def get_gates(query: str, complexity: str) -> tuple[float, float]:
    if complexity in ("trivial", "memory_recall"):
        return 0.0, 0.0
    web_research = 0.0
    if complexity == "complex" or _WEB_INTENT_RE.search(query):
        web_research = 1.0
    graphrag = 1.0
    return web_research, graphrag

ROUTER_ONNX_PATH = os.getenv("SOVEREIGN_ROUTER_ONNX_PATH", "/app/models/sovereign_router.onnx")
CHROMA_HOST = os.getenv("CHROMA_HOST", "")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

_onnx_session = None
_chroma_client = None
_template_collection = None
_embedding_function = None

_cluster_state_cache = {"ts": 0.0, "models": []}

def init_router():
    """Initializes the ONNX session and ChromaDB collection."""
    global _onnx_session, _chroma_client, _template_collection, _embedding_function
    
    # 1. Load ONNX model
    if os.path.exists(ROUTER_ONNX_PATH):
        try:
            # CPU Execution Provider is default and highly optimized for AVX-512
            # GPU providers (CUDA/ROCm) can be used if available
            providers = ['CPUExecutionProvider']
            if ort.get_device() == 'GPU':
                # Auto-detect ROCm (AMD) or CUDA (Nvidia) execution provider
                available = ort.get_available_providers()
                if 'MIGraphXExecutionProvider' in available:
                    providers.insert(0, 'MIGraphXExecutionProvider')
                elif 'ROCMExecutionProvider' in available:
                    providers.insert(0, 'ROCMExecutionProvider')
                elif 'CUDAExecutionProvider' in available:
                    providers.insert(0, 'CUDAExecutionProvider')
            
            _onnx_session = ort.InferenceSession(ROUTER_ONNX_PATH, providers=providers)
            logger.info(f"🎯 Sovereign Router ONNX model loaded from {ROUTER_ONNX_PATH} (providers={providers})")
        except Exception as e:
            logger.error(f"❌ Failed to load Sovereign Router ONNX model: {e}")
    else:
        logger.warning(f"⚠️ Sovereign Router ONNX model not found at {ROUTER_ONNX_PATH}")

    # 2. Initialize ChromaDB client and collection
    if CHROMA_HOST:
        try:
            _chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            _embedding_function = embedding_functions.DefaultEmbeddingFunction()
            _template_collection = _chroma_client.get_or_create_collection(
                name="moe_template_cache",
                embedding_function=_embedding_function,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info("connected to ChromaDB moe_template_cache collection.")
        except Exception as e:
            logger.error(f"❌ Failed to connect to ChromaDB moe_template_cache: {e}")


async def _get_cluster_state() -> list[dict]:
    """Gets lists of available models and warmed state, cached for 10s."""
    now = time.monotonic()
    if now - _cluster_state_cache["ts"] < 10 and _cluster_state_cache["models"]:
        return _cluster_state_cache["models"]

    models_list = []
    async with httpx.AsyncClient() as client:
        # 1. Query local Ollama endpoints
        for node, endpoint in OLLAMA_ENDPOINTS.items():
            try:
                # Get model tags
                res = await client.get(f"{endpoint}/api/tags", timeout=3.0)
                # Get currently active / warmed models
                ps_res = await client.get(f"{endpoint}/api/ps", timeout=3.0)
                
                warmed_names = set()
                if ps_res.status_code == 200:
                    warmed_names = {m.get("name") for m in ps_res.json().get("models", [])}
                    
                if res.status_code == 200:
                    for m in res.json().get("models", []):
                        m_name = m["name"]
                        models_list.append({
                            "model_id": f"{m_name}@{node}",
                            "model_name": m_name,
                            "endpoint": node,
                            "is_warmed": m_name in warmed_names,
                            "is_local": True
                        })
            except Exception as e:
                logger.debug(f"Telemetry: failed to poll local endpoint {node}: {e}")

        # 2. Query all configured cloud/OpenAI-compatible endpoints.
        # These are derived dynamically from INFERENCE_SERVERS (MoE Admin UI) —
        # no hardcoded URLs or tokens. Each entry has its own credential.
        for cloud in CLOUD_ENDPOINTS:
            try:
                headers = {"Authorization": f"Bearer {cloud['token']}"}
                res = await client.get(
                    f"{cloud['url']}/models", headers=headers, timeout=5.0
                )
                if res.status_code == 200:
                    for m in res.json().get("data", []):
                        m_id = m["id"]
                        m_name, m_ep = m_id.split("@", 1) if "@" in m_id else (m_id, cloud["name"])
                        models_list.append({
                            "model_id":   m_id,
                            "model_name": m_name,
                            "endpoint":   m_ep,
                            "is_warmed":  False,  # Cloud models: warmed status is opaque
                            "is_local":   False,
                        })
            except Exception as e:
                logger.debug(f"Telemetry: failed to poll cloud endpoint {cloud['name']}: {e}")

    _cluster_state_cache["models"] = models_list
    _cluster_state_cache["ts"] = now
    return models_list


async def _get_thompson_score(model: str, category: str) -> float:
    """Draws a Thompson sample for (model, category) based on Valkey performance logs."""
    if state.redis_client is None:
        return 0.5
    try:
        safe_model = re.sub(r"[^a-zA-Z0-9_\-]", "_", model)
        key = f"moe:perf:{safe_model}:{category}"
        data = await state.redis_client.hgetall(key)
        
        # Redis hgetall returns bytes keys/values
        total = int(data.get(b"total", data.get("total", 0)))
        if total < 5:  # EXPERT_MIN_DATAPOINTS = 5
            return 0.5
            
        positive = int(data.get(b"positive", data.get("positive", 0)))
        alpha = positive + 1
        beta = (total - positive) + 1
        
        # Load penalty to avoid overloading a node
        load_penalty = float(os.getenv("THOMPSON_LOAD_PENALTY", "2.0"))
        node = model.split("@")[-1] if "@" in model else ""
        node_load = 0.0
        if node:
            load_raw = await state.redis_client.get(f"moe:load:{node}")
            if load_raw:
                node_load = float(load_raw)
        beta = beta * (1.0 + load_penalty * node_load)
        
        return random.betavariate(alpha, beta)
    except Exception:
        return 0.5


async def _score_and_allocate_model(category: str, models: list[dict], model_metadata: dict, local_only: bool, complexity: str = "moderate") -> list[dict]:
    """Scores models based on parameter size, context window, warmed status, strengths, and Thompson Sampling."""
    scored_models = []
    
    # Weights for scoring formula (optimized for quality, latency-agnostic)
    W_WARMED = 0.0
    W_LOCAL = 0.1
    W_BENCH = 5.0
    W_FEEDBACK = 1.0

    for m in models:
        m_id = m["model_id"]
        is_local = m["is_local"]
        
        # Compliance Gate: block cloud endpoints completely in local_only mode
        if local_only and not is_local:
            continue

        # Fetch metadata
        meta = model_metadata.get(m_id, {})
        
        # Benchmark score: check if model specializes in target strengths, else average baseline
        bench_scores = meta.get("benchmark_scores", {})
        if category in ("code_reviewer", "creative_writer", "creative_writing") and "human_eval" in bench_scores:
            bench_val = bench_scores["human_eval"] / 100.0
        elif category == "math" and "gsm8k" in bench_scores:
            bench_val = bench_scores["gsm8k"] / 100.0
        else:
            bench_val = bench_scores.get("mmlu", 50.0) / 100.0
            
        # Draw Thompson sample for feedback rating
        t_sample = await _get_thompson_score(m_id, category)
        
        # 1. Strengths Match Bonus
        strengths = meta.get("strengths", [])
        strength_match_bonus = 0.0
        if strengths:
            # Map category to typical strengths keywords
            cat_to_strength_map = {
                "code_reviewer": "code",
                "math": "math",
                "reasoning": "reasoning",
                "creative_writer": "creative",
                "creative_writing": "creative",
                "data_analysis": "data",
                "legal_advisor": "legal",
                "mail_classify": "classification",
                "technical_support": "technical",
                "tool_agent": "reasoning"
            }
            target_str = cat_to_strength_map.get(category)
            if target_str and any(target_str in s.lower() for s in strengths):
                strength_match_bonus = 0.5  # Apply bonus for aligned expert capability

        # 2. Capacity / Size optimization based on parameter count (bonus for larger models)
        param_size = meta.get("parameter_size_b", 7.0)
        size_bonus = param_size / 70.0  # Up to +1.0 bonus for large (70B+) models

        # Score calculation
        score = (
            W_WARMED * (1.0 if m["is_warmed"] else 0.0) +
            W_LOCAL * (1.0 if is_local else 0.0) +
            W_BENCH * bench_val +
            W_FEEDBACK * t_sample +
            strength_match_bonus +
            size_bonus
        )
        
        scored_models.append({
            "model_id": m_id,
            "model_name": m["model_name"],
            "endpoint": m["endpoint"],
            "score": score,
            "context_window": meta.get("context_window", 4096)
        })
        
    # Sort models by score descending
    scored_models.sort(key=lambda x: x["score"], reverse=True)
    return scored_models


async def _match_existing_template(prompt: str) -> tuple[Optional[str], Optional[str]]:
    """Checks the semantic template cache (ChromaDB) for reusable templates."""
    if _template_collection is None:
        return None, None
    try:
        # Cosine distance similarity threshold (empirically tuned, < 0.18 is extremely close)
        results = _template_collection.query(
            query_texts=[prompt],
            n_results=1
        )
        if results and results["ids"] and results["ids"][0]:
            dist = results["distances"][0][0]
            if dist < 0.18:
                tmpl_id = results["ids"][0][0]
                tmpl_name = results["metadatas"][0][0].get("name", "")
                logger.info(f"🎯 Semantic template cache L2 hit! Reusing template '{tmpl_name}' (id={tmpl_id}, distance={dist:.4f})")
                return tmpl_id, tmpl_name
    except Exception as e:
        logger.debug(f"ChromaDB: template query error: {e}")
    return None, None


async def _save_template_to_db_and_cache(name: str, desc: str, config: dict, reasoning_trace: str, cache_query_text: str) -> str:
    """Saves the dynamic template config to Postgres and registers in ChromaDB.

    `cache_query_text` is the text indexed in ChromaDB and must match the
    shape of the text `_match_existing_template()` queries with (the raw
    prompt), so future similar prompts can retrieve this template via the
    semantic cache.
    """
    tmpl_id = f"moe-dyn-{uuid.uuid4().hex[:12]}"
    config_json = json.dumps(config)
    now = datetime_now_iso()
    
    # Save to Postgres
    dsn = MOE_USERDB_URL or ""
    if dsn:
        try:
            pool = _get_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, TRUE, %s, %s)",
                        (tmpl_id, name, f"{desc} | Reasoning: {reasoning_trace}", config_json, now, now)
                    )
            logger.debug(f"Registered template in DB: {tmpl_id}")
        except Exception as e:
            logger.error(f"❌ Failed to insert template into DB: {e}")

        # Seed dynamic_template_feedback_log so a later user rating
        # (routes/feedback.py -> update_dynamic_template_feedback_rating)
        # has a row to update.
        try:
            await log_dynamic_template_feedback(
                template_id=tmpl_id,
                prompt=cache_query_text,
                config_json=config_json,
                latency_ms=None,
                tokens_used=None,
            )
        except Exception as e:
            logger.error(f"❌ Failed to seed dynamic_template_feedback_log: {e}")

    # Register in ChromaDB Template Cache
    if _template_collection is not None:
        try:
            _template_collection.add(
                ids=[tmpl_id],
                documents=[cache_query_text],  # Same text shape as _match_existing_template()'s query
                metadatas=[{"name": name, "reasoning": reasoning_trace}]
            )
            logger.debug(f"Semantic Cache: Registered template {tmpl_id} in ChromaDB")
        except Exception as e:
            logger.error(f"❌ Failed to register template in ChromaDB cache: {e}")
            
    return tmpl_id


def _is_local_url(url: str) -> bool:
    """Determine if a URL points to a local or internal network endpoint."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        netloc = parsed.netloc or parsed.path
        host = netloc.split(":")[0].strip().lower()
        if not host:
            return False
        
        # Public commercial LLM APIs are never local
        external_domains = (
            "openai.com", "deepseek.com", "anthropic.com", "groq.com", "sambanova.ai",
            "cerebras.ai", "nvidia.com", "openrouter.ai", "mistral.ai", "deepinfra.com",
            "huggingface.co", "cohere.com", "together.xyz", "ai21.com", "google.com",
            "googleapis.com", "vertex"
        )
        if any(d in host for d in external_domains):
            return False
            
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
            
        import ipaddress
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback
        except ValueError:
            pass
            
        if host.endswith(".local") or host.endswith(".lan") or host.endswith(".home") or host.endswith(".internal"):
            return True
            
        if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172.16.") or host.startswith("172.17.") or host.startswith("172.18.") or host.startswith("172.19.") or host.startswith("172.20.") or host.startswith("172.21.") or host.startswith("172.22.") or host.startswith("172.23.") or host.startswith("172.24.") or host.startswith("172.25.") or host.startswith("172.26.") or host.startswith("172.27.") or host.startswith("172.28.") or host.startswith("172.29.") or host.startswith("172.30.") or host.startswith("172.31."):
            return True
            
        if "." not in host:
            return True
    except Exception:
        pass
    return False


async def get_dynamic_template(
    prompt: str,
    local_only: bool = False,
    user_connections: Optional[dict] = None,
    global_only: bool = False,
    user_conns_only: bool = False,
) -> Optional[dict]:
    """Main service entrypoint: matching, ONNX routing, cluster mapping, scoring and allocation.

    Args:
        prompt:           The user's raw message.
        local_only:       When True, only local (non-cloud) endpoints are considered.
        user_connections: User's private connections dictionary.
        global_only:      When True, restrict to global admin-defined endpoints.
        user_conns_only:  When True, restrict to user-created connections.
    """
    global _onnx_session
    if _onnx_session is None:
        init_router()
    if _onnx_session is None:
        return None

    # 1. Redundancy Detector (ChromaDB cache hit check)
    tmpl_id, tmpl_name = await _match_existing_template(prompt)
    if tmpl_id:
        # Fetch matching template from DB
        dsn = MOE_USERDB_URL or ""
        if dsn:
            try:
                pool = _get_pool()
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT config_json FROM admin_expert_templates WHERE id=%s", (tmpl_id,))
                        row = await cur.fetchone()
                        if row:
                            cached_config = json.loads(row["config_json"])
                            # config_json was stored before "id"/"name" were added to the
                            # caller's dict (see _save_template_to_db_and_cache call site) —
                            # restore them so downstream tracing (chat.py: dynamic_tmpl["id"]) works.
                            cached_config["id"] = tmpl_id
                            cached_config["name"] = tmpl_name
                            return cached_config
            except Exception as e:
                logger.error(f"Failed to fetch cached template: {e}")

    # 2. Run Gating Classifier ONNX Inference
    if _embedding_function is None:
        return None
        
    try:
        # Embed user prompt using the local MiniLM function
        # Embeddings return a list of lists of floats
        embeds = _embedding_function([prompt])
        embedding = np.array(embeds, dtype=np.float32) # shape [1, 384]
        
        # Run inference session
        outputs = _onnx_session.run(
            ["experts", "complexity", "gates"],
            {"input_embedding": embedding}
        )
        
        # Extract logits
        expert_logits = outputs[0][0]      # shape [14]
        complexity_logits = outputs[1][0]  # shape [4]
        gate_logits = outputs[2][0]        # shape [2]
        
        # Apply Sigmoid to experts and gates
        expert_probs = 1.0 / (1.0 + np.exp(-expert_logits))
        gate_probs = 1.0 / (1.0 + np.exp(-gate_logits))
        
        # Get active experts (prob >= 0.5)
        active_expert_indices = np.where(expert_probs >= 0.5)[0]
        active_experts = [EXPERT_CLASSES[idx] for idx in active_expert_indices]
        
        if not active_experts:
            active_experts = ["general"]
            
        # Get complexity (Argmax)
        complexity_idx = np.argmax(complexity_logits)
        complexity = COMPLEXITY_CLASSES[complexity_idx]
        
        # Get active gates
        enable_web_research = bool(gate_probs[0] >= 0.5)
        enable_graphrag = bool(gate_probs[1] >= 0.5)
        
        # Override gates for trivial/memory-recall
        if complexity in ("trivial", "memory_recall"):
            enable_web_research = False
            enable_graphrag = False
            
    except Exception as e:
        logger.error(f"❌ Sovereign Router ONNX inference failed: {e}")
        return None

    # 3. Model Scoring and Allocation
    try:
        models = []
        if not user_conns_only:
            global_models = await _get_cluster_state()
            if global_models:
                models.extend(global_models)

        if not global_only and user_connections:
            user_pool = []
            for conn_name, conn_cfg in user_connections.items():
                for mc in conn_cfg.get("models_cache", []):
                    raw_id = mc.get("id") if isinstance(mc, dict) else str(mc)
                    base = raw_id.rsplit("@", 1)[0] if "@" in (raw_id or "") else (raw_id or "")
                    if not base:
                        continue
                    user_pool.append({
                        "model_id":   f"{base}@{conn_name}",
                        "model_name": base,
                        "endpoint":   conn_name,
                        "is_warmed":  False,
                        "is_local":   _is_local_url(conn_cfg.get("url", "")),
                    })
            if user_pool:
                models.extend(user_pool)
                logger.debug("Dynamic routing: added %d models from user connections", len(user_pool))

        if not models:
            logger.warning("Dynamic routing: no active models discovered on endpoints.")
            return None

        # Fetch model metadata profiles from DB
        model_metadata = {}
        dsn = MOE_USERDB_URL or ""
        if dsn:
            pool = _get_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT model_id, context_window, benchmark_scores, parameter_size_b, family, strengths FROM model_metadata")
                    rows = await cur.fetchall()
                    for r in rows:
                        if isinstance(r, dict):
                            m_id = r["model_id"]
                            model_metadata[m_id] = {
                                "context_window": r["context_window"],
                                "benchmark_scores": r["benchmark_scores"] if isinstance(r["benchmark_scores"], dict) else json.loads(r["benchmark_scores"] or "{}"),
                                "parameter_size_b": float(r["parameter_size_b"] or 7.0),
                                "family": r["family"] or "other",
                                "strengths": (r["strengths"] if isinstance(r["strengths"], list) else (json.loads(r["strengths"] or "[]") if isinstance(r["strengths"], str) else r["strengths"])) or []
                            }
                        else:
                            model_metadata[r[0]] = {
                                "context_window": r[1],
                                "benchmark_scores": r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}"),
                                "parameter_size_b": float(r[3] or 7.0) if len(r) > 3 else 7.0,
                                "family": (r[4] or "other") if len(r) > 4 else "other",
                                "strengths": (r[5] if isinstance(r[5], list) else (json.loads(r[5] or "[]") if isinstance(r[5], str) else r[5])) if len(r) > 5 else []
                            }

        # Select Planner and Judge models dynamically using metadata
        planner_choices = [m for m in models if m["is_local"]] if local_only else models
        if not planner_choices:
            planner_choices = models
            
        planner_meta_choices = []
        for m in planner_choices:
            meta = model_metadata.get(m["model_id"], {})
            param_size = meta.get("parameter_size_b", 7.0)
            family = (meta.get("family") or "").lower()
            planner_meta_choices.append((m, param_size, family))

        # 1. Select Planner: prioritize largest available models for complex task planning
        planner_meta_choices.sort(key=lambda x: x[1], reverse=True)  # Sort by param_size descending
        best_planners = [
            m for m, size, fam in planner_meta_choices
            if size >= 30.0 or fam in ("qwen", "llama", "deepseek")
        ]
        planner_model = best_planners[0]["model_id"] if best_planners else planner_meta_choices[0][0]["model_id"]
        
        # 2. Select Judge: select the absolute largest and most capable model for quality synthesis
        judge_meta_choices = list(planner_meta_choices)
        judge_meta_choices.sort(key=lambda x: x[1], reverse=True)  # Sort by param_size descending
        judge_model = judge_meta_choices[0][0]["model_id"]

        # Compile expert models configurations
        experts_config = {}
        justification_trace = f"Complexity={complexity}, WebSearch={enable_web_research}, GraphRAG={enable_graphrag}. "

        for exp in active_experts:
            # Allocate models for this expert category
            allocated = await _score_and_allocate_model(exp, models, model_metadata, local_only, complexity)
            if not allocated:
                # If local_only is active but no local model is available, fall back to whatever is available
                allocated = await _score_and_allocate_model(exp, models, model_metadata, False, complexity)
                
            if allocated:
                models_cfg = []
                # Add Primary
                primary = allocated[0]
                models_cfg.append({
                    "model": primary["model_name"],
                    "endpoint": primary["endpoint"],
                    "role": "primary"
                })
                justification_trace += f"{exp} (Primary={primary['model_id']}, score={primary['score']:.2f}). "
                
                # Add Fallback (if second best model is available)
                if len(allocated) > 1:
                    fallback = allocated[1]
                    models_cfg.append({
                        "model": fallback["model_name"],
                        "endpoint": fallback["endpoint"],
                        "role": "fallback"
                    })
                
                # Context window mapping
                ctx = primary["context_window"]
                
                # Custom system prompts for experts
                sys_prompt = f"You are a specialized expert in {exp}."
                if exp == "code_reviewer":
                    sys_prompt = "You are an expert software engineer and code reviewer. Analyze the code, find bugs, and suggest clean improvements."
                elif exp == "technical_support":
                    sys_prompt = "You are a technical support expert. Provide clear, precise, and actionable answers to technical questions."
                elif exp == "math":
                    sys_prompt = "You are a mathematics expert. Provide rigorous step-by-step mathematical reasoning and calculations."
                elif exp == "legal_advisor":
                    sys_prompt = "You are a legal advisor expert. Analyze the legal context, reference relevant laws or paragraphs precisely, and provide expert legal summaries."
                elif exp == "research":
                    sys_prompt = "You are a research expert. Analyze scientific literature, academic papers, and reference reliable sources clearly."
                elif exp in ("creative_writer", "creative_writing"):
                    sys_prompt = "You are a creative writer. Craft engaging, expressive, and high-quality creative text matching the requested style."
                elif exp == "data_analysis":
                    sys_prompt = "You are a data analyst. Interpret data tables, statistics, and trends, and explain findings with clear quantitative insights."
                elif exp == "reasoning":
                    sys_prompt = "You are a logical reasoning expert. Break down complex arguments, evaluate premises, and explain logical transitions step-by-step."
                elif exp == "science":
                    sys_prompt = "You are a scientific expert. Apply physics, chemistry, biology, or engineering principles to explain concepts rigorously."
                elif exp == "tool_agent":
                    sys_prompt = "You are an agent expert in calling tools and APIs. Coordinate tool outputs to solve the user's request."
                elif exp == "precision_tools":
                    sys_prompt = "You are a calculations expert. Perform precise formatting, unit conversion, date difference calculation, and exact arithmetic."
                elif exp == "mail_classify":
                    sys_prompt = "You are an email classification expert. Categorize incoming emails, extract key metadata, and draft professional responses."

                experts_config[exp] = {
                    "system_prompt": sys_prompt,
                    "context_window": ctx,
                    "models": models_cfg
                }

        # Build complete dynamic template configuration
        planner_ctx = model_metadata.get(planner_model, {}).get("context_window", 0) or 0
        judge_ctx = model_metadata.get(judge_model, {}).get("context_window", 0) or 0
        
        # Context-aware system prompts for planner and judge
        planner_prompt = f"You are a specialized planner model in a Mixture of Experts (MoE) system. Coordinate planning, tool execution, and task delegation for the following expert areas: {', '.join(active_experts)}."
        judge_prompt = f"You are a specialized synthesis judge. Consolidate and merge responses from the following expert models: {', '.join(active_experts)}. Resolve contradictions using paraconsistent logic rules and output a unified, high-quality answer."
        
        template_config = {
            "planner_model": planner_model,
            "judge_model": judge_model,
            "planner_prompt": planner_prompt,
            "judge_prompt": judge_prompt,
            "planner_num_ctx": planner_ctx,
            "judge_num_ctx": judge_ctx,
            "enable_cache": True,
            "enable_graphrag": enable_graphrag,
            "enable_web_research": enable_web_research,
            "experts": experts_config
        }
        
        # Save to Postgres and cache in ChromaDB
        hash_suffix = uuid.uuid4().hex[:6]
        template_name = f"Dynamic Template {hash_suffix}"
        template_desc = f"Dynamic gating template compiled for prompt: {prompt[:80]}..."
        
        tmpl_id = await _save_template_to_db_and_cache(
            name=template_name,
            desc=template_desc,
            config=template_config,
            reasoning_trace=justification_trace,
            cache_query_text=prompt,
        )
        
        # Add metadata for orchestrator tracing
        template_config["id"] = tmpl_id
        template_config["name"] = template_name
        template_config["description"] = template_desc
        template_config["reasoning_trace"] = justification_trace
        
        logger.info(f"✨ Dynamic template compiled and registered: '{template_name}' (id={tmpl_id})")
        return template_config
        
    except Exception as e:
        logger.error(f"❌ Failed to score and allocate models for dynamic template: {e}", exc_info=True)
        return None

def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

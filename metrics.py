"""
metrics.py — Prometheus metrics registry for MoE Sovereign.

All PROM_* objects are created once at import time (Prometheus requirement:
duplicate metric names raise a ValueError). Import this module everywhere
metrics are needed instead of re-creating them.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Request & token accounting
# ---------------------------------------------------------------------------

PROM_TOKENS          = Counter('moe_tokens_total',             'Total tokens processed',      ['model', 'token_type', 'node', 'user_id'])
PROM_REQUESTS        = Counter('moe_requests_total',           'Total requests',              ['mode', 'cache_hit', 'user_id'])
PROM_BUDGET_EXCEEDED = Counter('moe_budget_exceeded_total',    'Budget limit exceeded',       ['user_id', 'limit_type'])
PROM_EXPERT_CALLS    = Counter('moe_expert_calls_total',       'Expert model calls',          ['model', 'category', 'node'])
PROM_CONFIDENCE      = Counter('moe_expert_confidence_total',  'Expert confidence level',     ['level', 'category'])
PROM_CACHE_HITS      = Counter('moe_cache_hits_total',         'Cache hits')
PROM_CACHE_MISSES    = Counter('moe_cache_misses_total',       'Cache misses')
PROM_RESPONSE_TIME   = Histogram('moe_response_duration_seconds', 'Response duration',        ['mode'],
                                 buckets=[1, 2, 5, 10, 20, 30, 60, 120])
PROM_SELF_EVAL       = Histogram('moe_self_eval_score',        'Self-evaluation score',       buckets=[1,2,3,4,5,6])
PROM_FEEDBACK        = Histogram('moe_feedback_score',         'User feedback score',         buckets=[1,2,3,4,5,6])
PROM_COMPLEXITY      = Counter('moe_complexity_routing_total', 'Request complexity routing',  ['level'])

# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

PROM_CHROMA_DOCS          = Gauge('moe_chroma_documents_total',          'Documents in ChromaDB cache')
PROM_GRAPH_ENTITIES       = Gauge('moe_graph_entities_total',            'Entities in Neo4j')
PROM_GRAPH_RELATIONS      = Gauge('moe_graph_relations_total',           'Relations in Neo4j')
PROM_GRAPH_DENSITY        = Gauge('moe_graph_density_ratio',             'Relations per entity (graph connectivity measure)')
PROM_SYNTHESIS_NODES      = Gauge('moe_graph_synthesis_nodes_total',     'Synthesis-Nodes in Neo4j (:Synthesis)')
PROM_FLAGGED_RELS         = Gauge('moe_graph_flagged_relations_total',   'Flagged relations in Neo4j (r.flagged=true)')
PROM_SYNTHESIS_CREATED    = Counter('moe_synthesis_persisted_total',     'Synthesis nodes persisted', ['domain', 'insight_type'])
PROM_LINTING_RUNS         = Counter('moe_linting_runs_total',            'Total graph linting runs')
PROM_LINTING_ORPHANS      = Counter('moe_linting_orphans_deleted_total', 'Orphaned nodes deleted by linting')
PROM_LINTING_CONFLICTS    = Counter('moe_linting_conflicts_resolved_total', 'Conflicts resolved by linting')
PROM_LINTING_DECAY        = Counter('moe_linting_decay_deleted_total',   'Relations deleted by confidence decay')
PROM_QUARANTINE_ADDED     = Counter('moe_quarantine_added_total',        'Triples quarantined by blast-radius check')

# ---------------------------------------------------------------------------
# Inference server live status
# ---------------------------------------------------------------------------

PROM_ACTIVE_REQUESTS         = Gauge('moe_active_requests',                    'Active requests in progress')
PROM_SERVER_UP               = Gauge('moe_inference_server_up',                'Inference server reachable (1=up, 0=down)', ['server'])
PROM_SERVER_MODELS           = Gauge('moe_available_models_total',             'Available models per inference server',     ['server'])
PROM_SERVER_LOADED_MODELS    = Gauge('moe_inference_server_loaded_models',     'Currently loaded models in VRAM (Ollama /api/ps)', ['server'])
PROM_SERVER_VRAM_BYTES       = Gauge('moe_inference_server_vram_bytes',        'Total VRAM bytes used by loaded models',    ['server'])
PROM_SERVER_MODEL_VRAM_BYTES = Gauge('moe_inference_server_model_vram_bytes',  'VRAM bytes used by a specific loaded model', ['server', 'model'])
PROM_PLANNER_PATS            = Gauge('moe_planner_patterns_total',             'Successful planner patterns')
PROM_ONTOLOGY_GAPS           = Gauge('moe_ontology_gaps_total',                'Ontology gap terms')
PROM_ONTOLOGY_ENTS           = Gauge('moe_ontology_entities_total',            'Ontology entities (static)')

# ---------------------------------------------------------------------------
# Tool model calls
# ---------------------------------------------------------------------------

PROM_TOOL_CALL_DURATION = Histogram(
    'moe_tool_call_duration_seconds', 'Duration of individual tool model calls',
    ['node', 'model', 'phase'],
    buckets=[1, 2, 5, 10, 20, 30, 60, 120, 300, 600],
)
PROM_TOOL_TIMEOUTS      = Counter('moe_tool_call_timeout_total',        'Timeout errors in tool model calls',   ['node', 'model'])
PROM_TOOL_FORMAT_ERRORS = Counter('moe_tool_call_format_errors_total',  'Malformed tool call responses',        ['node', 'model', 'format'])
PROM_TOOL_CALL_SUCCESS  = Counter('moe_tool_call_success_total',        'Successful tool call responses',       ['node', 'model'])

# ---------------------------------------------------------------------------
# RL flywheel, context window, memory
# ---------------------------------------------------------------------------

PROM_HISTORY_COMPRESSED     = Counter('moe_history_compressed_total',     'History compression events (turns truncated)')
PROM_HISTORY_UNLIMITED      = Counter('moe_history_unlimited_total',      'Requests with compression disabled (per-template)')
PROM_SEMANTIC_MEMORY_STORED = Counter('moe_semantic_memory_stored_total', 'Conversation turns stored in Tier-2 semantic memory')
PROM_SEMANTIC_MEMORY_HITS   = Counter('moe_semantic_memory_hits_total',   'Warm context blocks injected from semantic memory')
PROM_CORRECTIONS_STORED     = Counter('moe_corrections_stored_total',     'Correction memory entries written to Neo4j',          ['source'])
PROM_CORRECTIONS_INJECTED   = Counter('moe_corrections_injected_total',   'Correction memory entries injected into expert prompts', ['category'])
PROM_JUDGE_REFINED          = Counter('moe_judge_refinement_total',       'Judge refinement rounds executed',    ['outcome'])
PROM_EXPERT_FAILURES        = Counter('moe_expert_failures_total',        'Expert invocation failures (VRAM, timeout, error)', ['model', 'reason'])

# ---------------------------------------------------------------------------
# Thompson sampling
# ---------------------------------------------------------------------------

PROM_THOMPSON = Histogram(
    "moe_thompson_sample", "Thompson-sampled expert score",
    buckets=[.1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0],
)

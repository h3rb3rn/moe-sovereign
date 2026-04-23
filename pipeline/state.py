"""
pipeline/state.py — Shared LangGraph state contract for MoE Sovereign.

AgentState is the single TypedDict that flows through every node in the
pipeline. Treat it as the public API between nodes: if a field appears here,
it is intentional; if a node needs new data, it must be declared here first.

Field groups (in declaration order):
    1. Request identity & auth       — who asked, with what permissions
    2. Planning & routing            — what the planner decided
    3. Node results                  — outputs of parallel worker nodes
    4. Token accounting              — accumulated across all LLM calls
    5. Conversation context          — history, reasoning, examples
    6. Template configuration        — per-template toggles and overrides
    7. History limits                — per-template conversation pruning
    8. LLM overrides                 — per-template judge / planner models
    9. Routing & complexity flags    — skip signals from complexity estimator
   10. Causal-path & explainability  — audit trail for compliance
   11. Agentic loop                  — re-planning state across iterations
   12. Working memory hub            — structured facts + tool call log
"""
from __future__ import annotations

import operator
from typing import Annotated, Dict, List, TypedDict


class AgentState(TypedDict):
    # ── 1. Request identity & auth ────────────────────────────────────────────
    input: str                          # Raw user query text
    response_id: str                    # Unique chat ID used for feedback tracking
    mode: str                           # "default"|"code"|"concise"|"agent"|"agent_orchestrated"|"research"|"report"|"plan"
    user_id: str                        # Authenticated user identifier, or "anon"
    api_key_id: str                     # API key ID written to usage_log
    system_prompt: str                  # System message from client (e.g. coding-agent file context)
    template_name: str                  # User-facing template name; empty for native/mode requests
    user_permissions: dict              # Deserialized permissions_json from Valkey
    user_experts: dict                  # Per-template expert config resolved from template
    tenant_ids: List[str]               # Graph tenant IDs for RBAC-scoped Neo4j queries

    # ── 2. Planning & routing ─────────────────────────────────────────────────
    plan: List[Dict[str, str]]          # [{task, category, mcp_tool?, mcp_args?, search_query?, metadata_filters?}]
    complexity_level: str               # "trivial"|"moderate"|"complex" — set by planner
    direct_expert: str                  # If set by semantic router, planner LLM is bypassed
    pending_reports: List[str]          # Progress messages collected before pipeline starts streaming
    metadata_filters: Dict              # Optional domain filters from planner for scoped ChromaDB retrieval

    # ── 3. Node results ───────────────────────────────────────────────────────
    # Annotated[list, operator.add] means LangGraph accumulates values from
    # parallel fan-out branches by concatenating rather than overwriting.
    expert_results: Annotated[list, operator.add]
    expert_models_used: Annotated[list, operator.add]   # "model::category" strings per expert call
    web_research: str                   # Formatted citations from SearXNG (research_node)
    cached_facts: str                   # L1 ChromaDB soft-cache hit content
    cache_hit: bool                     # True = L0 Redis exact match; pipeline short-circuits to merger
    math_result: str                    # SymPy calculation output (math_node)
    mcp_result: str                     # Precision tool results (mcp_node)
    graph_context: str                  # Neo4j retrieved context, possibly compressed (graph_rag_node)
    final_response: str                 # Merger's synthesised answer (overwritten by critic if edited)
    reasoning_trace: str                # Chain-of-thought from thinking_node
    soft_cache_examples: str           # Few-shot similar Q&A pairs injected into planner/merger context
    images: List[Dict]                  # Extracted image payloads for vision-capable experts

    # ── 4. Token accounting ───────────────────────────────────────────────────
    # Both fields accumulate across all parallel node calls via operator.add.
    prompt_tokens: Annotated[int, operator.add]
    completion_tokens: Annotated[int, operator.add]

    # ── 5. Conversation context ───────────────────────────────────────────────
    chat_history: List[Dict]            # Prior user/assistant turns (truncated per history limits below)
    output_skill_body: str              # Resolved skill body for output formatting (planner suggestion)
    provenance_sources: List[Dict]      # [REF:entity] tags extracted from merger output for graph ingest

    # ── 6. Template feature toggles ───────────────────────────────────────────
    enable_cache: bool                  # Allow L1 ChromaDB cache reads/writes
    enable_graphrag: bool               # Allow Neo4j knowledge graph queries
    enable_web_research: bool           # Allow SearXNG web search
    graphrag_max_chars: int             # Hard char budget for graph_context (0 = derive from judge model context window)
    no_cache: bool                      # If True, bypass both L0 (Redis) and L1 (ChromaDB) entirely

    # ── 7. History limits ─────────────────────────────────────────────────────
    history_max_turns: int              # Max conversation turns kept (0 = global default, -1 = unlimited)
    history_max_chars: int              # Max total history chars kept (0 = global default, -1 = unlimited)

    # ── 8. LLM overrides (per-template) ──────────────────────────────────────
    planner_prompt: str                 # Custom planner system prompt; empty = DEFAULT_PLANNER_ROLE
    judge_prompt: str                   # Custom judge/merger system prompt; empty = mode config default
    judge_model_override: str           # Override global JUDGE_MODEL for this template
    judge_url_override: str             # Override global JUDGE_URL
    judge_token_override: str           # Override global JUDGE_TOKEN
    planner_model_override: str         # Override global PLANNER_MODEL
    planner_url_override: str           # Override global PLANNER_URL
    planner_token_override: str         # Override global PLANNER_TOKEN

    # ── 9. Routing & complexity flags ─────────────────────────────────────────
    skip_research: bool                 # Skip research_node (set by complexity estimator for trivial queries)
    skip_thinking: bool                 # Skip thinking_node (set by complexity estimator)
    query_temperature: float            # Adaptive sampling temperature: math=0.05, creative=0.70, default=0.20
    cost_tier: str                      # "local_7b"|"mid_tier"|"full" — cost classifier output
    force_tier1: bool                   # Skip Tier-2 experts to reduce cost for trivial tasks

    # ── 10. Causal-path & explainability ──────────────────────────────────────
    # These fields form an audit trail for compliance and XAI dashboards.
    graphrag_entities: List[Dict]       # Neo4j entities used: [{name, type, confidence, relations_used}]
    judge_reason: str                   # Human-readable explanation of why the judge intervened
    judge_before_after: Dict            # {before_score, after_score, delta} from judge refinement loop
    expert_inputs: Dict                 # {expert_name: {tokens_out, latency_ms}} per-expert call stats

    # ── 11. Agentic re-planning loop ──────────────────────────────────────────
    # When agentic_max_rounds > 0, the merger can trigger a second planning pass
    # by setting agentic_gap. The loop exits when the gap is empty or the round
    # limit is reached.
    agentic_iteration: int              # Current loop iteration (0 = first pass)
    agentic_max_rounds: int             # Max allowed iterations from template config (0 = disabled)
    agentic_history: list               # [{iteration, findings, gap}] accumulated across rounds
    agentic_gap: str                    # What information is still missing — output of gap-detection LLM call
    attempted_queries: list             # All search queries already tried [{query, result_quality}] — injected into re-planner to prevent repetition
    search_strategy_hint: str          # Suggested next search approach from gap-detection LLM (e.g. "try site:github.com")
    discovered_domains: list           # Authoritative domains found in web results [{domain, context}] — offered to re-planner for targeted follow-up

    # ── 12. Working memory hub ────────────────────────────────────────────────
    # Structured persistence layer for tool results across agentic iterations.
    # Unlike agentic_history (unstructured prose), working_memory is keyed and
    # queryable so later iterations can reference specific facts by name.
    working_memory: dict                # {key: {value, source, confidence, ts}}
    tool_calls_log: list                # [{tool, args, result, status, ts}] — full tool invocation trace
    tool_failures: list                 # Subset of tool_calls_log: only failed calls; injected into planner prompt

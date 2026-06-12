# System Data Flow & Caching

This section describes the detailed request sequence, caching tiers, and feedback telemetry loops in the MoE Sovereign platform.

---

## 1. Normal Request Flow (With IMoE Gating)

The following sequence diagram outlines a normal request flow that misses the L1 semantic response cache and goes through the **IMoE Gating Layer** to compile a dynamic template, followed by parallel expert execution and judge synthesis.

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI :8002
    participant Cache as ChromaDB/Valkey
    participant ONNX as ONNX Router (CPU)
    participant Planner as Planner LLM
    participant Experts as Expert Workers (parallel)
    participant Graph as Neo4j GraphRAG
    participant Merger as Merger LLM
    participant Kafka

    Client->>+API: POST /v1/chat/completions
    
    %% L1 Cache Check
    API->>+Cache: L1 Response Cache Lookup (ChromaDB)
    Cache-->>-API: Miss (distance > 0.15)
    
    %% IMoE Gating Layer
    API->>+Cache: L4 Template Cache Lookup (ChromaDB)
    alt Template Cache Hit (distance < 0.18)
        Cache-->>API: Reuse Cached Template Configuration
    else Template Cache Miss
        API->>+ONNX: sovereign_router.onnx classifier (<5ms)
        ONNX-->>-API: Category scores, complexity, retrieval gates
        API->>+Cache: Fetch Thompson scores & VRAM metadata
        Cache-->>-API: Model metrics & Beta values
        API->>API: Compile Dynamic Template JSON
        API->>Cache: Save Template (Postgres & ChromaDB Template Cache)
    end
    
    %% L2 Plan Cache
    API->>+Cache: L2 Plan Cache Lookup (Valkey)
    alt Plan Cache Hit (TTL 30 min)
        Cache-->>API: Reuse Task Plan
    else Plan Cache Miss
        API->>+Planner: Decompose request (complexity-informed)
        Planner-->>-API: Plan [{task, category, metadata_filters?}]
        API->>Cache: Write plan to Valkey (TTL 30 min)
    end
    
    %% Parallel Fan-out
    par Fan-Out (parallel)
        API->>+Experts: Expert Workers (T1 -> T2 if needed)
        API->>Experts: MCP deterministic tools (if planned)
        API->>Experts: SearXNG Web Research (if planned)
        API->>+Graph: L3 GraphRAG Cache (Valkey)
        alt GraphRAG Cache Hit (TTL 1h)
            Graph-->>API: Return cached Graph context
        else GraphRAG Cache Miss
            Graph->>+Graph: Neo4j 2-hop traversal + CAG gate check
            Graph->>Cache: Filtered ChromaDB query (using metadata_filters)
            Cache-->>Graph: [Domain-Filtered Memory]
            Graph-->>-API: Return aggregated GraphRAG context
        end
    end
    Experts-->>-API: Response content + expert confidence
    
    %% Response Synthesis
    API->>+Merger: Synthesize (Pre-flight context budget check)
    Merger-->>-API: Final Response content
    
    %% Post-request write-backs
    API->>Cache: Write response to L1 ChromaDB Cache
    API->>+Kafka: Publish moe.requests (audit) & moe.ingest (GraphRAG queue)
    Kafka-->>-API: Ack
    API-->>-Client: SSE Stream Response
```

---

## 2. L1 Cache Hit Fast Path

If an identical or semantically equivalent query has been resolved recently, the system skips all LLMs and database lookups entirely, returning the answer in less than 50 ms.

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI :8002
    participant Chroma as ChromaDB

    Client->>+API: POST /v1/chat/completions
    API->>+Chroma: L1 Response Cache Lookup (cosine distance)
    Chroma-->>-API: Hit! (distance < 0.15)
    API-->>-Client: ⚡ Direct Response (no LLM calls)
```

---

## 3. Telemetry & Thompson Feedback Loop

When users submit ratings, the data propagates to update expert selections dynamically. High-scoring models are preferred in subsequent runs, whereas low-scoring ones trigger fallback routes.

```mermaid
flowchart TD
    User["👤 User Feedback\nPOST /v1/feedback (1–5)"] --> FeedAPI["FastAPI /v1/feedback"]
    
    FeedAPI --> Valkey[("Valkey\nmoe:perf:model:category")]
    Valkey --> Thompson["Thompson Sampling\nBeta-Bernoulli draw (live)"]
    Thompson --> IMoE["IMoE Gating Layer\n(expert selection)"]
    
    FeedAPI --> FewShot[("Valkey Few-Shot Cache\nmoe:few_shot:category")]
    FewShot --> ExpertPrompt["Expert System Prompt\n(Few-shot examples injected)"]
    
    FeedAPI --> Postgres[("Postgres\ndynamic_template_feedback_log")]
    FeedAPI --> RetrainDataset[("logs/retraining_dataset.jsonl\nPositive / Negative samples")]
```

---

## 4. GraphRAG Ingest Pipeline

The response content is checked for insights to ingest back into Neo4j in the background, reinforcing the knowledge base over time.

```mermaid
flowchart TD
    Resp["Merger LLM Response"] --> Synth{"SYNTHESIS_INSIGHT\npresent?"}
    
    Synth -->|Yes| Strip["Strip tag\nParse synthesis JSON"]
    Synth -->|No| KeyHeuristic["Keyword Heuristic\n(factual vs procedural)"]
    
    Strip --> KeyHeuristic
    
    KeyHeuristic --> Kafka[("Kafka topic: moe.ingest")]
    
    Kafka --> Consumer["Kafka Consumer\n(Decoupled async worker)"]
    
    Consumer --> IngestLLM["Graph Ingest LLM\n(Semaphore concurrent limit = 2)"]
    
    IngestLLM --> IngestGraph[("Neo4j Database\nMERGE node + relations\n(tagged with expert_domain)")]
```

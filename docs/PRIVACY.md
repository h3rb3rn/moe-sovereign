# Data Handling & Privacy

MoE Sovereign is designed for **self-hosted, on-premises deployment**. The operator who deploys this system is responsible for compliance with applicable data protection laws (including GDPR where relevant).

This document describes what data the system processes and where it is stored.

---

## Data Flows

### 1. Conversation Data (Kafka — audit log)

Every request processed by the orchestrator is written to the Kafka topic `moe.requests`:

| Field | Content | Retention |
|---|---|---|
| `input` | First 300 characters of user query | 7 days (configurable) |
| `answer` | First 500 characters of AI response | 7 days |
| `expert_models_used` | Which LLM experts were invoked | 7 days |
| `cache_hit` | Whether the response came from cache | 7 days |
| `ts` | Request timestamp | 7 days |

Full inputs and responses are also written to `moe.ingest` for knowledge graph enrichment.

**Kafka retention** is configured via `KAFKA_RETENTION_MS` and `KAFKA_RETENTION_BYTES` in `docker-compose.yml`. Default: 7 days / 512 MB per topic.

### 2. Knowledge Graph (Neo4j — persistent)

The GraphRAG pipeline extracts semantic triples from AI responses and stores them permanently in Neo4j. This may include:
- Factual statements derived from user queries
- Concepts and relationships mentioned in responses

There is no automatic expiry for Neo4j data. Operators can manually delete nodes using the Neo4j Browser at `http://localhost:7474`.

### 3. Semantic Cache (ChromaDB — TTL-based)

AI responses are cached in ChromaDB for fast repeated lookup. Stored data:
- Embedding vector of the user query
- Full AI response text (minimum 50 characters)
- Request metadata (category, models used, timestamp)

ChromaDB does not implement automatic TTL expiry by default. Data persists until manually deleted or the volume is cleared.

### 4. User Database (PostgreSQL — persistent)

The Admin UI stores the following per user account:
- Username (hashed for API keys)
- Bcrypt-hashed password
- API key hash (not the key itself)
- API usage counters per model

No conversation content is stored in PostgreSQL.

### 5. Session Data (Valkey — volatile)

LangGraph checkpoint state and performance scores are stored in Valkey with no guaranteed persistence across container restarts (unless `appendonly yes` is configured). This data contains intermediate reasoning state for ongoing requests.

---

## Who Can Access Data

| Role | Access |
|---|---|
| **Admins** | Full access to Admin UI, Grafana dashboards, Dozzle logs, Neo4j Browser |
| **Users** | Own conversation history via the chat interface only |
| **No one** | Conversation data is never sent to external services (unless SearXNG web search is invoked by the user) |

---

## Data Deletion

The system does not currently provide a self-service data deletion interface. Operators can delete data manually:

```bash
# Clear ChromaDB cache
sudo docker compose exec chromadb-vector python -c "import chromadb; chromadb.HttpClient().reset()"

# Clear Neo4j knowledge graph (destructive — removes all nodes)
sudo docker compose exec neo4j-knowledge cypher-shell -u neo4j -p $NEO4J_PASS "MATCH (n) DETACH DELETE n"

# Clear Valkey checkpoint data
sudo docker compose exec terra_cache valkey-cli -a $REDIS_PASSWORD FLUSHALL
```

Kafka data expires automatically after the configured retention period (default 7 days).

---

## GDPR Considerations

If this system is deployed within the European Union and processes personal data (e.g., names, contact details, health information in queries), the **operator** is the data controller under GDPR. Responsibilities include:

- Documenting the legal basis for processing
- Informing end users about data collection and retention
- Providing mechanisms for data access and erasure requests
- Configuring appropriate retention periods for Kafka, ChromaDB, and Neo4j

This software does not include a user-facing privacy notice or consent mechanism — the operator must implement these according to their specific deployment context.

### Automated Scrubbing Limitations

The built-in [Privacy Scrubber](federation/trust.md#privacy-scrubber) handles pattern-identifiable data (IP addresses, email addresses, API keys, file paths). It does **not** constitute a complete GDPR compliance solution. Operators are responsible for:

- Ensuring no conversation transcripts or user-linked entity data enter the federation pipeline.
- Reviewing domain outbound policies so sensitive domains are set to `blocked` or `manual` in the Admin UI.
- Understanding the **Mosaic Effect**: combinations of individually non-sensitive triples can indirectly identify individuals. This requires human judgment, not automation.

MoE Sovereign is not a substitute for a Data Protection Impact Assessment (DPIA) when deploying in regulated environments. The responsibility for preventing contextual PII leaks remains with the human operator initiating a federation push.

---

## Medical & Legal Data

Queries sent to the `medical_consult` or `legal_advisor` experts may contain sensitive personal information (health data, legal situations). This data is subject to heightened protection requirements under GDPR Art. 9 (special categories of personal data).

Operators deploying this system for medical or legal use cases must ensure:
- Appropriate access controls are in place
- Retention periods are minimized
- A Data Protection Impact Assessment (DPIA) is conducted
- A Data Processing Agreement (DPA) exists with all sub-processors

---

## External Data Transmission

By default, **no user data leaves the server**. The following optional integrations may transmit data externally if configured:

| Integration | Data sent | Condition |
|---|---|---|
| SearXNG web search | User query (anonymised) | Only when web search is invoked |
| External LLM APIs (OpenAI, Azure, Google) | Full prompt and context | Only if configured in `INFERENCE_SERVERS` |
| German law tool (`/law`) | Law reference code (no user data) | Only when `/law` tool is called |

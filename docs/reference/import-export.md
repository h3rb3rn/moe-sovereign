# Import & Export

Expert templates and Claude Code profiles can be exported as JSON files and imported again on other instances or by other users. The feature is available both in the Admin backend and in the User Portal.

## Overview

| What | Where (Admin) | Where (User) | Filename |
|-----|-----------|-----------|-----------|
| Expert templates | `/templates` | `/user/templates` | `expert_templates.json` |
| Claude Code profiles | `/profiles` | `/user/cc-profiles` | `cc_profiles.json` |

---

## Export Process

### Admin Backend

```
/templates → "Export" button
           → GET /api/expert-templates/export
           → browser download: expert_templates.json

/profiles → "Export" button
          → GET /api/profiles/export
          → browser download: cc_profiles.json
```

### User Portal

```
/user/templates → "Export" button
               → GET /user/api/templates/export
               → browser download: expert_templates.json

/user/cc-profiles → "Export" button
                 → GET /user/api/cc-profiles/export
                 → browser download: cc_profiles.json
```

---

## Import Process

### Import Modes

| Mode | Behavior for duplicate names |
|-------|------------------------------|
| `merge` | Existing entries are **skipped** |
| `replace` | Existing entries are **overwritten** |

### Response

```json
{
  "ok": true,
  "imported": 3,
  "skipped": 1
}
```

---

## JSON Schemas

### Expert Template Export File

```json
{
  "type": "expert_template",
  "scope": "admin",
  "version": "1.0",
  "exported_at": "2026-04-05T12:00:00.000Z",
  "items": [ ... ]
}
```

`scope` is `"admin"` for admin templates, `"user"` for user templates.

#### Expert Template Item (complete)

```json
{
  "name": "Full-Stack Developer",
  "description": "Optimized for code review, technical support, and data analysis",
  "planner_model": "qwen2.5:32b@RTX",
  "planner_prompt": "You are a router LLM. Analyze the request and decide which expert categories are relevant.",
  "judge_model": "qwen2.5:32b@RTX",
  "judge_prompt": "You are a quality checker. Synthesize the expert responses into an optimal overall answer.",
  "experts": {
    "code_reviewer": {
      "system_prompt": "You are an experienced senior developer. Analyze code for quality, security, and performance.",
      "models": [
        {
          "model": "qwen2.5-coder:32b",
          "endpoint": "RTX",
          "required": true
        },
        {
          "model": "deepseek-coder:6.7b",
          "endpoint": "RTX",
          "required": false
        }
      ]
    },
    "technical_support": {
      "system_prompt": null,
      "models": [
        {
          "model": "qwen2.5:32b",
          "endpoint": "RTX",
          "required": true
        }
      ]
    }
  }
}
```

#### Field Reference: Expert Template Item

| Field | Type | Required | Description |
|------|-----|---------|-------------|
| `name` | string | ✓ | Unique name |
| `description` | string | – | Free-text description |
| `planner_model` | string | – | `model:tag@endpoint` or `null` |
| `planner_prompt` | string | – | System prompt for planner LLM |
| `judge_model` | string | – | `model:tag@endpoint` or `null` |
| `judge_prompt` | string | – | System prompt for judge LLM |
| `experts` | object | ✓ | Map: category ID → expert config |
| `experts.{cat}.system_prompt` | string/null | – | Category-specific system prompt |
| `experts.{cat}.models` | array | ✓ | List of LLMs for this category |
| `experts.{cat}.models[].model` | string | ✓ | Model name (without endpoint) |
| `experts.{cat}.models[].endpoint` | string | ✓ | Inference server name |
| `experts.{cat}.models[].required` | boolean | ✓ | `true` = mandatory, `false` = optional |

---

### Claude Code Profile Export File

```json
{
  "type": "cc_profile",
  "scope": "admin",
  "version": "1.0",
  "exported_at": "2026-04-05T12:00:00.000Z",
  "items": [ ... ]
}
```

#### CC Profile Item (complete)

```json
{
  "name": "Production – Claude Sonnet",
  "accepted_models": [
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5"
  ],
  "tool_model": "qwen2.5:32b",
  "tool_endpoint": "RTX",
  "moe_mode": "moe_orchestrated",
  "tool_choice": "auto",
  "tool_max_tokens": 8192,
  "reasoning_max_tokens": 16384,
  "system_prompt_prefix": "You are a helpful AI assistant in an enterprise environment. Always respond professionally.",
  "stream_think": true
}
```

#### Field Reference: CC Profile Item

| Field | Type | Required | Description |
|------|-----|---------|-------------|
| `name` | string | ✓ | Unique profile name |
| `accepted_models` | array | ✓ | Claude model IDs that are accepted |
| `tool_model` | string | ✓ | LLM for tool calls (`model:tag`) |
| `tool_endpoint` | string | ✓ | Inference server name |
| `moe_mode` | string | ✓ | `native` / `moe_reasoning` / `moe_orchestrated` |
| `tool_choice` | string | – | `auto` (default) or `required` |
| `tool_max_tokens` | integer | – | Max tokens for tool responses (default: 8192) |
| `reasoning_max_tokens` | integer | – | Max tokens for reasoning (default: 16384) |
| `system_prompt_prefix` | string | – | Prefix for all system prompts |
| `stream_think` | boolean | – | Stream MoE pipeline progress (default: true) |

---

## Skeleton Templates

### Minimal Expert Template (Single Model)

```json
{
  "type": "expert_template",
  "scope": "admin",
  "version": "1.0",
  "exported_at": "2026-04-05T00:00:00.000Z",
  "items": [
    {
      "name": "My Template",
      "description": "",
      "planner_model": null,
      "planner_prompt": null,
      "judge_model": null,
      "judge_prompt": null,
      "experts": {
        "general": {
          "system_prompt": null,
          "models": [
            {
              "model": "qwen2.5:32b",
              "endpoint": "RTX",
              "required": true
            }
          ]
        }
      }
    }
  ]
}
```

### Complete Expert Template with Planner & Judge

```json
{
  "type": "expert_template",
  "scope": "admin",
  "version": "1.0",
  "exported_at": "2026-04-05T00:00:00.000Z",
  "items": [
    {
      "name": "Enterprise Full-Stack",
      "description": "Fully configured multi-expert template",
      "planner_model": "qwen2.5:32b@RTX",
      "planner_prompt": "Analyze the request. Decide which of the following categories are relevant: code_reviewer, technical_support, data_analyst. Respond with JSON: {\"categories\": [\"...\"], \"rationale\": \"...\"}",
      "judge_model": "qwen2.5:32b@RTX",
      "judge_prompt": "You receive multiple expert responses. Synthesize them into a single optimal answer without repetition.",
      "experts": {
        "code_reviewer": {
          "system_prompt": "You are an experienced senior developer. Check code for correctness, security (OWASP), performance, and readability.",
          "models": [
            { "model": "qwen2.5-coder:32b", "endpoint": "RTX", "required": true },
            { "model": "deepseek-coder:6.7b", "endpoint": "RTX", "required": false }
          ]
        },
        "technical_support": {
          "system_prompt": null,
          "models": [
            { "model": "qwen2.5:32b", "endpoint": "RTX", "required": true }
          ]
        },
        "data_analyst": {
          "system_prompt": "Analyze data precisely. Use Python code blocks for calculations.",
          "models": [
            { "model": "qwen2.5:32b", "endpoint": "RTX", "required": true }
          ]
        }
      }
    }
  ]
}
```

### Minimal CC Profile

```json
{
  "type": "cc_profile",
  "scope": "admin",
  "version": "1.0",
  "exported_at": "2026-04-05T00:00:00.000Z",
  "items": [
    {
      "name": "Standard",
      "accepted_models": ["claude-sonnet-4-6"],
      "tool_model": "qwen2.5:32b",
      "tool_endpoint": "RTX",
      "moe_mode": "native",
      "tool_choice": "auto",
      "tool_max_tokens": 8192,
      "reasoning_max_tokens": 16384,
      "system_prompt_prefix": null,
      "stream_think": false
    }
  ]
}
```

### CC Profile with Full MoE Pipeline

```json
{
  "type": "cc_profile",
  "scope": "admin",
  "version": "1.0",
  "exported_at": "2026-04-05T00:00:00.000Z",
  "items": [
    {
      "name": "MoE Orchestrated – High Quality",
      "accepted_models": [
        "claude-opus-4-6",
        "claude-sonnet-4-6"
      ],
      "tool_model": "devstral:24b",
      "tool_endpoint": "RTX",
      "moe_mode": "moe_orchestrated",
      "tool_choice": "auto",
      "tool_max_tokens": 16384,
      "reasoning_max_tokens": 32768,
      "system_prompt_prefix": "You are working in an enterprise environment. All responses must be professional.",
      "stream_think": true
    }
  ]
}
```

---

## Validation Rules on Import

| Check | Error behavior |
|---------|----------------|
| `type` matches the endpoint | HTTP 400 |
| `version` == `"1.0"` | HTTP 400 |
| `items` is an array | HTTP 400 |
| `name` is not empty | Entry is skipped |
| Duplicate + mode `merge` | Entry is skipped (no error) |
| Duplicate + mode `replace` | Entry is overwritten |

---

## Knowledge Graph Export / Import

!!! info "Community Knowledge Bundles"
    MoE Sovereign can export learned knowledge from the Neo4j GraphRAG as
    JSON-LD bundles. These bundles can be shared with other MoE instances
    or the community, enabling **collective intelligence** across deployments.

### What Gets Exported

| Component | Description | Sensitive? |
|-----------|-------------|:----------:|
| **Entities** | Named concepts (e.g., "JavaScript", "SyntaxError") | No |
| **Relations** | Triples (e.g., duplicate_const → CAUSES → SyntaxError) | Potentially |
| **Syntheses** | High-level insights connecting multiple entities | No |

### Privacy & Semantic Leakage Protection

The export pipeline applies three layers of protection:

1. **Metadata Stripping**: `tenant_id`, `source_model`, timestamps removed by default
2. **Regex Hard-Filter**: Detects and removes entities/relations containing:
    - Passwords, API keys, credentials
    - IP addresses, email addresses
    - Infrastructure hints (`prod_`, `staging_`, `internal_`)
    - Client/customer names
3. **Sensitive Relation Types**: Relations like `HAS_PASSWORD`, `HAS_CREDENTIAL`,
   `AUTHENTICATES_WITH` are always excluded

The export stats include a `scrubbed` counter showing how many entries were removed.

### Export API

```
GET /graph/knowledge/export
    ?domains=technical_support,code_reviewer   # comma-separated (optional)
    &min_trust=0.3                              # minimum trust score (default: 0.3)
    &strip_sensitive=true                       # remove PII (default: true)
    &include_syntheses=true                     # include synthesis nodes (default: true)
```

**Admin UI**: Admin Backend → Knowledge → Export Bundle

### Export Bundle Format (JSON-LD)

```json
{
  "@context": "https://moe-sovereign.org/knowledge/v1",
  "format_version": "1.0",
  "exported_at": "2026-04-12T20:00:00Z",
  "filters": {"domains": ["code_reviewer"], "min_trust": 0.3},
  "stats": {"entities": 728, "relations": 2341, "syntheses": 69, "scrubbed": 97},
  "entities": [
    {"name": "SyntaxError", "type": "Error", "source": "extracted", "domain": "code_reviewer"}
  ],
  "relations": [
    {
      "subject": "duplicate_const_declaration",
      "predicate": "CAUSES",
      "object": "SyntaxError",
      "confidence": 0.9,
      "trust_score": 0.85,
      "verified": true
    }
  ],
  "syntheses": [
    {
      "text": "Duplicate const declarations in JavaScript cause a SyntaxError that silently kills the entire script block.",
      "insight_type": "synthesis",
      "entities": ["SyntaxError", "JavaScript"],
      "confidence": 0.9
    }
  ]
}
```

### Import API

```
POST /graph/knowledge/import
Content-Type: application/json

{
  "bundle": { ... },           // the JSON-LD bundle
  "source_tag": "community_import",  // source label (default)
  "trust_floor": 0.5,          // max trust for imported data (default: 0.5)
  "dry_run": false              // preview only? (default: false)
}
```

**Validation (Dry Run):**
```
POST /graph/knowledge/import/validate
```

**Admin UI**: Admin Backend → Knowledge → Import Knowledge Bundle

### Import Safety Mechanisms

| Protection | Description |
|-----------|-------------|
| **Entity MERGE** | Entities are merged by name — no duplicates created |
| **Trust Ceiling** | Imported relations capped at `trust_floor` (default 0.5) — never overrides locally verified facts |
| **Contradiction Detection** | If an imported triple contradicts an existing high-trust relation (e.g., A-[TREATS]->B vs. imported A-[CAUSES]->B), the import is **skipped** and logged |
| **Source Tracking** | All imported data tagged with `source: "community_import"` for audit |

### Import Response

```json
{
  "status": "ok",
  "dry_run": false,
  "entities_created": 42,
  "entities_skipped": 686,
  "relations_created": 1205,
  "relations_skipped": 136,
  "syntheses_created": 69,
  "contradictions": [
    {
      "imported": "(Drug_X)-[TREATS]->(Condition_Y)",
      "conflicts_with": "(Drug_X)-[CONTRAINDICATES]->(Condition_Y)",
      "existing_trust": 0.8,
      "existing_source": "extracted"
    }
  ],
  "errors": []
}

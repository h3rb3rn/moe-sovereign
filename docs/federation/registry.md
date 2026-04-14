# Server Discovery

## The MoE Libris Registry

The **moe-libris-registry** is a public Git repository that serves as a decentralized directory of MoE Libris hubs and nodes. It follows the same pattern used by package registries and DNS zone files: the source of truth is a version-controlled repository, and changes are submitted via pull requests.

Repository: `github.com/moe-libris/moe-libris-registry` (planned)

---

## How to Register Your Server

### Step 1: Fork the Registry

Fork the `moe-libris-registry` repository to your own GitHub account.

### Step 2: Add Your Server Entry

Create a new JSON file in the appropriate directory:

- **For hubs:** `hubs/<your-hub-id>.json`
- **For nodes:** `nodes/<your-node-id>.json`

### Step 3: Fill in the Schema

=== "Hub Registration"

    ```json
    {
      "$schema": "https://moe-libris.org/schemas/hub-v1.json",
      "hub_id": "community-hub-eu",
      "display_name": "MoE Libris Community Hub (EU)",
      "description": "Community-operated hub for European MoE Sovereign instances",
      "api_url": "https://hub-eu.moe-libris.org/api/v1",
      "operator": {
        "name": "Example Operator",
        "contact": "admin@example.org",
        "website": "https://example.org"
      },
      "location": {
        "region": "EU",
        "country": "DE"
      },
      "policies": {
        "open_registration": true,
        "auto_accept": false,
        "max_nodes": 100,
        "push_audit": "manual"
      },
      "created_at": "2026-04-13",
      "verified": false
    }
    ```

=== "Node Registration"

    ```json
    {
      "$schema": "https://moe-libris.org/schemas/node-v1.json",
      "node_id": "moe-sovereign-lab-01",
      "display_name": "Horn Lab -- MoE Sovereign",
      "description": "Research lab running MoE Sovereign on heterogeneous GPU hardware",
      "hub_id": "community-hub-eu",
      "operator": {
        "name": "Philipp Horn",
        "contact": "philipp@example.org"
      },
      "capabilities": {
        "domains": ["programming", "machine_learning", "mathematics"],
        "models_count": 8,
        "gpu_types": ["RTX", "Tesla"]
      },
      "created_at": "2026-04-13",
      "verified": false
    }
    ```

### Step 4: Submit a Pull Request

Push your changes and open a pull request against the main registry repository.

---

## Schema Format

### Hub Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `hub_id` | string | Yes | Unique identifier (lowercase, hyphens allowed) |
| `display_name` | string | Yes | Human-readable name |
| `description` | string | Yes | Short description of the hub's purpose |
| `api_url` | string | Yes | Public API base URL |
| `operator.name` | string | Yes | Operator name or organization |
| `operator.contact` | string | Yes | Contact email |
| `operator.website` | string | No | Operator website |
| `location.region` | string | No | Geographic region (e.g., `EU`, `US`, `APAC`) |
| `location.country` | string | No | ISO 3166-1 alpha-2 country code |
| `policies.open_registration` | boolean | Yes | Whether new nodes can register without invitation |
| `policies.auto_accept` | boolean | Yes | Whether registrations are accepted automatically |
| `policies.max_nodes` | integer | No | Maximum number of nodes (null = unlimited) |
| `policies.push_audit` | string | Yes | `manual` or `automatic` |
| `created_at` | date | Yes | Registration date (ISO 8601) |
| `verified` | boolean | No | Set to `true` by registry maintainers after verification |

### Node Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `node_id` | string | Yes | Unique identifier |
| `display_name` | string | Yes | Human-readable name |
| `description` | string | No | Short description |
| `hub_id` | string | Yes | ID of the hub this node is registered with |
| `operator.name` | string | Yes | Operator name |
| `operator.contact` | string | Yes | Contact email |
| `capabilities.domains` | array | No | Knowledge domains this node contributes |
| `capabilities.models_count` | integer | No | Number of models available |
| `capabilities.gpu_types` | array | No | Types of GPUs in use |
| `created_at` | date | Yes | Registration date |
| `verified` | boolean | No | Set by registry maintainers |

---

## CI Validation

The registry repository includes CI checks that run on every pull request:

1. **Schema validation** -- JSON files are validated against the published JSON Schema.
2. **ID uniqueness** -- No two hubs or nodes may share the same ID.
3. **URL reachability** -- The CI pipeline attempts to reach the `api_url` (for hubs) to verify the server is online. This check is advisory; a temporarily offline server does not block the PR.
4. **Contact verification** -- The CI sends a verification email to the operator's contact address. The operator must click the verification link before the PR can be merged.
5. **Duplicate detection** -- Checks for entries with the same `api_url` or `contact` to prevent spam registrations.

---

## Verification Process

After a pull request is submitted:

1. **Automated CI** runs schema validation and reachability checks.
2. **Contact verification** email is sent to the operator.
3. **Registry maintainers** review the PR for completeness and legitimacy.
4. Once merged, the entry appears in the registry with `"verified": false`.
5. **Post-merge verification**: A registry maintainer manually verifies the server by:
    - Checking the hub/node is reachable and responds correctly
    - Verifying the operator's identity (cross-referencing website, social media, or known community members)
    - Running a test handshake (for hubs)
6. Once verified, the maintainer updates the entry to `"verified": true`.

!!! info "Verification is Optional"
    Unverified servers can still participate in federation. However, hub operators may choose to only accept connections from verified nodes. The `verified` flag is a signal of trust, not a requirement.

---

## Discovering Peers

Nodes can discover available hubs and peers through several mechanisms:

| Method | Description |
|--------|-------------|
| **Registry browse** | Browse the Git repository directly |
| **Registry API** | `GET https://registry.moe-libris.org/api/v1/hubs` returns all registered hubs |
| **Hub directory** | Each hub exposes `GET /api/v1/nodes` listing its registered nodes |
| **Admin UI** | The Federation tab includes a "Discover Hubs" button that queries the registry API |

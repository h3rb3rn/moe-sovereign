# Setup Guide

## Prerequisites

Before enabling federation, ensure the following:

- **MoE Sovereign** is running and healthy (all core services up).
- **Neo4j** knowledge graph is operational with at least one domain populated.
- **A MoE Libris Hub** is deployed and reachable from your instance (self-hosted or community hub).
- **Admin access** to the MoE Sovereign Admin UI.

!!! warning "Network Requirements"
    Your MoE Sovereign instance must be reachable by the hub for push confirmations. If running behind NAT, configure port forwarding or use a reverse proxy with a public URL.

---

## Configuring Federation in the Admin UI

### Step 1: Open the Federation Tab

Navigate to the Admin UI and select the **Federation** tab. This tab is only visible to users with the `admin` role.

### Step 2: Set Hub Connection

| Field | Description | Example |
|-------|-------------|---------|
| **Hub URL** | Base URL of the MoE Libris Hub API | `https://hub.moe-libris.org/api/v1` |
| **API Key** | Key received after hub registration (see Step 3) | `mlh-sk-abc123...` |
| **Node ID** | Unique identifier for your instance | `moe-sovereign-lab-01` |
| **Node Display Name** | Human-readable name shown to other nodes | `Horn Lab -- MoE Sovereign` |
| **Callback URL** | Public URL where the hub can reach your instance | `https://moe.example.org/federation/callback` |

### Step 3: Register with the Hub

1. Click **Register with Hub** in the Federation tab.
2. Your instance sends a registration request to the hub (see [Protocol -- Handshake](protocol.md#handshake-flow)).
3. The hub admin reviews and accepts your registration.
4. Once accepted, an API key is exchanged automatically.
5. The status indicator changes from "Pending" to "Connected."

!!! tip "Self-hosted Hub"
    If you run your own hub, you can auto-accept your own nodes. See the hub deployment documentation for details.

---

## Testing Connectivity

After registration is confirmed, verify the connection:

1. In the Federation tab, click **Test Connection**.
2. The system performs a health check against the hub API.
3. A successful test shows:
    - Hub version and uptime
    - Number of registered nodes on the hub
    - Your node's trust status

Alternatively, test from the command line:

```bash
curl -s -H "Authorization: Bearer YOUR_API_KEY" \
  https://hub.moe-libris.org/api/v1/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "nodes_registered": 12,
  "your_node": {
    "id": "moe-sovereign-lab-01",
    "status": "active",
    "last_sync": "2026-04-13T08:30:00Z"
  }
}
```

---

## First Push

Once connected, push knowledge triples to the hub:

1. Navigate to **Federation > Outbound Policy** in the Admin UI.
2. Configure which domains and confidence thresholds to share (see [Trust & Security -- Outbound Policy](trust.md#outbound-policy)).
3. Click **Push Now** or wait for the automatic push cycle (default: every 6 hours).
4. Monitor the push in **Federation > Activity Log**.

The first push exports all eligible triples. Subsequent pushes send only deltas.

---

## First Pull

To import knowledge from the hub:

1. Navigate to **Federation > Import Settings**.
2. Set the **Trust Floor** (default: `0.5` -- imported triples are capped at this confidence).
3. Enable **Auto-Import** or use **Manual Review** mode.
4. Click **Pull Now** to perform an initial delta sync.
5. Review imported triples in the activity log.

!!! info "Contradiction Detection"
    If an imported triple contradicts an existing triple in your knowledge graph, both are flagged for manual review. See [Trust & Security -- Contradiction Detection](trust.md#contradiction-detection).

---

## Configuration Reference

All federation settings are stored in the Admin UI configuration and persisted to the database. They can also be set via environment variables for automated deployments:

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `FEDERATION_ENABLED` | `false` | Enable or disable federation |
| `FEDERATION_HUB_URL` | *(empty)* | Hub API base URL |
| `FEDERATION_API_KEY` | *(empty)* | Hub API key |
| `FEDERATION_NODE_ID` | *(empty)* | Unique node identifier |
| `FEDERATION_PUSH_INTERVAL` | `21600` | Push interval in seconds (default: 6h) |
| `FEDERATION_PULL_INTERVAL` | `3600` | Pull interval in seconds (default: 1h) |
| `FEDERATION_TRUST_FLOOR` | `0.5` | Maximum confidence for imported triples |
| `FEDERATION_AUTO_IMPORT` | `false` | Auto-import pulled triples without review |

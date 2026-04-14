# Firewall & Network Exposure

MoE Sovereign publishes several service ports on the host network. A subset
of these must **never** be reachable from the public internet in a
production deployment. This page lists them, explains the blast radius, and
shows the minimum firewall configuration for each hosting environment.

## Port inventory

All host ports listed below are configurable via `.env` — set
`<SERVICE>_HOST_PORT=<n>` to remap. The defaults shown are the
out-of-the-box values.

| Default port | Service | `.env` override | Bind in `docker-compose.yml` | Must be firewalled? |
|-----:|---------|-----------------|---------------------------------|--------------------:|
| 80   | Caddy reverse proxy (HTTP → HTTPS) | `CADDY_HTTP_PORT` | `0.0.0.0` | No (public) |
| 443  | Caddy reverse proxy (HTTPS / HTTP3) | `CADDY_HTTPS_PORT` | `0.0.0.0` | No (public) |
| 8002 | Orchestrator API (`/v1/chat/completions`, `/graph/*`, `/v1/admin/*`) | `LANGGRAPH_HOST_PORT` | `0.0.0.0` | **Yes — LAN/admins only** |
| 8003 | MCP precision tool server | `MCP_HOST_PORT` | `0.0.0.0` | **Yes — LAN/admins only** |
| 8088 | Admin UI | `ADMIN_UI_HOST_PORT` | `0.0.0.0` | **Yes — LAN/admins only** |
| 8098 | docs / ChromaDB direct port | `DOCS_HOST_PORT` | `0.0.0.0` | **Yes — LAN/admins only** |
| 3001 | Grafana dashboards | `GRAFANA_HOST_PORT` | `0.0.0.0` | **Yes — LAN/admins only** |
| 8001 | ChromaDB internal | `CHROMA_HOST_PORT` | `127.0.0.1` (loopback) | No (already loopback) |
| 9090 | Prometheus | `PROMETHEUS_HOST_PORT` | `127.0.0.1` (loopback) | No (already loopback) |
| 7474 | Neo4j Browser | `NEO4J_HTTP_PORT` | `127.0.0.1` (loopback) | No (already loopback) |
| 7687 | Neo4j Bolt | `NEO4J_BOLT_PORT` | `127.0.0.1` (loopback) | No (already loopback) |
| 9092 | Kafka broker | `KAFKA_HOST_PORT` | `127.0.0.1` (loopback) | No (already loopback) |
| 9999 | Dozzle log viewer | `DOZZLE_HOST_PORT` | `127.0.0.1` (loopback) | No (already loopback) |
| 9100 | node-exporter | `NODE_EXPORTER_HOST_PORT` | `127.0.0.1` (loopback) | No (already loopback) |
| 9338 | cAdvisor | `CADVISOR_HOST_PORT` | `127.0.0.1` (loopback) | No (already loopback) |

If a port is already used on your host (e.g. local nginx on `:80` or
GitLab Pages on `:8088`), set the override in `.env` and run
`docker compose up -d` — no edit to `docker-compose.yml` required.

## Why these ports need firewalling

The orchestrator's graph and admin endpoints do **not** require an API key
by design — they rely on network-level isolation for authentication. If
exposed publicly, an unauthenticated attacker can:

- **read and overwrite the knowledge graph** via `POST /graph/knowledge/import`
- **export the full entity list** via `GET /graph/knowledge/export`
- **search all entities** via `GET /graph/search?q=…`
- **enumerate ontology gaps, planner patterns and tool-eval metrics** via
  the `/v1/admin/*` endpoints

Port 8088 (Admin UI) handles its own session auth, but a publicly reachable
admin login page is still a large attack surface (credential stuffing,
brute force, zero-days in the auth stack). Treat it as an internal-only
service.

## Production recommendation

- **Always** deploy behind Caddy (ports 80/443) and route public traffic to
  `api.<domain>` / `admin.<domain>` through the reverse proxy. The
  Caddyfile shipped with this repo already terminates TLS and proxies to
  the internal ports — the host's external firewall only needs to permit
  80/443.
- Block ports 8002, 8003, 8088, 8098 and 3001 on the host firewall. They
  remain reachable from inside the Docker network (containers talk to
  each other directly) and from `localhost` (operators), which is the
  intended access pattern.

## UFW (Ubuntu/Debian)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp          # SSH
sudo ufw allow 80/tcp          # Caddy HTTP
sudo ufw allow 443/tcp         # Caddy HTTPS
sudo ufw allow 443/udp         # Caddy HTTP/3 (QUIC)
# Optional: allow LAN admin access on 8088/3001
# sudo ufw allow from 10.0.0.0/8 to any port 8088
# sudo ufw allow from 10.0.0.0/8 to any port 3001
sudo ufw enable
sudo ufw status verbose
```

## firewalld (RHEL/Fedora/Alma)

```bash
sudo firewall-cmd --zone=public --add-service=ssh --permanent
sudo firewall-cmd --zone=public --add-service=http --permanent
sudo firewall-cmd --zone=public --add-service=https --permanent
# Explicitly deny the internal service ports on the public zone
for p in 8002 8003 8088 8098 3001; do
  sudo firewall-cmd --zone=public --remove-port=${p}/tcp --permanent 2>/dev/null || true
done
sudo firewall-cmd --reload
```

## iptables (minimal)

```bash
# Allow loopback
sudo iptables -A INPUT -i lo -j ACCEPT
# Allow established + related
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Public services
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -A INPUT -p udp --dport 443 -j ACCEPT
# Drop everything else
sudo iptables -P INPUT DROP
```

## WSL / local-dev installs

Docker Desktop on Windows forwards published ports to `localhost` by default
— they are **not** externally reachable unless the Windows Defender
Firewall is explicitly opened. Verify with:

```powershell
netsh advfirewall firewall show rule name=all | Select-String 8002,8003,8088
```

If the rules expose the ports to your LAN, remove or scope them to
`127.0.0.1`.

## Cloud / managed environments

- **AWS / GCP / Azure**: use the VPC security groups or NSGs. Keep only
  80/443 in the public-facing group. Place 8002/8003/8088/8098/3001 in an
  internal-only group reachable from your admin jump host or VPN.
- **Kubernetes**: set `type: ClusterIP` for all services except the
  ingress. Expose only the ingress via `LoadBalancer` or `NodePort`.
  Consult the Helm chart's `values.yaml` — the defaults already match this
  pattern.

## Verification

After applying the rules, confirm externally (from a host outside your
network, e.g. a VPS):

```bash
for p in 80 443 8002 8003 8088 8098 3001; do
  nc -zv -w 2 <your-public-ip> $p 2>&1 | grep -E 'succeeded|open|refused|timed out'
done
```

Expected: `80` and `443` open, everything else `refused` or `timed out`.

## What does **not** depend on a firewall

Public-facing endpoints still enforce their own authentication:

- `POST /v1/chat/completions` — API key required (`Authorization: Bearer moe-sk-…`)
- `POST /v1/memory/ingest` — API key required
- `POST /v1/feedback` — API key required
- `POST /v1/messages` (Anthropic-compatible) — API key required

These are safe to reach through the reverse proxy.

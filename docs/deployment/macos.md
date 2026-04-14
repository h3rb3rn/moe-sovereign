# macOS Deployment (Docker Desktop)

MoE Sovereign runs on macOS via Docker Desktop, including Apple Silicon
(M1/M2/M3/M4). The compose stack uses bind mounts for state directories
(Postgres, Neo4j, Redis, ChromaDB, Kafka, Prometheus, Grafana) — those
need to live in a path that Docker Desktop is allowed to share.

## Common error

```text
Error response from daemon: mounts denied:
The path /opt/moe-infra is not shared from the host and is not known to Docker.
You can configure shared paths from Docker -> Preferences... -> Resources -> File Sharing.
```

This means the `MOE_DATA_ROOT` host directory is outside Docker Desktop's
File Sharing whitelist. By default Docker Desktop only permits `/Users`,
`/Volumes`, `/private`, `/tmp` and `/var/folders`. `/opt` is **not** in
that list.

## One-time setup

1. Install Docker Desktop ≥ 4.30 from <https://docs.docker.com/desktop/install/mac-install/>.
2. Allow the project to use a host directory under your home folder. The
   installer picks `~/moe-data` automatically on macOS.
3. Open **Docker Desktop → Settings → Resources → File Sharing** and
   verify that `/Users/<your-name>/moe-data` (or whichever path you set
   in `MOE_DATA_ROOT`) is in the list. If not, click **+** to add it,
   then **Apply & Restart**.
4. Run the installer:
   ```bash
   curl -sSL https://moe-sovereign.org/install.sh | bash
   ```
   The script detects macOS, defaults `MOE_DATA_ROOT=$HOME/moe-data`,
   creates the subdirectories, and writes the path into `.env`.

## Manual setup (without install.sh)

```bash
# 1. Choose shared paths (Grafana keeps its own root for separate backups)
export MOE_DATA_ROOT="$HOME/moe-data"
export GRAFANA_DATA_ROOT="$HOME/moe-grafana"

# 2. Pre-create the subdirectories
mkdir -p "$MOE_DATA_ROOT"/{kafka-data,neo4j-data,neo4j-logs,agent-logs,\
chroma-onnx-cache,chroma-data,redis-data,prometheus-data,admin-logs,\
userdb,few-shot}
mkdir -p "$GRAFANA_DATA_ROOT"/{data,dashboards}

# 3. Add the host roots to your .env
{
  echo "MOE_DATA_ROOT=$MOE_DATA_ROOT"
  echo "GRAFANA_DATA_ROOT=$GRAFANA_DATA_ROOT"
} >> .env

# 4. Add BOTH paths to Docker Desktop File Sharing
#    Settings → Resources → File Sharing → +  →  $MOE_DATA_ROOT
#    Settings → Resources → File Sharing → +  →  $GRAFANA_DATA_ROOT
#    → Apply & Restart

# 5. Bring up the stack
docker compose up -d
```

### Configurable host paths

All container bind mounts derive from these `.env` variables — adjust
them once and every service follows. `install.sh` writes the right
defaults for your platform.

| Variable | Linux default | macOS default | What it holds |
|---|---|---|---|
| `MOE_DATA_ROOT` | `/opt/moe-infra` | `$HOME/moe-data` | Postgres, Neo4j, Redis, ChromaDB, Kafka, Prometheus, agent/admin logs, few-shot store |
| `GRAFANA_DATA_ROOT` | `/opt/grafana` | `$HOME/moe-grafana` | Grafana SQLite + dashboards |
| `FEW_SHOT_HOST_DIR` | `${MOE_DATA_ROOT}/few-shot` | same | Override only if you mount a separate dataset disk |

## Apple Silicon notes

- All upstream images (Postgres, Neo4j, Valkey, ChromaDB, Caddy,
  Prometheus, Grafana, Kafka) ship `arm64` variants — no emulation
  required.
- The MoE orchestrator and admin UI are built locally from the included
  `Dockerfile`s. The first `docker compose build` will compile native
  arm64 wheels (mostly Pydantic / NumPy / SciPy) and takes 5–15 minutes.
- For inference, **use Ollama on the host** (`brew install ollama`),
  not inside the container. Ollama on macOS uses Metal, which is faster
  than the CPU fallback inside any container.
  ```bash
  brew install ollama
  ollama serve   # binds 127.0.0.1:11434 by default
  ollama pull llama3.1:8b
  ```
  Then point the orchestrator at `http://host.docker.internal:11434/v1`
  via the Admin UI's *Inference Servers* page.

## Performance tuning for Docker Desktop

- Docker Desktop → Settings → Resources → CPUs / Memory: allocate at
  least **6 CPUs and 12 GB RAM**. The default 2 CPU / 8 GB is enough
  to start but throttles ChromaDB embeddings and Neo4j heavily.
- Use **VirtioFS** (Settings → General → "Use Virtualization framework")
  on Ventura+. It is significantly faster for bind mounts than
  `gRPC FUSE`.
- Disable **WSL2 / Hyper-V backend leftovers** — irrelevant on macOS but
  some users carry the toggle from a Windows install.

## Verify

After `docker compose up -d`:

```bash
docker compose ps                  # all services Up + healthy
docker compose logs langgraph-app  # no "mount denied" lines
open http://localhost:8088         # Admin UI
```

Common follow-up errors and what they mean:

| Error | Cause | Fix |
|---|---|---|
| `permission denied` on a sub-volume | Host UID ≠ container UID | `chmod -R 0775 "$MOE_DATA_ROOT"` |
| `Cannot connect to the Docker daemon` | Docker Desktop is not running | start it from /Applications |
| `no matching manifest for linux/arm64` | Pinned amd64-only image | upstream issue; report or use `--platform linux/amd64` |
| Slow `docker compose up` | gRPC FUSE bind-mount layer | switch to VirtioFS (see above) |

## Uninstall

```bash
docker compose down -v             # stop + remove containers
rm -rf "$MOE_DATA_ROOT"            # remove all bind-mounted data
# (also remove the path from Docker Desktop File Sharing if you wish)
```

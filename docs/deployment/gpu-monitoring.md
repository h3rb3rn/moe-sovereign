# GPU & Inference Node Monitoring

MoE Sovereign automatically collects GPU metrics from inference nodes when a
[Prometheus Node Exporter](https://github.com/prometheus/node_exporter) with
an NVIDIA GPU textfile collector is installed on each Ollama host.

## What the System Reads Automatically

When a node-exporter is reachable on port 9100 of an inference node, the system
collects:

| Metric | Source | Used In |
|--------|--------|---------|
| CPU usage | `node_cpu_seconds_total` | Admin UI Server Cards, Grafana |
| RAM used/total | `node_memory_*` | Admin UI Server Cards, Grafana |
| Disk usage | `node_filesystem_*` | Grafana Gauge |
| Network I/O | `node_network_*` | Grafana |
| **GPU VRAM used/total** | `node_gpu_memory_*_bytes` | Admin UI, Grafana, VRAM-aware scheduler |
| **GPU utilization** | `node_gpu_utilization_percent` | Admin UI, Grafana |
| **GPU temperature** | `node_gpu_temperature_celsius` | Grafana |
| **GPU power draw/limit** | `node_gpu_power_*_watts` | Grafana |

The Admin UI derives the host IP from the Ollama URL and queries `/metrics`
directly — no additional configuration needed.

## Installation Script

Run this on each Ollama host with NVIDIA GPUs:

```bash
#!/usr/bin/env bash
# Setup: Prometheus Node Exporter + NVIDIA GPU Textfile Collector
# Target: Debian / Ubuntu with nvidia-smi installed
set -e

# 1. Install Node Exporter
apt-get update && apt-get install -y prometheus-node-exporter

# 2. Prepare textfile collector directory
TEXTFILE_DIR="/var/lib/prometheus/node-exporter"
mkdir -p "$TEXTFILE_DIR"
chown prometheus:prometheus "$TEXTFILE_DIR"

# 3. Create GPU metrics collection script
cat << 'SCRIPT' > /usr/local/bin/get_gpu_metrics.sh
#!/usr/bin/env bash
PROM_FILE="/var/lib/prometheus/node-exporter/gpu_metrics.prom"
TMP_FILE="${PROM_FILE}.tmp"

command -v nvidia-smi &>/dev/null || exit 1

{
    echo "# HELP node_gpu_memory_total_bytes Total GPU memory in bytes"
    echo "# TYPE node_gpu_memory_total_bytes gauge"
    echo "# HELP node_gpu_memory_used_bytes Used GPU memory in bytes"
    echo "# TYPE node_gpu_memory_used_bytes gauge"
    echo "# HELP node_gpu_utilization_percent GPU core utilization in percent"
    echo "# TYPE node_gpu_utilization_percent gauge"
    echo "# HELP node_gpu_temperature_celsius GPU temperature in Celsius"
    echo "# TYPE node_gpu_temperature_celsius gauge"
    echo "# HELP node_gpu_power_draw_watts Current GPU power draw in Watts"
    echo "# TYPE node_gpu_power_draw_watts gauge"
    echo "# HELP node_gpu_power_limit_watts Max GPU power limit in Watts"
    echo "# TYPE node_gpu_power_limit_watts gauge"
} > "$TMP_FILE"

nvidia-smi --query-gpu=index,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,power.limit \
  --format=csv,noheader,nounits | while IFS=', ' read -r id mem_total mem_used util temp power_draw power_limit; do
    mem_total_bytes=$((mem_total * 1024 * 1024))
    mem_used_bytes=$((mem_used * 1024 * 1024))
    {
        echo "node_gpu_memory_total_bytes{gpu=\"${id}\"} ${mem_total_bytes}"
        echo "node_gpu_memory_used_bytes{gpu=\"${id}\"} ${mem_used_bytes}"
        echo "node_gpu_utilization_percent{gpu=\"${id}\"} ${util}"
        echo "node_gpu_temperature_celsius{gpu=\"${id}\"} ${temp}"
        echo "node_gpu_power_draw_watts{gpu=\"${id}\"} ${power_draw}"
        echo "node_gpu_power_limit_watts{gpu=\"${id}\"} ${power_limit}"
    } >> "$TMP_FILE"
done

mv "$TMP_FILE" "$PROM_FILE"
SCRIPT
chmod +x /usr/local/bin/get_gpu_metrics.sh

# 4. Cron job (every minute)
echo "* * * * * root /usr/local/bin/get_gpu_metrics.sh" > /etc/cron.d/prometheus_gpu_metrics

# 5. Enable textfile collector
CONFIG="/etc/default/prometheus-node-exporter"
FLAG="--collector.textfile.directory=$TEXTFILE_DIR"
if ! grep -q -- "$FLAG" "$CONFIG"; then
    if grep -q '^ARGS=""' "$CONFIG"; then
        sed -i "s|^ARGS=\"\"|ARGS=\"$FLAG\"|" "$CONFIG"
    else
        echo "ARGS=\"\$ARGS $FLAG\"" >> "$CONFIG"
    fi
    systemctl restart prometheus-node-exporter
fi

# Initial run
/usr/local/bin/get_gpu_metrics.sh
echo "Done. GPU metrics available at http://$(hostname -I | awk '{print $1}'):9100/metrics"
```

## Prometheus Configuration

Add your inference nodes to `prometheus/prometheus.yml`:

```yaml
- job_name: 'inference-nodes'
  static_configs:
    - targets: ['<NODE_1_IP>:9100']
      labels:
        node: 'gpu-node-1'
    - targets: ['<NODE_2_IP>:9100']
      labels:
        node: 'gpu-node-2'
  scrape_interval: 15s
```

After editing, reload Prometheus without restart:
```bash
curl -X POST http://localhost:9090/-/reload
```

## Grafana Dashboard

The `moe-gpu-nodes` dashboard provides:

| Panel | Description |
|-------|-------------|
| CPU Usage | Per-node CPU utilization (filled area) |
| RAM Used | Used (solid) with total as dashed reference line |
| VRAM Used | Used VRAM (solid fill) with max VRAM as red dashed line |
| GPU Utilization | Per-GPU utilization with 70/90% warning thresholds |
| GPU Temperature | Per-GPU temperature with 70/80/85°C color thresholds |
| GPU Power Draw | Current draw (solid) with power limit (red dashed) |
| Disk Usage | Gauge per node with 70/85% color coding |
| Network I/O | RX/TX bytes per second per node |

## How the VRAM-Aware Scheduler Uses These Metrics

The orchestrator's `_select_node()` function polls each Ollama endpoint's
`/api/ps` every 5 seconds to determine which models are currently loaded
in VRAM. The Prometheus GPU metrics provide an additional layer:

1. **Model Registry** (Valkey): Records which models are warm on which nodes
2. **Sticky Sessions**: Routes repeat requests to the same node (warm model)
3. **Load Score**: `running_models / gpu_count` determines node busyness
4. **Admin UI**: Server cards show per-GPU VRAM, temperature, and power in real-time

## Improvement Suggestions

The current script collects metrics every 60 seconds (cron). For higher
resolution during benchmarks:

```bash
# Optional: 15-second collection via systemd timer instead of cron
# (better than cron for sub-minute intervals)
```

For AMD GPUs, replace `nvidia-smi` with `rocm-smi`:
```bash
rocm-smi --showmeminfo vram --showtemp --showpower --csv
```

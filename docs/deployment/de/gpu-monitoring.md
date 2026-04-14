# GPU- & Inferenz-Knoten-Monitoring

MoE Sovereign erfasst automatisch GPU-Metriken von Inferenz-Knoten, sofern auf
jedem Ollama-Host ein
[Prometheus Node Exporter](https://github.com/prometheus/node_exporter) mit
einem NVIDIA-GPU-Textfile-Collector installiert ist.

## Automatisch erfasste Metriken

Sobald ein Node-Exporter auf Port 9100 eines Inferenz-Knotens erreichbar ist,
werden folgende Metriken erfasst:

| Metrik | Quelle | Verwendet in |
|--------|--------|-------------|
| CPU-Auslastung | `node_cpu_seconds_total` | Admin-UI Server Cards, Grafana |
| RAM belegt/gesamt | `node_memory_*` | Admin-UI Server Cards, Grafana |
| Festplattennutzung | `node_filesystem_*` | Grafana-Gauge |
| Netzwerk-I/O | `node_network_*` | Grafana |
| **GPU-VRAM belegt/gesamt** | `node_gpu_memory_*_bytes` | Admin-UI, Grafana, VRAM-bewusster Scheduler |
| **GPU-Auslastung** | `node_gpu_utilization_percent` | Admin-UI, Grafana |
| **GPU-Temperatur** | `node_gpu_temperature_celsius` | Grafana |
| **GPU-Leistungsaufnahme/-limit** | `node_gpu_power_*_watts` | Grafana |

Die Admin-UI leitet die Host-IP aus der Ollama-URL ab und fragt `/metrics`
direkt ab -- keine zusaetzliche Konfiguration noetig.

## Installationsskript

Dieses Skript auf jedem Ollama-Host mit NVIDIA-GPUs ausfuehren:

```bash
#!/usr/bin/env bash
# Setup: Prometheus Node Exporter + NVIDIA GPU Textfile Collector
# Ziel: Debian / Ubuntu mit installiertem nvidia-smi
set -e

# 1. Node Exporter installieren
apt-get update && apt-get install -y prometheus-node-exporter

# 2. Textfile-Collector-Verzeichnis vorbereiten
TEXTFILE_DIR="/var/lib/prometheus/node-exporter"
mkdir -p "$TEXTFILE_DIR"
chown prometheus:prometheus "$TEXTFILE_DIR"

# 3. GPU-Metrik-Sammelskript erstellen
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

# 4. Cron-Job (jede Minute)
echo "* * * * * root /usr/local/bin/get_gpu_metrics.sh" > /etc/cron.d/prometheus_gpu_metrics

# 5. Textfile-Collector aktivieren
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

# Erstmalige Ausfuehrung
/usr/local/bin/get_gpu_metrics.sh
echo "Done. GPU metrics available at http://$(hostname -I | awk '{print $1}'):9100/metrics"
```

## Prometheus-Konfiguration

Die Inferenz-Knoten in `prometheus/prometheus.yml` eintragen:

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

Nach dem Bearbeiten Prometheus ohne Neustart neu laden:
```bash
curl -X POST http://localhost:9090/-/reload
```

## Grafana-Dashboard

Das `moe-gpu-nodes`-Dashboard bietet folgende Panels:

| Panel | Beschreibung |
|-------|-------------|
| CPU-Auslastung | CPU-Nutzung pro Knoten (gefuellte Flaeche) |
| RAM belegt | Belegter RAM (Vollfarbe) mit Gesamtwert als gestrichelte Referenzlinie |
| VRAM belegt | Belegtes VRAM (gefuellte Flaeche) mit maximalem VRAM als rote gestrichelte Linie |
| GPU-Auslastung | Pro-GPU-Auslastung mit 70/90 %-Warnschwellen |
| GPU-Temperatur | Pro-GPU-Temperatur mit 70/80/85 Grad-Farbschwellen |
| GPU-Leistungsaufnahme | Aktuelle Aufnahme (Vollfarbe) mit Leistungslimit (rote gestrichelte Linie) |
| Festplattennutzung | Gauge pro Knoten mit 70/85 %-Farbcodierung |
| Netzwerk-I/O | RX/TX Bytes pro Sekunde pro Knoten |

## Nutzung der Metriken durch den VRAM-bewussten Scheduler

Die `_select_node()`-Funktion des Orchestrators fragt alle 5 Sekunden den
`/api/ps`-Endpunkt jedes Ollama-Hosts ab, um zu ermitteln, welche Modelle
aktuell im VRAM geladen sind. Die Prometheus-GPU-Metriken liefern eine
zusaetzliche Schicht:

1. **Modell-Registry** (Valkey): Verzeichnet, welche Modelle auf welchen Knoten warm sind
2. **Sticky Sessions**: Leitet Wiederholungsanfragen an denselben Knoten (warmes Modell)
3. **Last-Score**: `laufende_Modelle / GPU-Anzahl` bestimmt die Auslastung eines Knotens
4. **Admin-UI**: Server Cards zeigen pro-GPU-VRAM, Temperatur und Leistung in Echtzeit

## Verbesserungsvorschlaege

Das aktuelle Skript sammelt Metriken alle 60 Sekunden (Cron). Fuer hoehere
Aufloesung waehrend Benchmarks:

```bash
# Optional: 15-Sekunden-Erfassung via systemd-Timer statt Cron
# (besser als Cron fuer sub-minuetliche Intervalle)
```

Fuer AMD-GPUs `nvidia-smi` durch `rocm-smi` ersetzen:
```bash
rocm-smi --showmeminfo vram --showtemp --showpower --csv
```

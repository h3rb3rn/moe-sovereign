#!/usr/bin/env python3
"""
power_monitor.py -- Continuous GPU power draw logger for N04-RTX during a benchmark run.

Polls `nvidia-smi --query-gpu=power.draw` over SSH at a fixed interval, appends every
sample to a CSV, and maintains a live-updating JSON summary with cumulative energy
(Wh) per GPU and total, via trapezoidal integration between samples. Designed to run
alongside benchmarks/run_scientific_benchmark.py for the whole overnight run and be
read at any time (the summary is always current, not just written at the end) so it
can later be correlated with per-task timestamps in the benchmark's own result JSON.

Usage:
    python3 benchmarks/power_monitor.py --run-id <run_id> [--host N04-RTX] [--interval 10]

Stop with SIGTERM/SIGINT -- writes a final summary before exiting.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import signal
import subprocess
import sys
import time
import datetime

RESULTS_DIR = pathlib.Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

_stop = False


def _handle_stop(signum, frame):
    global _stop
    _stop = True


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)


def sample(host: str) -> list[tuple[int, str, float]]:
    """Returns [(gpu_index, gpu_name, watts), ...] or [] on failure (host unreachable etc)."""
    try:
        out = subprocess.run(
            ["ssh", host, "nvidia-smi --query-gpu=index,name,power.draw --format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return []
        rows = []
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3:
                try:
                    rows.append((int(parts[0]), parts[1], float(parts[2])))
                except ValueError:
                    continue
        return rows
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True, help="Tag used in output filenames, e.g. the benchmark run_id")
    ap.add_argument("--host", default="N04-RTX")
    ap.add_argument("--interval", type=float, default=10.0, help="Seconds between samples")
    args = ap.parse_args()

    csv_path = RESULTS_DIR / f"power_log_{args.run_id}.csv"
    summary_path = RESULTS_DIR / f"power_summary_{args.run_id}.json"

    csv_new = not csv_path.exists()
    csv_f = open(csv_path, "a", newline="")
    writer = csv.writer(csv_f)
    if csv_new:
        writer.writerow(["timestamp_utc", "gpu_index", "gpu_name", "watts"])

    # energy_wh[gpu_index] = cumulative Wh; last_sample[gpu_index] = (unix_ts, watts)
    energy_wh: dict[int, float] = {}
    gpu_names: dict[int, str] = {}
    last_sample: dict[int, tuple[float, float]] = {}
    start_ts = time.time()
    n_samples = 0
    n_failed_polls = 0

    def write_summary():
        total_wh = sum(energy_wh.values())
        elapsed_h = (time.time() - start_ts) / 3600.0
        payload = {
            "run_id": args.run_id,
            "host": args.host,
            "started_utc": datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc).isoformat(),
            "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_hours": round(elapsed_h, 4),
            "n_samples": n_samples,
            "n_failed_polls": n_failed_polls,
            "total_energy_wh": round(total_wh, 3),
            "total_energy_kwh": round(total_wh / 1000.0, 5),
            "mean_total_power_w": round(total_wh / elapsed_h, 2) if elapsed_h > 0 else 0.0,
            "per_gpu": {
                str(idx): {
                    "name": gpu_names.get(idx, "?"),
                    "energy_wh": round(wh, 3),
                    "last_watts": last_sample.get(idx, (0, 0.0))[1],
                }
                for idx, wh in sorted(energy_wh.items())
            },
        }
        summary_path.write_text(json.dumps(payload, indent=2))

    print(f"[power_monitor] Logging {args.host} GPU power every {args.interval}s -> {csv_path}", flush=True)
    try:
        while not _stop:
            now = time.time()
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            rows = sample(args.host)
            if not rows:
                n_failed_polls += 1
            else:
                n_samples += 1
                for idx, name, watts in rows:
                    gpu_names[idx] = name
                    writer.writerow([now_iso, idx, name, watts])
                    if idx in last_sample:
                        prev_ts, prev_watts = last_sample[idx]
                        dt_h = (now - prev_ts) / 3600.0
                        # trapezoidal integration between consecutive samples
                        energy_wh[idx] = energy_wh.get(idx, 0.0) + (prev_watts + watts) / 2.0 * dt_h
                    last_sample[idx] = (now, watts)
                csv_f.flush()
                write_summary()
            time.sleep(args.interval)
    finally:
        write_summary()
        csv_f.close()
        print(f"[power_monitor] Stopped. Total energy: {sum(energy_wh.values()):.2f} Wh over {n_samples} samples.", flush=True)


if __name__ == "__main__":
    main()

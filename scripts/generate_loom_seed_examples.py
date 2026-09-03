#!/usr/bin/env python3
"""scripts/generate_loom_seed_examples.py — bootstrap seed data for
data/loom_training_examples.jsonl (LUMI-G post-training Candidate 1,
docs/experiments/lumig_posttraining_candidates.md: acquire-release
memory-ordering reasoning).

Organic production/benchmark traffic triggers the rust_loom_check merger
retry path too rarely to build a usable training set in reasonable time.
This script instead drives the real rust-loom-sandbox service directly with
a curated library of producer/consumer memory-ordering scenarios, each in a
deliberately-broken (Relaxed publish/consume) and a corrected
(Release/Acquire) variant, and records the *actual* sandbox verdict for
each -- it never fabricates a compiles/passed label.

Output is written to stdout in the exact JSONL schema
graph.synthesis._record_loom_training_example produces, one line per
determinate result (compiles is not None and not timed_out), so it can be
appended straight onto data/loom_training_examples.jsonl and consumed
identically to organically-collected examples by
scripts/curate_coder_expert_dataset.py.

Must run with network access to the sandbox's internal-only network
(moe-infra_rust_loom_internal) -- e.g. inside the mcp-precision container,
which already talks to RUST_LOOM_SANDBOX_URL for the live rust_loom_check
tool:

    docker cp scripts/generate_loom_seed_examples.py mcp-precision:/tmp/gen.py
    docker exec mcp-precision python3 /tmp/gen.py --count 3 \\
        >> data/loom_training_examples.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Tuple

import httpx

DEFAULT_SANDBOX_URL = "http://rust-loom-sandbox:8080"
_HTTP_TIMEOUT_S = 200.0  # above the sandbox's own 180s RUST_LOOM_TIMEOUT_S
_OUTPUT_TAIL_MAX_CHARS = 2000  # matches _record_loom_training_example


def _flag_guard(v: Dict[str, Any], broken: bool) -> str:
    store_order = "Relaxed" if broken else "Release"
    load_order = "Relaxed" if broken else "Acquire"
    return f"""use loom::sync::atomic::{{AtomicUsize, Ordering}};
use loom::sync::Arc;
use loom::thread;

#[test]
fn {v['test_name']}() {{
    loom::model(|| {{
        let {v['payload']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));
        let {v['ready']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));

        let producer_payload = {v['payload']}.clone();
        let producer_ready = {v['ready']}.clone();
        let producer = thread::spawn(move || {{
            producer_payload.store({v['value']}, Ordering::Relaxed);
            producer_ready.store(1, Ordering::{store_order});
        }});

        let consumer_payload = {v['payload']}.clone();
        let consumer_ready = {v['ready']}.clone();
        let consumer = thread::spawn(move || {{
            if consumer_ready.load(Ordering::{load_order}) == 1 {{
                let observed = consumer_payload.load(Ordering::Relaxed);
                assert_eq!(observed, {v['value']}, "payload write not ordered before ready publish");
            }}
        }});

        producer.join().unwrap();
        consumer.join().unwrap();
    }});
}}
"""


def _counter_handoff(v: Dict[str, Any], broken: bool) -> str:
    store_order = "Relaxed" if broken else "Release"
    load_order = "Relaxed" if broken else "Acquire"
    return f"""use loom::sync::atomic::{{AtomicUsize, Ordering}};
use loom::sync::Arc;
use loom::thread;

#[test]
fn {v['test_name']}() {{
    loom::model(|| {{
        let {v['slot']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));
        let {v['gen']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));

        let w_slot = {v['slot']}.clone();
        let w_gen = {v['gen']}.clone();
        let writer = thread::spawn(move || {{
            w_slot.store({v['value']}, Ordering::Relaxed);
            w_gen.store(1, Ordering::{store_order});
        }});

        let r_slot = {v['slot']}.clone();
        let r_gen = {v['gen']}.clone();
        let reader = thread::spawn(move || {{
            if r_gen.load(Ordering::{load_order}) == 1 {{
                let observed = r_slot.load(Ordering::Relaxed);
                assert_eq!(observed, {v['value']}, "reader observed generation bump before slot write was visible");
            }}
        }});

        writer.join().unwrap();
        reader.join().unwrap();
    }});
}}
"""


def _lazy_init_config(v: Dict[str, Any], broken: bool) -> str:
    store_order = "Relaxed" if broken else "Release"
    load_order = "Relaxed" if broken else "Acquire"
    return f"""use loom::sync::atomic::{{AtomicUsize, Ordering}};
use loom::sync::Arc;
use loom::thread;

#[test]
fn {v['test_name']}() {{
    loom::model(|| {{
        let {v['field_a']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));
        let {v['field_b']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));
        let {v['ready']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));

        let init_a = {v['field_a']}.clone();
        let init_b = {v['field_b']}.clone();
        let init_ready = {v['ready']}.clone();
        let initializer = thread::spawn(move || {{
            init_a.store({v['value']}, Ordering::Relaxed);
            init_b.store({v['value2']}, Ordering::Relaxed);
            init_ready.store(1, Ordering::{store_order});
        }});

        let read_a = {v['field_a']}.clone();
        let read_b = {v['field_b']}.clone();
        let read_ready = {v['ready']}.clone();
        let user = thread::spawn(move || {{
            if read_ready.load(Ordering::{load_order}) == 1 {{
                let a = read_a.load(Ordering::Relaxed);
                let b = read_b.load(Ordering::Relaxed);
                assert_eq!(a, {v['value']}, "config field a not visible despite ready flag observed");
                assert_eq!(b, {v['value2']}, "config field b not visible despite ready flag observed");
            }}
        }});

        initializer.join().unwrap();
        user.join().unwrap();
    }});
}}
"""


def _spsc_single_slot(v: Dict[str, Any], broken: bool) -> str:
    store_order = "Relaxed" if broken else "Release"
    load_order = "Relaxed" if broken else "Acquire"
    return f"""use loom::sync::atomic::{{AtomicUsize, Ordering}};
use loom::sync::Arc;
use loom::thread;

#[test]
fn {v['test_name']}() {{
    loom::model(|| {{
        let {v['slot']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));
        let {v['tail']}: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));

        let p_slot = {v['slot']}.clone();
        let p_tail = {v['tail']}.clone();
        let producer = thread::spawn(move || {{
            p_slot.store({v['value']}, Ordering::Relaxed);
            p_tail.store(1, Ordering::{store_order});
        }});

        let c_slot = {v['slot']}.clone();
        let c_tail = {v['tail']}.clone();
        let consumer = thread::spawn(move || {{
            if c_tail.load(Ordering::{load_order}) == 1 {{
                let observed = c_slot.load(Ordering::Relaxed);
                assert_eq!(observed, {v['value']}, "consumer advanced past tail before payload write was published");
            }}
        }});

        producer.join().unwrap();
        consumer.join().unwrap();
    }});
}}
"""


_ARCHETYPES = {
    "flag_guard": (_flag_guard, ["payload", "ready", "value"]),
    "counter_handoff": (_counter_handoff, ["slot", "gen", "value"]),
    "lazy_init_config": (_lazy_init_config, ["field_a", "field_b", "ready", "value", "value2"]),
    "spsc_single_slot": (_spsc_single_slot, ["slot", "tail", "value"]),
}

# Deterministic, human-legible name/value pools -- varying identifiers and
# literal values across variants avoids training on N copies of one
# memorized snippet while keeping each variant trivially reviewable by eye.
_NAME_POOL = ["alpha", "beta", "gamma", "delta", "epsilon"]
_VALUE_POOL = [42, 7, 128, 255, 1001]


def _iter_variants(count_per_archetype: int) -> Iterator[Tuple[str, Dict[str, Any]]]:
    for archetype_id, (_fn, fields) in _ARCHETYPES.items():
        for i in range(count_per_archetype):
            suffix = _NAME_POOL[i % len(_NAME_POOL)]
            values = {"value": _VALUE_POOL[i % len(_VALUE_POOL)], "value2": _VALUE_POOL[(i + 1) % len(_VALUE_POOL)]}
            params: Dict[str, Any] = {"test_name": f"{archetype_id}_{suffix}"}
            for field in fields:
                if field in values:
                    params[field] = values[field]
                else:
                    params[field] = f"{field}_{suffix}"
            yield archetype_id, params


def _call_sandbox(client: httpx.Client, sandbox_url: str, source: str) -> Dict[str, Any]:
    resp = client.post(f"{sandbox_url}/loom-check", json={"source": source, "edition": "2021"})
    resp.raise_for_status()
    return resp.json()


def _record(request_id: str, source: str, result: Dict[str, Any], attempt: int, max_attempts: int) -> Dict[str, Any] | None:
    # Mirrors graph.synthesis._record_loom_training_example's own filter:
    # never fabricate a label for an inconclusive/timed-out sandbox result.
    if result.get("compiles") is None or result.get("timed_out"):
        return None
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "source": source,
        "compiles": result.get("compiles"),
        "passed": result.get("passed"),
        "output_tail": (result.get("output_tail") or "")[-_OUTPUT_TAIL_MAX_CHARS:],
        "duration_ms": result.get("duration_ms"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sandbox-url", default=DEFAULT_SANDBOX_URL)
    parser.add_argument("--count", type=int, default=3, help="variants per archetype (2 sandbox calls each: broken + fixed)")
    args = parser.parse_args()

    total_pairs = len(_ARCHETYPES) * args.count
    written = 0
    with httpx.Client(timeout=_HTTP_TIMEOUT_S) as client:
        for idx, (archetype_id, params) in enumerate(_iter_variants(args.count), start=1):
            fn = _ARCHETYPES[archetype_id][0]
            request_id = f"loom-seed-{archetype_id}-{params['test_name']}-{uuid.uuid4().hex[:8]}"
            print(f"[{idx}/{total_pairs}] {request_id} ...", file=sys.stderr)

            broken_source = fn(params, broken=True)
            fixed_source = fn(params, broken=False)

            t0 = time.monotonic()
            broken_result = _call_sandbox(client, args.sandbox_url, broken_source)
            print(f"  broken: compiles={broken_result.get('compiles')} passed={broken_result.get('passed')} "
                  f"({time.monotonic() - t0:.1f}s)", file=sys.stderr)
            record = _record(request_id, broken_source, broken_result, attempt=1, max_attempts=2)
            if record is not None:
                print(json.dumps(record, ensure_ascii=False))
                written += 1

            t0 = time.monotonic()
            fixed_result = _call_sandbox(client, args.sandbox_url, fixed_source)
            print(f"  fixed:  compiles={fixed_result.get('compiles')} passed={fixed_result.get('passed')} "
                  f"({time.monotonic() - t0:.1f}s)", file=sys.stderr)
            record = _record(request_id, fixed_source, fixed_result, attempt=2, max_attempts=2)
            if record is not None:
                print(json.dumps(record, ensure_ascii=False))
                written += 1

    print(f"Done: {written} determinate JSONL records written from {total_pairs} archetype pairs.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

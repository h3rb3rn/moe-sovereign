#!/usr/bin/env python3
"""
LLM Role Suitability Test — Evaluates models for Planner and Judge roles.

Tests each available LLM on two tasks:
1. PLANNER: Decompose a multi-domain request into structured JSON subtasks
2. JUDGE: Score a pre-generated expert response on quality (0-10 JSON)

Measures: success (valid JSON output), latency, output quality, token usage.

Usage:
  python3 benchmarks/llm_role_suitability_test.py
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime

import httpx

# Ollama inference nodes — configure via environment or edit this list.
# Example: INFERENCE_NODES='[{"name":"gpu-1","url":"http://10.0.0.1:11434","gpu":"RTX3060"}]'
_nodes_env = os.getenv("INFERENCE_NODES", "")
if _nodes_env.strip():
    NODES = json.loads(_nodes_env)
else:
    NODES = [
        {"name": "gpu-node-1", "url": "http://localhost:11434", "gpu": "default"},
    ]

# Test prompts
PLANNER_PROMPT = """You are an orchestrator. Decompose this request into 1-4 JSON subtasks.
Answer EXCLUSIVELY with a JSON array. No text, no markdown.
Each object MUST have "task" (string) and "category" (string).
Valid categories: code_reviewer, technical_support, reasoning, research, math, legal_advisor

Request: "Review the Python Flask app for SQL injection vulnerabilities, calculate the CVSS score for any findings, and check if GDPR Article 25 requires additional input validation."

JSON array:"""

JUDGE_PROMPT = """Rate the following expert response on a scale of 0-10.
Respond ONLY with JSON: {"score": <number>, "reasoning": "<one sentence>"}

Question: "What is the subnet mask for 10.42.155.160/27?"
Expert Response: "The subnet mask for a /27 network is 255.255.255.224. This provides 30 usable host addresses (2^5 - 2 = 30). The network address is 10.42.155.160 and the broadcast address is 10.42.155.191."

JSON:"""

EXPECTED_PLANNER_CATEGORIES = {"code_reviewer", "math", "legal_advisor", "technical_support", "reasoning", "research"}


async def get_available_models(node: dict) -> list:
    """Get all models available on a node."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{node['url']}/api/tags")
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


async def test_model(node: dict, model: str, role: str, prompt: str) -> dict:
    """Test a model in a specific role (planner or judge)."""
    t0 = time.time()
    result = {
        "model": model, "node": node["name"], "role": role,
        "success": False, "valid_json": False, "latency_s": 0,
        "tokens": 0, "output": "", "error": "",
    }

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{node['url']}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                    "temperature": 0.1,
                },
            )
            result["latency_s"] = round(time.time() - t0, 1)

            if r.status_code != 200:
                result["error"] = f"HTTP {r.status_code}"
                return result

            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            result["tokens"] = usage.get("total_tokens", 0)
            result["output"] = content[:500]

            if not content.strip():
                result["error"] = "Empty response"
                return result

            # Validate JSON output
            # Strip markdown fences
            clean = re.sub(r'^```\w*\n?', '', content.strip())
            clean = re.sub(r'\n?```$', '', clean).strip()

            if role == "planner":
                match = re.search(r'\[.*\]', clean, re.S)
                if match:
                    parsed = json.loads(match.group())
                    if isinstance(parsed, list) and len(parsed) > 0:
                        has_task = all("task" in t for t in parsed)
                        has_cat = all("category" in t for t in parsed)
                        categories = {t.get("category", "") for t in parsed}
                        relevant = categories & EXPECTED_PLANNER_CATEGORIES
                        result["valid_json"] = True
                        result["success"] = has_task and has_cat and len(relevant) >= 2
                        result["details"] = {
                            "num_tasks": len(parsed),
                            "categories": list(categories),
                            "has_task_field": has_task,
                            "has_category_field": has_cat,
                            "relevant_categories": len(relevant),
                        }

            elif role == "judge":
                match = re.search(r'\{.*\}', clean, re.S)
                if match:
                    parsed = json.loads(match.group())
                    score = parsed.get("score")
                    if score is not None and isinstance(score, (int, float)):
                        result["valid_json"] = True
                        result["success"] = 0 <= score <= 10
                        result["details"] = {
                            "score": score,
                            "has_reasoning": bool(parsed.get("reasoning")),
                        }

    except json.JSONDecodeError:
        result["error"] = "Invalid JSON in response"
    except Exception as e:
        result["error"] = str(e)[:100]
        result["latency_s"] = round(time.time() - t0, 1)

    return result


async def main():
    print("LLM Role Suitability Test")
    print("=" * 72)

    # Discover all models across all nodes
    all_models = {}
    for node in NODES:
        models = await get_available_models(node)
        for m in models:
            # Skip very large models that won't fit / too slow
            if m not in all_models:
                all_models[m] = node

    # Filter to reasonable models (skip duplicates, keep one node per model)
    # Prefer faster nodes
    test_models = {}
    for m, node in sorted(all_models.items()):
        base = m.split(":")[0]
        if base not in test_models:
            test_models[base] = (m, node)

    print(f"Found {len(test_models)} unique models across {len(NODES)} nodes\n")

    results = []

    for i, (base, (model, node)) in enumerate(sorted(test_models.items()), 1):
        print(f"[{i}/{len(test_models)}] {model} @ {node['name']}")

        # Test as Planner
        print(f"  Planner...", end="", flush=True)
        pr = await test_model(node, model, "planner", PLANNER_PROMPT)
        status = "OK" if pr["success"] else ("JSON" if pr["valid_json"] else "FAIL")
        print(f" {status} ({pr['latency_s']}s, {pr['tokens']} tok)")
        results.append(pr)

        # Test as Judge
        print(f"  Judge...", end="", flush=True)
        jr = await test_model(node, model, "judge", JUDGE_PROMPT)
        status = "OK" if jr["success"] else ("JSON" if jr["valid_json"] else "FAIL")
        print(f" {status} ({jr['latency_s']}s, {jr['tokens']} tok)")
        results.append(jr)

        print()

    # Summary table
    print("=" * 72)
    print("RESULTS SUMMARY")
    print("=" * 72)
    print(f"{'Model':<35} {'Planner':>8} {'Judge':>8} {'P-Lat':>7} {'J-Lat':>7}")
    print("-" * 72)

    models_tested = set()
    for r in results:
        base = r["model"].split(":")[0]
        if base in models_tested:
            continue
        models_tested.add(base)

        p = next((x for x in results if x["model"] == r["model"] and x["role"] == "planner"), None)
        j = next((x for x in results if x["model"] == r["model"] and x["role"] == "judge"), None)

        p_status = "OK" if p and p["success"] else ("JSON" if p and p["valid_json"] else "FAIL")
        j_status = "OK" if j and j["success"] else ("JSON" if j and j["valid_json"] else "FAIL")
        p_lat = f"{p['latency_s']}s" if p else "—"
        j_lat = f"{j['latency_s']}s" if j else "—"

        print(f"  {r['model']:<33} {p_status:>8} {j_status:>8} {p_lat:>7} {j_lat:>7}")

    # Save results
    out = {
        "timestamp": datetime.utcnow().isoformat(),
        "models_tested": len(test_models),
        "results": results,
    }
    out_path = "benchmarks/results/llm_role_suitability.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

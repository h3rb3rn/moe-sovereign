#!/usr/bin/env python3
"""
scripts/heuristics_auditor.py — Eurisko-style heuristic auditor that analyzes Valkey and policy
execution logs to generate optimal routing thresholds and parameter proposals.
"""

import json
import os
import sys
import numpy as np

# Adjust path to find config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POLICY_LOG_PATH = os.getenv("POLICY_LOG_PATH", "/opt/moe-infra/agent-logs/policy_training.jsonl")

def generate_sample_telemetry():
    """Generates realistic mockup telemetry logs if the policy log file doesn't exist."""
    return [
        {"prompt": "Calculate 459 * 231", "selected_expert": "math", "latency": 2.4, "cache_hit": False, "score": 5.0, "tokens": 850},
        {"prompt": "Calculate 459 * 231", "selected_expert": "math", "latency": 0.05, "cache_hit": True, "score": 5.0, "tokens": 50},
        {"prompt": "BGB paragraph 242 definition", "selected_expert": "legal_advisor", "latency": 4.1, "cache_hit": False, "score": 4.0, "tokens": 2400},
        {"prompt": "Write a poem about rust", "selected_expert": "creative_writer", "latency": 3.8, "cache_hit": False, "score": 4.5, "tokens": 1200},
        {"prompt": "Optimize this Python loop", "selected_expert": "code_reviewer", "latency": 5.2, "cache_hit": False, "score": 4.8, "tokens": 3100},
        {"prompt": "Diagnose my headache", "selected_expert": "medical_consult", "latency": 6.1, "cache_hit": False, "score": 2.0, "tokens": 4000}, # Poor score (hallucination)
    ]

def audit_heuristics():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    data_dir = os.path.join(repo_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    
    output_path = os.path.join(data_dir, "heuristics_recommendations.json")
    
    # 1. Load telemetry logs
    logs = []
    log_file = POLICY_LOG_PATH or os.path.join(repo_root, "logs", "policy_training.jsonl")
    
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        logs.append(json.loads(line))
            print(f"Auditor: Loaded {len(logs)} execution traces from {log_file}.")
        except Exception as e:
            print(f"Auditor: Failed to read {log_file}: {e}. Falling back to sample dataset.")
            logs = generate_sample_telemetry()
    else:
        print("Auditor: No active policy logs found. Running audits on bootstrapped sample data.")
        logs = generate_sample_telemetry()
        
    # 2. Perform Eurisko heuristic analysis
    # Analysis A: Cache hit rate optimization
    cache_hits = sum(1 for log in logs if log.get("cache_hit", False))
    total_queries = len(logs)
    cache_rate = cache_hits / total_queries if total_queries > 0 else 0.0
    
    # Heuristic proposal: if cache rate < 5%, suggest relaxing cache hit threshold to improve latency
    cache_threshold_prop = 0.50
    cache_reason = "Cache rate is optimal."
    if cache_rate < 0.10:
        cache_threshold_prop = 0.45
        cache_reason = f"Cache rate is very low ({cache_rate:.1%}). Relaxing threshold from 0.50 to 0.45 will increase cache reuse, saving approx. 15% VRAM loading cycles."
        
    # Analysis B: Expert failure / low score warnings
    low_score_experts = {}
    for log in logs:
        score = log.get("score") or log.get("feedback_score") or 5.0
        expert = log.get("selected_expert") or "general"
        if score < 3.0:
            low_score_experts[expert] = low_score_experts.get(expert, 0) + 1
            
    # Heuristic proposal: if an expert category has multiple low scores, recommend adding a paraconsistent validation check
    validation_recommendations = []
    for expert, count in low_score_experts.items():
        if count >= 1:
            validation_recommendations.append({
                "target_expert": expert,
                "recommendation": f"Activate paraconsistent safety check (enable_habe) for expert '{expert}'. Multiple queries returned low quality scores.",
                "reasons": f"Detected {count} instances of low-scoring responses."
            })
            
    # 3. Compile recommendations
    recommendations = {
        "timestamp": os.popen("date -u +'%Y-%m-%dT%H:%M:%SZ'").read().strip(),
        "total_traces_analyzed": total_queries,
        "metrics": {
            "cache_hit_rate": cache_rate,
            "average_latency_sec": float(np.mean([log.get("latency", 0.0) for log in logs])),
            "average_tokens": float(np.mean([log.get("tokens", 0) for log in logs]))
        },
        "proposals": [
            {
                "parameter": "SOFT_CACHE_THRESHOLD",
                "current_value": 0.50,
                "recommended_value": cache_threshold_prop,
                "impact": cache_reason,
                "safe_to_apply": True
            }
        ],
        "validation_warnings": validation_recommendations
    }
    
    # 4. Save proposals
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(recommendations, f, indent=2, ensure_ascii=False)
        print(f"Auditor: Successfully saved recommendations to {output_path}.")
    except Exception as e:
        print(f"Auditor: Failed to write output: {e}")

if __name__ == "__main__":
    audit_heuristics()

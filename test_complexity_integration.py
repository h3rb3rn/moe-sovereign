import sys
import os
import logging

# Configure basic logging to see the classifier logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

from complexity_estimator import estimate_complexity, complexity_routing_hint

# Test queries covering different complexity levels
test_queries = {
    # Memory recall
    "what did i say about neo4j earlier?": "memory_recall",
    # Trivial
    "Was ist die Hauptstadt von Frankreich?": "trivial",
    # Complex
    "Compare the performance of ROCm and CUDA for sparse MoE models step by step.": "complex",
    # Moderate
    "def hello_world(): print('hello')": "moderate",
    "Docker subnetz cidr § 123 BGB": "moderate",
}

print("Running complexity integration tests...")
passed = True
for query, expected in test_queries.items():
    res = estimate_complexity(query)
    if res == expected:
        print(f"✅ PASS: {query!r} -> {res}")
    else:
        print(f"❌ FAIL: {query!r} -> {res} (expected: {expected})")
        passed = False

if passed:
    print("🎉 All integration tests passed successfully!")
    sys.exit(0)
else:
    print("🚨 Some integration tests failed.")
    sys.exit(1)

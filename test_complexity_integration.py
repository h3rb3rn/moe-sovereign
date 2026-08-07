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

def run_checks() -> bool:
    """Run the manual smoke matrix without side effects during module import."""
    print("Running complexity integration tests...")
    passed = True
    for query, expected in test_queries.items():
        result = estimate_complexity(query)
        if result == expected:
            print(f"✅ PASS: {query!r} -> {result}")
        else:
            print(f"❌ FAIL: {query!r} -> {result} (expected: {expected})")
            passed = False
    print(
        "🎉 All integration tests passed successfully!"
        if passed
        else "🚨 Some integration tests failed."
    )
    return passed


if __name__ == "__main__":
    raise SystemExit(0 if run_checks() else 1)

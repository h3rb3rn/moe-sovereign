.PHONY: benchmark benchmark-smoke setup clean

# Run full benchmark (requires running MoE API)
benchmark:
	python benchmark_graphrag.py --fixtures data/fixtures/sample_100.jsonl

# Quick smoke test on 3 items
benchmark-smoke:
	python benchmark_graphrag.py --fixtures data/fixtures/sample_100.jsonl \
		--difficulties fact_retrieval

# Start Neo4j benchmark environment
neo4j-up:
	docker compose up -d neo4j-bench
	@echo "Waiting for Neo4j to be ready..."
	@until docker compose exec neo4j-bench wget -qO- http://localhost:7474/ > /dev/null 2>&1; do sleep 2; done
	@echo "Neo4j ready."

neo4j-down:
	docker compose down

setup:
	pip install -r requirements.txt

clean:
	rm -rf results/*.json

# MoE Sovereign — Agent Guidelines & Permanent Tasks

This file outlines the system conventions, codebase structure, and permanent development tasks for AI coding assistants working on the **MoE Sovereign** project.

---

## 1. System Overview: The Middleware Gateway

**MoE Sovereign** acts as an intelligent **Middleware Gateway** between downstream inference servers (Ollama, local SLMs, Cloud APIs) and AI Tool APIs (MCP servers, SearXNG Web Search, Skills, Neo4j GraphRAG). 
*   **The Controller:** A specialized model (e.g. `Sovereign-14B-Controller`) acts as the Orchestrator, running the Planner, Router, and Judge functions.
*   **The Goal:** Optimize model selection, dynamically compile expert templates, execute native tools, and resolve contradictions using paraconsistent logic. We do *not* train models to replace downstream expert LLMs.

---

## 2. Permanent Agent Tasks (Dauerhafte Aufgaben)

As an AI assistant, you must maintain and execute the following ongoing tasks:

### Task 1: Co-Create and Maintain the JMoE Research Paper / Whitepaper
*   **Objective:** Parallel to code development, document all new theoretical insights and architecture designs in a new academic paper under the path:
    📁 `~/whitepaper/arxiv_paper/jmoe_paper.tex`
*   **Trigger:** Whenever you implement or refine routing logic, Optimal Transport algorithms, paraconsistent logic arbitration, or RLSF policy gradients, you **must** update the corresponding LaTeX sections of `jmoe_paper.tex` to ensure the academic documentation mirrors the production implementation.
*   **Compilation:** Recompile the paper using `pdflatex` or `latexmk` inside the `~/whitepaper/arxiv_paper/` folder after edits.

### Task 2: Continuous RLSF & Feedback Optimization
*   **Objective:** Maintain and optimize the reinforcement learning loop.
*   **Details:** Verify that the Postgres `dynamic_template_feedback_log` correctly matches Valkey Thompson scores, and that the daily model metadata indexer (`scripts/index_models_metadata.py`) correctly scores active resources.

### Task 3: Local-Only Compliance Enforcement
*   **Objective:** Guarantee data sovereignty.
*   **Details:** Ensure any code change in `services/dynamic_router.py`, `services/routing.py`, or `graph/synthesis.py` strictly respects the `local_only` flag, preventing any data leak to non-local endpoints when active.

---

## 3. Technology Stack & Coding Conventions

*   **Database:** PostgreSQL (psycopg3) and Valkey/Redis (redis-py). Use async connections when possible.
*   **Vector Search:** ChromaDB.
*   **Graph Database:** Neo4j (bolt protocol).
*   **Inference:** OpenAI-compatible APIs (hosted via Ollama or LiteLLM).
*   **Schema Enforcement:** Always validate dynamic JSON templates against the predefined configuration schema.

---

## 4. GitOps Publish-Workflow (Sync & Normalization)

All code and documentation edits must be performed in the dev repository first, then synchronized and published using the following pipeline:
1. **Develop:** Make edits and commit them in the dev repository (`/opt/moe-sovereign`).
2. **Pull:** Change directory to the publish repository (`/opt/deployment/Github/moe-sovereign`) and pull the latest state: `git checkout main && git pull origin main`.
3. **Sync:** Return to the dev repository and run the synchronization script: `bash scripts/sync-to-publish.sh`.
4. **Branch:** In the publish repository, create a **feature branch**: `git checkout -b <branch-name>`.
5. **Commit:** Stage and commit: `git add -A && git commit -m 'Sync from dev: <description>'`.
6. **Push & PR:** Push the branch to GitHub: `git push origin <branch-name>`. Open a Pull Request (PR) on GitHub. **Never push directly to the `main` branch of the public GitHub repository.**

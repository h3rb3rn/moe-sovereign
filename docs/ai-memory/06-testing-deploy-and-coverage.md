# Testing, Deploy, and Coverage — MoE Sovereign (moe-infra)

## Clean Test Ritual

Before any proof run:

1. Rebuild and restart the affected service:
   ```
   sudo docker compose build <service> && sudo docker compose up -d <service>
   ```
2. Verify startup logs are clean (no new errors beyond known pre-existing
   warnings such as the NiFi self-signed-cert warning).
3. Run the targeted proof.
4. Run broader proof only after targeted proof passes.

Do not recycle unknown container, runtime, or session state for clean
verification.

## Verification Layers

Run in this order — never skip a layer to reach the next:

| Layer | Command / Method | Scope |
|---|---|---|
| 1. Syntax / static | `python3 -m py_compile <file>` or linting | Touched files only |
| 2. Domain / unit | `python3 -m pytest tests/ -q` | Full test suite (currently 195+ tests) |
| 3. API / persistence | In-container Python script against live DB and ChromaDB | Specific seam under test |
| 4. Integration (E2E) | `http://192.168.155.225:8002/v1/chat/completions`, model `moe-auto` | Full pipeline round-trip |
| 5. External / LUMI-G | SLURM job, remote training | Only when layers 1–4 pass |

## GUI Rule

Admin UI tests validate rendering, visible interaction, and configuration
intent. They do not own routing or backend semantics.

## Service Names

| Service | Command suffix | Purpose |
|---|---|---|
| `langgraph-app` | `main.py` | Core orchestrator |
| `moe-admin` | `admin_ui/` | Admin UI |
| `mcp-precision` | `mcp_server/` | MCP tool server |

## Coverage Log

Track current coverage here (update after each verification session):

- Backend / domain: `pytest tests/` — 195+ tests (as of 2026-06-12)
- API / persistence: in-container E2E for seams under active development
- GUI / browser: Admin UI manual smoke test
- Live / external: MoE-API round-trip via `moe-auto`
- Known gaps:
  - Router model training on LUMI-G (requires renewed SSH cert)
  - Sovereign-14B SFT pipeline (TASK-7, pending)
  - Dynamic system prompts in dataset generation (TASK-7, pending)

## Important Test Files

- `tests/test_dynamic_router.py` — IMoE gating network (6 tests)
- `tests/test_context_index.py` — context budget resolution (24 tests)
- `tests/test_dynamic_router.py` — ChromaDB cache, Thompson sampling,
  local_only compliance

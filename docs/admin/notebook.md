# Notebook (JupyterLite)

The Notebook page (`/notebook`) embeds a browser-only JupyterLite
environment alongside copy-paste-ready snippets that talk to the
orchestrator API. Power users can prototype against the live graph
without installing a Python kernel anywhere — JupyterLite ships its own
WebAssembly Python.

> **Foundry equivalence.** This is the JupyterLite-based answer to
> Palantir Foundry's Code Workbook: an in-product notebook surface that
> can interact with the platform's APIs.

## Configuration

The page renders an `<iframe>` pointing at `JUPYTERLITE_URL`. The default
points at the public demo:

```
JUPYTERLITE_URL=https://jupyterlite.github.io/demo/lab/index.html
```

For air-gapped or sovereignty-strict deployments, host JupyterLite locally
(static files, no server needed) and override the env:

```
JUPYTERLITE_URL=https://lab.your-domain.example/lab/index.html
```

## Snippets

The right-hand panel exposes five copy-buttons, each preloading a typical
workflow:

| Snippet | What it does |
|---------|-------------|
| **Export bundle** | `GET /v1/graph/knowledge/export?domain=…` and load the JSON into a Pandas DataFrame |
| **Stage pending bundle** | `POST /v1/graph/knowledge/import/pending` with a small example bundle |
| **Search** | Vector + graph hybrid search via `/v1/graph/search` |
| **Read-only Cypher** | `POST /v1/graph/cypher/read` from inside the notebook |
| **Lineage runs** | `GET /v1/lineage/runs?limit=…` and tabulate by status |

Each snippet is parametrised with the orchestrator's base URL relative to
the deployment, so they work as-is once authenticated.

## Authentication inside the notebook

JupyterLite runs in the same browser context as the admin UI but **does
not** automatically share cookies across origins. For deployments where
the orchestrator is on a different host, generate a personal API key in
the user portal and paste it as the `Authorization: Bearer …` header in
the notebook snippets.

## What JupyterLite does *not* do

- It does not get GPU access — kernels run in WebAssembly only
- It does not persist state across browser tabs unless you manually
  download notebooks
- It is intentionally not a replacement for a real JupyterHub deployment
  — for heavy data science, run a dedicated Hub and link out to it
  instead of overriding `JUPYTERLITE_URL`

## Translations

`nav.notebook`, `page.notebook`, `lbl.snippets`, `btn.copy`, `lbl.snippet_export_bundle`,
`lbl.snippet_pending_import`, `lbl.snippet_search`, `lbl.snippet_cypher`,
`lbl.snippet_runs` — present in all four language files.

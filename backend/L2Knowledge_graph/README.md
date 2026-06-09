# L2 Knowledge Graph — `backend/L2Knowledge_graph`

The L2 Knowledge Graph module, built on **PostgreSQL 16 + Apache AGE**. This is a real
Python package imported by the running app — not a copy. Two of the three L2 dimensions
are implemented:

- **EntityMap** — *what exists* (Crew/Vessel/Port/Certificate/Contract). Requires AGE.
- **OpsMap** — *how work actually flows* (process mining over the crew-change workflow).
  Mined in Python from the captured event log, so it works under the **fallback**
  backend too; AGE is only needed to persist the mined model. See `docs/OPSMAP_DESIGN.md`.

```
from L2Knowledge_graph.routes import router as graph_router        # main.py
from L2Knowledge_graph.entity_map import build_entity_map, ...      # EntityMap queries/builder
from L2Knowledge_graph.ops_map import build_process_graph, ...      # OpsMap mining
from L2Knowledge_graph.graph_db import run_cypher, age_enabled      # Cypher access
from L2Knowledge_graph.compliance_graph import build_compliance_subgraph
```

## Layout

```
backend/L2Knowledge_graph/
├── __init__.py
├── entity_map.py          # EntityMap schema + builder + queries
│                          #   (search_crew, traverse_crew, search_subgraph, facets, summary)
├── ops_map.py             # OpsMap: event-log capture + process mining
│                          #   (record_event, build_process_graph, process_variants,
│                          #    bottlenecks, conformance, persist_process_model)
├── graph_db.py            # the ONLY Cypher access layer (run_cypher, ensure_graph)
├── compliance_graph.py    # rules-as-data + Python fallback subgraph builder
├── routes.py              # FastAPI router → /api/v1/graph  (registered in main.py)
├── scripts/
│   ├── seed_entity_map.py   # build the EntityMap from the crew table
│   ├── seed_graph.py        # seed the compliance subgraph (AGE)
│   ├── render_entity_map.py # render the graph to PNG
│   ├── gen_l2_pdfs.py       # explainer PDFs
│   └── gen_summary_pdf.py   # implementation-summary PDF
├── docs/                  # design doc, PDFs, rendered PNGs
├── deploy/                # postgres-age.Dockerfile (AGE + pgvector image)
└── frontend_reference/    # NON-FUNCTIONAL copies of the UI files (see below)
```

## Depends on (these stay in the main app — not moved)
`config.settings` · `database.db` (engine/session) · `database.crew_repository`.

## External wiring (re-pointed to this package)
- `main.py` → `from L2Knowledge_graph.routes import router as graph_router`
- `agents/compliance_agent.py` → `from L2Knowledge_graph.compliance_graph import ...`

## Frontend
The web UI for this module **cannot live here** — Next.js resolves routes from
`frontend/src/app/`, so the live files stay in the frontend app:

| Live file (functional) | Reference copy here |
|------------------------|---------------------|
| `frontend/src/app/graph/page.tsx` | `frontend_reference/app/graph/page.tsx` |
| `frontend/src/components/graph/EntityGraph.tsx` | `frontend_reference/components/graph/EntityGraph.tsx` |
| `frontend/src/lib/api.ts` (the `graphApi` block) | `frontend_reference/lib/api.graph.ts` |

The `frontend_reference/` copies are for reading only; editing them does nothing.

## API (`/api/v1/graph`)
EntityMap (requires AGE): `GET /summary` · `GET /facets` · `GET /crew/search` · `GET /crew/{id}/traverse` · `GET /subgraph` · `GET /node/{id}`

OpsMap (works under fallback too): `GET /opsmap/summary` · `GET /opsmap/process` · `GET /opsmap/variants` · `GET /opsmap/bottlenecks` · `GET /opsmap/conformance` · `POST /opsmap/persist` (AGE only)

## Run (from `backend/`, with `GRAPH_BACKEND=age`)
```bash
python -m scripts.seed_crew                       # crew table (general seeder, stays in scripts/)
python -m L2Knowledge_graph.scripts.seed_entity_map   # build the EntityMap graph
uvicorn main:app --port 8000                      # serves /api/v1/graph
```

OpsMap self-populates from live workflow events. To inspect / demo it offline:
```bash
python -m L2Knowledge_graph.scripts.seed_ops_map --demo            # mine 4 sample traces, print model
GRAPH_BACKEND=age python -m L2Knowledge_graph.scripts.seed_ops_map --demo --persist  # + write DFG to AGE
```
Docs / images:
```bash
GRAPH_BACKEND=age python -m L2Knowledge_graph.scripts.render_entity_map ..
python -m L2Knowledge_graph.scripts.gen_l2_pdfs ..
python -m L2Knowledge_graph.scripts.gen_summary_pdf ..
```

## Extra deps
`greenlet` (async DB); `fpdf2`, `networkx`, `matplotlib` (docs/images only).

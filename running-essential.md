# Running — Essential Steps

Maritime Crew Orchestrator + L2 Knowledge Graph. Run order: **infra → backend → frontend**.

| Service | URL / Port |
|---------|-----------|
| Frontend (dashboard + Graph page) | http://localhost:3000  ·  graph: http://localhost:3000/graph |
| Backend API | http://localhost:8000  ·  docs: http://localhost:8000/docs |
| Postgres + Apache AGE | localhost:5434 (user `postgres` / pw `password` / db `maritime_crew`) |
| Redis | localhost:6379 |

---

## A. Fast path (everything already set up)

The containers and venv already exist — just start them.

```bash
# 1. Infra
docker start crew-postgres crew-redis

# 2. Backend  (from repo root)
cd backend
./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
#   leave running; open a new terminal for the frontend

# 3. Frontend  (new terminal, from repo root)
cd frontend
npm run dev
```

Open **http://localhost:3000/graph**.

---

## B. First-time setup (fresh machine)

```bash
# ── 1. Infra: AGE-enabled Postgres + Redis ──────────────────────────────────
docker run -d --name crew-postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=password -e POSTGRES_DB=maritime_crew \
  -p 5434:5432 apache/age:release_PG16_1.6.0
docker run -d --name crew-redis -p 6379:6379 redis:7-alpine

# ── 2. Backend: venv + deps ─────────────────────────────────────────────────
cd backend
python3.13 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/pip install greenlet fpdf2 networkx matplotlib   # extras (async DB + docs/images)

# ── 3. Config: point at the DB and enable the graph ─────────────────────────
#    backend/.env must contain:
#      DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5434/maritime_crew
#      REDIS_URL=redis://localhost:6379
#      GRAPH_BACKEND=age
#      ANTHROPIC_API_KEY=sk-ant-...

# ── 4. Seed data ────────────────────────────────────────────────────────────
./venv/bin/python -m scripts.seed_crew                      # 40 crew rows -> Postgres
./venv/bin/python -m L2Knowledge_graph.scripts.seed_entity_map   # build the EntityMap graph

# ── 5. Run backend ──────────────────────────────────────────────────────────
./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000

# ── 6. Frontend (new terminal) ──────────────────────────────────────────────
cd frontend
npm install
npm run dev
```

---

## C. Verify it works

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/graph/summary                       # 90 nodes / 189 edges
curl "http://localhost:8000/api/v1/graph/subgraph?rank=Master"        # graph for the Master rank
curl "http://localhost:8000/api/v1/graph/crew/SOF-2000/traverse"      # one crew's relationships
```

In the browser: **/graph** → pick Rank / Certificate / Port → **Search**.

---

## D. Regenerate the L2 docs & images (optional)

```bash
cd backend
GRAPH_BACKEND=age ./venv/bin/python -m L2Knowledge_graph.scripts.render_entity_map ..   # PNGs
./venv/bin/python -m L2Knowledge_graph.scripts.gen_l2_pdfs ..                            # explainer PDFs
./venv/bin/python -m L2Knowledge_graph.scripts.gen_summary_pdf ..                        # summary PDF
```

---

## E. Stop / housekeeping

```bash
# stop the app: Ctrl+C in the backend and frontend terminals
docker stop crew-postgres crew-redis           # stop infra (data is preserved)
docker start crew-postgres crew-redis          # bring it back
# full reset (DESTROYS graph + crew data):
#   docker rm -f crew-postgres crew-redis   then redo section B
```

### Common gotchas
- **`Connection refused` / DB init failed** → the Postgres container is stopped: `docker start crew-postgres`.
- **Graph endpoints return 503** → `GRAPH_BACKEND=age` missing from `backend/.env`, or the graph isn't seeded (run section B step 4).
- **`/graph` shows only ~14 crew** → intentional legibility cap; apply a filter to narrow it.
- **Empty crew list after re-seeding** → stale Redis cache: `docker exec crew-redis redis-cli del crew:signon crew:signoff`.

"""
Context-graph access layer (Apache AGE inside the existing PostgreSQL DB).

This module is the ONLY place that speaks Cypher. It sits beside crew_repository
in the data-access layer (see ARCHITECTURE / the Context-Graph review doc): the
relational `crew` table and the `maritime` graph live in the same Postgres
instance, so no new datastore is added.

Backend selection is controlled by settings.graph_backend:

  - "fallback" (default): AGE is NOT touched. Connections are left alone and the
    compliance subgraph is built in Python from crew + rule data
    (database.compliance_graph.build_compliance_subgraph). This makes the feature
    demoable TODAY without swapping the Postgres image.
  - "age": every connection runs `LOAD 'age'` + sets the search_path, and
    run_cypher() executes openCypher against the `maritime` graph. Requires a
    Postgres image with the AGE extension (see docker-compose.yml) and a seeded
    graph (scripts/seed_graph.py).

Either way the subgraph returned to the rest of the app has the SAME shape, so the
agent, the WebSocket payload and the frontend never need to know which backend ran.
"""
import json
from typing import Any, Dict, List

import structlog
from sqlalchemy import event, text

from config import settings
from database.db import AsyncSessionLocal, engine

log = structlog.get_logger()

GRAPH_NAME = "maritime"


def age_enabled() -> bool:
    """True when AGE is the configured backend."""
    return str(getattr(settings, "graph_backend", "fallback")).lower() == "age"


# AGE requires LOAD 'age' + search_path on EVERY connection. Registering this hook
# only when AGE is enabled is deliberate: if AGE is not installed, running
# `LOAD 'age'` on each connect would break ordinary crew-table queries too.
if age_enabled():

    @event.listens_for(engine.sync_engine, "connect")
    def _load_age(dbapi_conn, _connection_record):  # pragma: no cover - infra glue
        try:
            cur = dbapi_conn.cursor()
            cur.execute("LOAD 'age';")
            cur.execute('SET search_path = ag_catalog, "$user", public;')
            cur.close()
        except Exception as exc:
            log.warning("age.load_failed", error=str(exc))


async def run_cypher(query: str) -> List[Dict[str, Any]]:
    """Run a Cypher query against the `maritime` graph and parse the agtype rows.

    Returns [] when AGE is not the active backend, so callers can always call it
    and fall back to the Python builder if the result is empty.
    """
    if not age_enabled():
        return []
    sql = text(f"SELECT * FROM cypher('{GRAPH_NAME}', $$ {query} $$) AS (v agtype);")
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql)).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                out.append(json.loads(r[0]))
            except Exception:
                out.append({"raw": str(r[0])})
        return out


async def ensure_graph() -> None:
    """Create the AGE extension and the `maritime` graph if missing (no-op under
    the fallback backend). Safe to call repeatedly; used by scripts/seed_graph.py."""
    if not age_enabled():
        log.info("graph.ensure.skipped", reason="graph_backend != age")
        return
    async with AsyncSessionLocal() as session:
        await session.execute(text("CREATE EXTENSION IF NOT EXISTS age;"))
        await session.execute(text("LOAD 'age';"))
        await session.execute(text('SET search_path = ag_catalog, "$user", public;'))
        try:
            await session.execute(text(f"SELECT create_graph('{GRAPH_NAME}');"))
        except Exception:
            # create_graph errors if the graph already exists — fine.
            pass
        await session.commit()
    log.info("graph.ensure.ok", graph=GRAPH_NAME)

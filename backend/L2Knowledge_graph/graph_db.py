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
            cur.execute('SET search_path = "$user", public, ag_catalog;')
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
    # NB: use exec_driver_sql (raw SQL straight to the driver), NOT text(). Cypher's
    # label/relationship syntax (e.g. `[:HOLDS]`, `(n:Crew)`) contains colons that
    # SQLAlchemy's text() would mis-parse as `:param` bind placeholders. The query is
    # already fully inlined inside the `$$ ... $$` dollar-quoted block, so no binding
    # is needed.
    sql = f"SELECT * FROM cypher('{GRAPH_NAME}', $$ {query} $$) AS (v agtype);"
    async with AsyncSessionLocal() as session:
        conn = await session.connection()
        # AGE's cypher() lives in ag_catalog and needs the extension LOADed + the
        # search_path set on THIS connection. The connect-event hook is unreliable
        # through the async asyncpg adapter (the sync cursor call there can no-op),
        # so we (idempotently) ensure it per call. LOAD is cheap and a single
        # cypher() round-trip stays well under the latency budget.
        await conn.exec_driver_sql("LOAD 'age';")
        await conn.exec_driver_sql('SET search_path = ag_catalog, "$user", public;')
        rows = (await conn.exec_driver_sql(sql)).fetchall()
        # cypher() can MUTATE (MERGE/SET/CREATE), so commit — otherwise writes roll
        # back when the session closes. A commit after a read-only query is a no-op.
        await session.commit()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                out.append(json.loads(r[0]))
            except Exception:
                out.append({"raw": str(r[0])})
        # MERGE / CREATE are wrapped in an implicit transaction by the session;
        # commit so writes persist (no-op for read-only MATCH queries).
        await session.commit()
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
        await session.execute(text('SET search_path = "$user", public, ag_catalog;'))
        try:
            await session.execute(text(f"SELECT create_graph('{GRAPH_NAME}');"))
        except Exception:
            # create_graph errors if the graph already exists — fine.
            pass
        await session.commit()
    log.info("graph.ensure.ok", graph=GRAPH_NAME)

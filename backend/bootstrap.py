"""
Optional startup auto-seed (Option 1, gated by SEED_ON_STARTUP).

When SEED_ON_STARTUP=true, the app self-seeds on boot so a FRESH deploy shows data
without a manual seed step. It is written to be SAFE to run on every boot:

  • Crew table (Postgres) — seeded ONLY when empty. scripts.seed_crew DROPS and
    recreates the table, so running it over existing rows would wipe runtime state
    (match scores, pool moves); the emptiness guard prevents that.
  • EntityMap (AGE)       — rebuilt every boot when AGE is on. Idempotent (all MERGE),
    derived from the crew table, so it must run AFTER the crew seed.
  • OrgMap (AGE)          — rebuilt every boot when AGE is on. Idempotent (all MERGE);
    overlays EntityMap's Vessel/Crew nodes, so it must run AFTER EntityMap.

OpsMap needs nothing here — it mines itself from the live workflow event log.

Every step is wrapped so a failure is logged and the app still boots. Default OFF —
this is a demo / fresh-deploy convenience, not a steady-state production path.
"""
import structlog
from sqlalchemy import func, select

from config import settings
from database.crew_orm import Crew
from database.db import AsyncSessionLocal
from L2Knowledge_graph.graph_db import age_enabled

log = structlog.get_logger()


async def _crew_count() -> int:
    """Row count of the crew table (table already exists — init_db() ran first)."""
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(func.count()).select_from(Crew))).scalar_one()


async def run_startup_seed() -> None:
    """Self-seed the data layer when SEED_ON_STARTUP=true. No-op otherwise."""
    if not settings.seed_on_startup:
        return

    log.info("seed_on_startup.begin", age=age_enabled())

    # 1. Crew table — only when empty (seed_crew is destructive; never run over data).
    try:
        count = await _crew_count()
        if count == 0:
            from scripts.seed_crew import seed as seed_crew
            await seed_crew()
            log.info("seed_on_startup.crew_seeded")
        else:
            log.info("seed_on_startup.crew_present", count=count)
    except Exception as exc:  # noqa: BLE001 - log and continue so the app still boots
        log.error("seed_on_startup.crew_failed", error=str(exc))

    # 2/3. EntityMap then OrgMap — AGE only, idempotent, order matters (OrgMap overlays
    #      EntityMap). Skipped entirely under the fallback backend (no AGE graph).
    if not age_enabled():
        log.info("seed_on_startup.skip_graph", reason="GRAPH_BACKEND != age")
        log.info("seed_on_startup.done")
        return

    try:
        from L2Knowledge_graph.entity_map import build_entity_map
        summary = await build_entity_map()
        log.info("seed_on_startup.entitymap", **summary.get("nodes", {}))
    except Exception as exc:  # noqa: BLE001
        log.error("seed_on_startup.entitymap_failed", error=str(exc))

    try:
        from L2Knowledge_graph.org_map import build_org_map
        await build_org_map()
        log.info("seed_on_startup.orgmap")
    except Exception as exc:  # noqa: BLE001
        log.error("seed_on_startup.orgmap_failed", error=str(exc))

    # Rich demo OpsMap cases — the in-memory event log is reset on every restart, so
    # without this the case-highlight / Traversal views have no cases to show until a
    # live crew-change workflow runs. Pure in-memory replay, no AGE/LLM needed.
    try:
        from L2Knowledge_graph.ops_map import seed_demo_cases
        seed_demo_cases()
        log.info("seed_on_startup.opsmap_cases")
    except Exception as exc:  # noqa: BLE001
        log.error("seed_on_startup.opsmap_cases_failed", error=str(exc))

    log.info("seed_on_startup.done")
"""
Seed the L2 EntityMap dimension into the Apache AGE `maritime` graph.

Builds the canonical entity layer (Crew / Vessel / Port / Certificate / Contract
nodes + their factual relationships) from the 40 seeded crew records (20 sign-on
+ 20 sign-off), satisfying the L2 exit criterion of "20+ crew test records loaded".

Prerequisites:
    1. An AGE-enabled Postgres holding the `crew` table (docker: crew-postgres).
    2. GRAPH_BACKEND=age in backend/.env.
    3. The crew table seeded first:   python -m scripts.seed_crew

Usage:
    python -m L2Knowledge_graph.scripts.seed_entity_map
"""
import asyncio
import json

from L2Knowledge_graph.entity_map import build_entity_map


async def main() -> None:
    summary = await build_entity_map()
    print("EntityMap seeded into AGE graph 'maritime'.")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

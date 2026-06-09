"""
L3 Intelligence-Graph DEMO — shows the INPUT, the WORKFLOW (streamed live), and the
OUTPUT for one realistic sign-off.

Run:
    docker compose run --rm --no-deps backend python -m scripts.demo_l3
    # or:  cd backend && python -m scripts.demo_l3

Uses a fixed, realistic sign-on pool injected in-memory, so it's deterministic and
needs no DB / API key.
"""
import asyncio
import json
import sys
import time

import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(50))

from agents.intelligence import IntelligenceSupervisor, context_from_signoff_crew

BAR = "=" * 72
SUB = "-" * 72

# ── The departing crew member (the sign-off that triggers L3) ───────────────────
DEPARTING_CREW = {
    "crew_id": "SOF-2007", "name": "Capt. Henrik Larsen", "rank": "Chief Officer",
    "grade": "Grade A", "nationality": "Danish", "vessel": "MV Pacific Star",
    "port": "Singapore", "sign_off_date": "2026-06-12",
}

# ── The sign-on candidate pool L3 reasons over ──────────────────────────────────
POOL = [
    {"crew_id": "SNO-1001", "name": "Juan dela Cruz", "rank": "Chief Officer", "grade": "Grade A",
     "nationality": "Filipino", "port": "Singapore", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 12,
     "certifications": ["STCW Basic Safety", "GMDSS", "Advanced Fire Fighting"]},
    {"crew_id": "SNO-1002", "name": "Dmitri Sokolov", "rank": "Chief Officer", "grade": "Grade B",
     "nationality": "Russian", "port": "Rotterdam", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 9, "certifications": ["STCW Basic Safety", "GMDSS"]},
    {"crew_id": "SNO-1003", "name": "Rajesh Kumar", "rank": "Second Officer", "grade": "Grade B",
     "nationality": "Indian", "port": "Mumbai", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 8, "certifications": ["STCW Basic Safety", "GMDSS"]},
    {"crew_id": "SNO-1004", "name": "Liam O'Brien", "rank": "Chief Officer", "grade": "Grade A",
     "nationality": "Irish", "port": "Singapore", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 11, "certifications": ["STCW Basic Safety"]},  # no GMDSS
    {"crew_id": "SNO-1005", "name": "Petros Nikolaidis", "rank": "Chief Officer", "grade": "Grade A",
     "nationality": "Greek", "port": "Piraeus", "status": "Onboard", "stcw_status": "Valid",   # unavailable
     "visa_status": "Valid", "experience_years": 13, "certifications": ["STCW Basic Safety", "GMDSS"]},
    {"crew_id": "SNO-1006", "name": "Chen Wei", "rank": "Chief Engineer", "grade": "Grade A",   # wrong family
     "nationality": "Chinese", "port": "Shanghai", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 15, "certifications": ["STCW Basic Safety", "High Voltage"]},
    {"crew_id": "SNO-1007", "name": "Marco Rossi", "rank": "Second Officer", "grade": "Grade C",
     "nationality": "Italian", "port": "Genoa", "status": "Available", "stcw_status": "Expiring Soon",
     "visa_status": "Valid", "experience_years": 5, "certifications": ["STCW Basic Safety", "GMDSS"]},
    {"crew_id": "SNO-1008", "name": "Ahmed Khan", "rank": "Third Officer", "grade": "Grade B",   # 2 steps below
     "nationality": "Pakistani", "port": "Karachi", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 3, "certifications": ["STCW Basic Safety", "GMDSS"]},
]


async def crew_provider():
    return [dict(c) for c in POOL]


async def main() -> int:
    # Optional CLI overrides:  python -m scripts.demo_l3 [rank] [port]
    rank = sys.argv[1] if len(sys.argv) > 1 else DEPARTING_CREW["rank"]
    port = sys.argv[2] if len(sys.argv) > 2 else DEPARTING_CREW["port"]
    departing = {**DEPARTING_CREW, "rank": rank, "port": port}

    # ───────────────────────────────── INPUT ──────────────────────────────────────
    print(BAR); print(" L3 INTELLIGENCE GRAPH — DEMO"); print(BAR)
    print("\n[1] INPUT\n" + SUB)
    print("Trigger: a sign-off is initiated for the departing crew member.\n")
    print(f"  Departing crew : {departing['name']}  ({departing['crew_id']})")
    print(f"  Rank / grade   : {departing['rank']} / {departing['grade']}")
    print(f"  Vessel / port  : {departing['vessel']}  @  {departing['port']}")
    print(f"  Sign-off date  : {departing['sign_off_date']}")

    ctx = context_from_signoff_crew(departing, contract_period_months=6, workflow_id="demo-001")
    print("\n  → Derived vacancy context (SignOffContext) handed to the Supervisor:")
    print("    " + json.dumps(ctx.to_dict()))
    print(f"\n  Candidate pool (sign-on): {len(POOL)} seafarers")
    for c in POOL:
        print(f"    - {c['crew_id']}  {c['name']:<20} {c['rank']:<16} {c['status']:<10} @ {c['port']}")

    # ─────────────────────────────── WORKFLOW ─────────────────────────────────────
    print("\n[2] WORKFLOW  (live stream of intel_* events)\n" + SUB)
    t0 = time.perf_counter()

    async def trace(event_type, agent_name, data):
        ms = int((time.perf_counter() - t0) * 1000)
        line = f"  t+{ms:>4}ms  {event_type:<28}"
        if event_type == "intel_supervisor_started":
            line += f" delegating → {', '.join(data['investigators'])}"
        elif event_type == "intel_investigator_started":
            line += f" {data['investigator']} (pool={data['pool_size']})"
        elif event_type == "intel_investigator_completed":
            line += f" {data['investigator']}: eligible {data['eligible']}/{data['assessed']} ({data['duration_ms']}ms)"
        elif event_type == "intel_ranking":
            tops = ", ".join(f"#{c['rank_position']} {c['name']} ({c['score']})" for c in data["candidates"])
            line += f" top-{data['top_n']} → {tops}"
        elif event_type == "intel_no_crew":
            line += f" {data['message']}"
        elif event_type == "intel_notification_sent":
            line += f" → {data['role']} via {data['channel']} [{data['status']}]"
        elif event_type == "intel_supervisor_completed":
            line += f" status={data['status']} shortlisted={data['shortlisted']} notifs={data['notifications']}"
        print(line)

    sup = IntelligenceSupervisor(event_callback=trace, crew_provider=crew_provider)
    result = await sup.find_replacements(ctx, top_n=3)

    # ─────────────────────────────── OUTPUT ───────────────────────────────────────
    print("\n[3] OUTPUT\n" + SUB)
    print(f"Status : {result.status}")
    print(f"Pool   : {result.pool_size} assessed · {result.disqualified} disqualified · "
          f"{len(result.candidates)} shortlisted")
    print(f"Timing : first event {result.timing['first_event_ms']}ms · total {result.timing['total_ms']}ms "
          f"(SLO: <2000ms / <10000ms)\n")

    if result.status == "no_crew_found":
        print("⚠  NO ELIGIBLE CREW")
        print(f"   {result.message}")
    elif result.candidates:
        print("Top-3 ranked replacement candidates (with rationale):")
    for c in result.candidates:
        dims = " ".join(f"{k}={v}" for k, v in c.dimension_scores.items())
        print(f"\n  #{c.rank_position}  {c.name}  ({c.crew_id})   score {c.score}")
        print(f"       rank={c.rank}  grade={c.grade}  port={c.port}")
        print(f"       dimensions: {dims}")
        print(f"       rationale : {' · '.join(c.rationale)}")

    # Disqualified, with the gate reason (the 'why not')
    dq = []
    crew_key = next(r for r in result.reports if r.investigator == "Crew Intel")
    vessel_key = next(r for r in result.reports if r.investigator == "Vessel Ops Intel")
    shortlisted_ids = {c.crew_id for c in result.candidates}
    for c in POOL:
        cid = c["crew_id"]
        if cid in shortlisted_ids:
            continue
        a_crew = crew_key.assessments.get(cid)
        a_vessel = vessel_key.assessments.get(cid)
        reason = None
        if a_crew and not a_crew.eligible:
            reason = a_crew.reasons[0]
        elif a_vessel and not a_vessel.eligible:
            reason = a_vessel.reasons[0]
        if reason:
            dq.append((cid, c["name"], reason))
    if dq:
        print(f"\nDisqualified ({len(dq)}) — gate reasons:")
        for cid, name, reason in dq:
            print(f"  ✗ {cid}  {name:<20} {reason}")

    print(f"\nOperator notifications ({len(result.notifications)}):")
    for n in result.notifications:
        print(f"  → {n.role:<16} via {n.channel:<6} [{n.status}]  «{n.subject}»")

    print("\n" + SUB)
    print("Raw API response shape (POST /api/v1/intelligence/match → IntelResult):")
    compact = {
        "status": result.status,
        "candidates": [
            {"rank_position": c.rank_position, "crew_id": c.crew_id, "name": c.name,
             "score": c.score, "dimension_scores": c.dimension_scores, "rationale": c.rationale}
            for c in result.candidates
        ],
        "notifications": [{"role": n.role, "channel": n.channel, "status": n.status}
                          for n in result.notifications],
        "pool_size": result.pool_size, "disqualified": result.disqualified, "timing": result.timing,
    }
    print(json.dumps(compact, indent=2))
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

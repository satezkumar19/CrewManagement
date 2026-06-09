"""
L3 Intelligence-Graph verification harness — the executable side of docs/L3_TEST_PLAN.md.

Centres on the exit criterion **"5 sign-off scenarios pass"**: five distinct sign-off
vacancies, each with an EXPECTED ranked output asserted exactly. Plus the cross-cutting
checks (delegates to 3 investigators · top-3 + rationale · latency SLOs · correct
notification channel · "no crew found" handled gracefully) and an accuracy score.

Deterministic + DB-free (injects a fixed roster via crew_provider).

Run:
    docker compose run --rm --no-deps backend python -m scripts.verify_l3_intelligence
    # or:  python -m scripts.verify_l3_intelligence
Exit code is non-zero if any check fails.
"""
import asyncio
import sys

import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(50))

from agents.intelligence import IntelligenceSupervisor
from agents.intelligence.schemas import SignOffContext
from agents.intelligence.notifications import channel_for

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILURES.append(name)


# ── Fixed roster — designed so each of the 5 scenarios has a clear expected winner ──
ROSTER = [
    # Exact Chief Officer, Grade A, at Singapore, all certs, senior.
    {"crew_id": "A1", "name": "Juan dela Cruz", "rank": "Chief Officer", "grade": "Grade A",
     "nationality": "Filipino", "port": "Singapore", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 12,
     "certifications": ["STCW Basic Safety", "GMDSS", "Advanced Fire Fighting"]},
    # Exact Chief Officer, Grade A, at Rotterdam (wins when the join port is Rotterdam).
    {"crew_id": "D1", "name": "Dmitri Sokolov", "rank": "Chief Officer", "grade": "Grade A",
     "nationality": "Russian", "port": "Rotterdam", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 10, "certifications": ["STCW Basic Safety", "GMDSS"]},
    # Second Officer (adjacent to CO) at Mumbai — eligible cover, ranks below the COs.
    {"crew_id": "S2", "name": "Rajesh Kumar", "rank": "Second Officer", "grade": "Grade B",
     "nationality": "Indian", "port": "Mumbai", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 8, "certifications": ["STCW Basic Safety", "GMDSS"]},
    # Chief Officer MISSING GMDSS → Vessel Ops hard gate (disqualified for CO/Master).
    {"crew_id": "NG", "name": "Liam O'Brien", "rank": "Chief Officer", "grade": "Grade A",
     "nationality": "Irish", "port": "Singapore", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 11, "certifications": ["STCW Basic Safety"]},
    # Chief Officer UNAVAILABLE → Crew Intel hard gate.
    {"crew_id": "UN", "name": "Petros Nikolaidis", "rank": "Chief Officer", "grade": "Grade A",
     "nationality": "Greek", "port": "Piraeus", "status": "Onboard", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 13, "certifications": ["STCW Basic Safety", "GMDSS"]},
    # Chief Engineer — wrong family for a deck vacancy; adjacent for a Second Engineer vacancy.
    {"crew_id": "CE", "name": "Alexei Petrov", "rank": "Chief Engineer", "grade": "Grade A",
     "nationality": "Ukrainian", "port": "Rotterdam", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 15, "certifications": ["STCW Basic Safety", "High Voltage"]},
    # Exact Second Engineer, Grade A, at Dubai.
    {"crew_id": "B1", "name": "Wang Wei", "rank": "Second Engineer", "grade": "Grade A",
     "nationality": "Chinese", "port": "Dubai", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 9, "certifications": ["STCW Basic Safety", "High Voltage"]},
    # Third Officer — 2 steps below CO (gated for CO); third-engineer below is missing HV.
    {"crew_id": "T3", "name": "Ahmed Khan", "rank": "Third Officer", "grade": "Grade B",
     "nationality": "Pakistani", "port": "Karachi", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 3, "certifications": ["STCW Basic Safety", "GMDSS"]},
    # Third Engineer (adjacent to Second Engineer) but MISSING High Voltage → vessel gate there.
    {"crew_id": "E3", "name": "Marco Rossi", "rank": "Third Engineer", "grade": "Grade B",
     "nationality": "Italian", "port": "Genoa", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 5, "certifications": ["STCW Basic Safety"]},
]


# A pool of EXACT-rank, available Chief Engineers who all lack the vessel-mandated
# "High Voltage" cert → they clear Crew Intel (rank+availability) but every one is
# gated by Vessel Ops. Used by S6 to produce a no-crew via a cert cause (not rank).
CERT_GATED_ROSTER = [
    {"crew_id": "X1", "name": "Hans Mueller", "rank": "Chief Engineer", "grade": "Grade A",
     "nationality": "German", "port": "Singapore", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 14, "certifications": ["STCW Basic Safety"]},
    {"crew_id": "X2", "name": "Olav Berg", "rank": "Chief Engineer", "grade": "Grade B",
     "nationality": "Norwegian", "port": "Singapore", "status": "Available", "stcw_status": "Valid",
     "visa_status": "Valid", "experience_years": 10, "certifications": ["STCW Basic Safety", "Advanced Fire Fighting"]},
]


def provider_for(roster):
    async def _p():
        return [dict(c) for c in roster]
    return _p


async def run(ctx, roster=ROSTER, top_n=3, events=None):
    cb = None
    if events is not None:
        async def cb(event_type, agent_name, data):
            events.append(event_type)
    sup = IntelligenceSupervisor(event_callback=cb, crew_provider=provider_for(roster))
    return await sup.find_replacements(ctx, top_n=top_n)


# ── The 5 sign-off scenarios, each with an EXPECTED ranked output ────────────────
# expect: {"status", "order": [crew_id, ...]}  (order is the full shortlist, in rank order)
SCENARIOS = [
    {
        "name": "S1 · Exact deck match at join port",
        "ctx": SignOffContext(vacated_rank="Chief Officer", vacated_grade="Grade A", vessel="MV Pacific Star", port="Singapore"),
        "expect": {"status": "matched", "order": ["A1", "D1", "S2"]},
        "note": "Exact CO at Singapore beats the relocating CO and the adjacent Second Officer.",
    },
    {
        "name": "S2 · Engine-room exact match",
        "ctx": SignOffContext(vacated_rank="Second Engineer", vacated_grade="Grade A", vessel="MT Crude Titan", port="Dubai"),
        "expect": {"status": "matched", "order": ["B1", "CE"]},
        "note": "Second Engineer at Dubai wins; Third Engineer is gated (missing High Voltage).",
    },
    {
        "name": "S3 · Adjacent-rank cover (no exact available)",
        "ctx": SignOffContext(vacated_rank="Master", vacated_grade="Grade A", vessel="MV Pacific Star", port="Singapore"),
        "expect": {"status": "matched", "order": ["A1", "D1"]},
        "note": "No Master in pool → Chief Officers cover (adjacent); at-port A1 leads.",
    },
    {
        "name": "S4 · Port schedule flips the winner",
        "ctx": SignOffContext(vacated_rank="Chief Officer", vacated_grade="Grade A", vessel="MV Atlantic", port="Rotterdam"),
        "expect": {"status": "matched", "order": ["D1", "A1", "S2"]},
        "note": "Same pool as S1 but join port Rotterdam → D1 (at port) overtakes A1 (relocating).",
    },
    {
        "name": "S5 · No crew found — rank-family cause",
        "ctx": SignOffContext(vacated_rank="Bosun", vacated_grade="Grade B", vessel="MV Pacific Star", port="Singapore"),
        "expect": {"status": "no_crew_found", "order": []},
        "note": "Bosun is a rating; the all-officer pool yields no eligible family (Crew Intel gate).",
    },
    {
        "name": "S6 · No crew found — cert-gate cause",
        "ctx": SignOffContext(vacated_rank="Chief Engineer", vacated_grade="Grade A", vessel="MT Crude Titan", port="Singapore"),
        "roster": CERT_GATED_ROSTER,
        "expect": {"status": "no_crew_found", "order": []},
        "note": "Exact-rank, available Chief Engineers, but all miss High Voltage → every one Vessel-Ops gated.",
    },
]


async def main() -> int:
    # ══ THE SIGN-OFF SCENARIOS ══════════════════════════════════════════════════
    print(f"=== {len(SCENARIOS)} sign-off scenarios (expected ranked output) ===")
    passed = 0
    for sc in SCENARIOS:
        r = await run(sc["ctx"], roster=sc.get("roster", ROSTER), top_n=3)
        got_order = [c.crew_id for c in r.candidates]
        ok = r.status == sc["expect"]["status"] and got_order == sc["expect"]["order"]
        top = f"top1={got_order[0]}" if got_order else "no candidates"
        check(f"{sc['name']}", ok, f"status={r.status} order={got_order} ({top})")
        if ok:
            passed += 1
    check(f"⇒ {len(SCENARIOS)} sign-off scenarios pass (≥5 required)",
          passed == len(SCENARIOS) and passed >= 5, f"{passed}/{len(SCENARIOS)} passed")

    # Distinguish the TWO no-crew causes: S5 fails at Crew Intel (rank family); S6
    # passes Crew Intel (exact rank, available) but every candidate is cert-gated by
    # Vessel Ops — a genuinely different dead-end.
    r5 = await run(SCENARIOS[4]["ctx"])                                    # rank-family cause
    r6 = await run(SCENARIOS[5]["ctx"], roster=CERT_GATED_ROSTER)          # cert-gate cause
    crew5 = next(rep for rep in r5.reports if rep.investigator == "Crew Intel")
    crew6 = next(rep for rep in r6.reports if rep.investigator == "Vessel Ops Intel")
    crew6_rank = next(rep for rep in r6.reports if rep.investigator == "Crew Intel")
    check("S5 cause = rank family (all fail Crew Intel)",
          r5.status == "no_crew_found" and all(not a.eligible for a in crew5.assessments.values()),
          "Crew Intel gated all")
    check("S6 cause = cert gate (rank-OK at Crew Intel, all fail Vessel Ops)",
          r6.status == "no_crew_found"
          and all(a.eligible for a in crew6_rank.assessments.values())     # passed rank/availability
          and all(not a.eligible for a in crew6.assessments.values()),     # but cert-gated
          "Vessel Ops gated all rank-eligible candidates")
    print()

    # ══ CROSS-CUTTING EXIT CRITERIA (run on S1) ═════════════════════════════════
    print("=== cross-cutting exit criteria ===")
    events = []
    r1 = await run(SCENARIOS[0]["ctx"], top_n=3, events=events)

    check("delegates to all 3 investigators",
          {rep.investigator for rep in r1.reports} ==
          {"Crew Intel", "Contract/Wage Intel", "Vessel Ops Intel"},
          ", ".join(sorted(rep.investigator for rep in r1.reports)))
    check("top-3 ranked candidates returned", len(r1.candidates) == 3, f"{len(r1.candidates)} shortlisted")
    check("every candidate carries rationale", all(c.rationale for c in r1.candidates),
          str(r1.candidates[0].rationale[:2]))
    check("ranked by score descending",
          all(r1.candidates[i].score >= r1.candidates[i + 1].score for i in range(len(r1.candidates) - 1)),
          ", ".join(f"{c.crew_id}={c.score}" for c in r1.candidates))
    check("streamed investigator events",
          events.count("intel_investigator_started") == 3 and "intel_ranking" in events,
          f"{events.count('intel_investigator_started')} started")

    # Hard gates (the 4 disqualified in S1)
    ids = set(c.crew_id for c in r1.candidates)
    check("hard gates: missing-cert / unavailable / wrong-family / 2-steps excluded",
          all(x not in ids for x in ("NG", "UN", "CE", "T3")),
          f"shortlist={sorted(ids)}")

    # Latency SLOs
    check("first event < 2000ms", r1.timing["first_event_ms"] < 2000, f"{r1.timing['first_event_ms']}ms")
    check("full response < 10000ms", r1.timing["total_ms"] < 10000, f"{r1.timing['total_ms']}ms")

    # Notification channels
    by_role = {n.role: n for n in r1.notifications}
    check("Crewing Manager notified via email",
          by_role.get("Crewing Manager") and by_role["Crewing Manager"].channel == channel_for("Crewing Manager") == "email",
          str(by_role.get("Crewing Manager").channel if by_role.get("Crewing Manager") else None))
    check("proposed crew notified via sms",
          by_role.get("Crew") and by_role["Crew"].channel == "sms",
          str(by_role.get("Crew").channel if by_role.get("Crew") else None))
    check("all notifications delivered",
          r1.notifications and all(n.status == "delivered" for n in r1.notifications),
          f"{sum(n.status == 'delivered' for n in r1.notifications)}/{len(r1.notifications)}")

    # No-crew escalation routing (S5)
    r5 = await run(SCENARIOS[4]["ctx"])
    check("no-crew escalates to Crewing Manager + Ops Center",
          {n.role for n in r5.notifications} >= {"Crewing Manager", "Ops Center"},
          ", ".join(n.role for n in r5.notifications))

    # ══ L2 INTEGRATION (Vessel Ops reads the L2 compliance graph) ════════════════
    print("=== L2 compliance-graph integration ===")
    base_co = {"rank": "Chief Officer", "grade": "Grade A", "nationality": "Iranian",
               "port": "Singapore", "status": "Available", "stcw_status": "Valid",
               "experience_years": 12, "certifications": ["STCW Basic Safety", "GMDSS"]}
    ir_blocked = {**base_co, "crew_id": "IR1", "name": "Reza Ahmadi", "visa_status": "Expired"}
    ir_cleared = {**base_co, "crew_id": "IR2", "name": "Kaveh Rad", "visa_status": "Valid"}
    rb = await run(SignOffContext(vacated_rank="Chief Officer", port="Singapore"), roster=[ir_blocked, *ROSTER])
    rc = await run(SignOffContext(vacated_rank="Chief Officer", port="Singapore"), roster=[ir_cleared, *ROSTER])
    vrep = next(rep for rep in rb.reports if rep.investigator == "Vessel Ops Intel")

    check("Vessel Ops sourced port restrictions from L2",
          vrep.applied.get("l2_backend") in ("fallback", "age")
          and "Iranian" in (vrep.applied.get("l2_port_restricted_nationalities") or []),
          f"backend={vrep.applied.get('l2_backend')} restricted={vrep.applied.get('l2_port_restricted_nationalities')}")
    check("L2 graph gate: Iranian w/o valid visa restricted at Singapore",
          "IR1" not in {c.crew_id for c in rb.candidates} and not vrep.assessments["IR1"].eligible,
          vrep.assessments["IR1"].reasons[0])
    check("gate reason cites the L2 graph (Port-RESTRICTS->Country)",
          "Port-RESTRICTS" in vrep.assessments["IR1"].reasons[0] or "L2 graph" in vrep.assessments["IR1"].reasons[0])
    check("L2 graph: Iranian WITH valid visa is eligible (not gated)",
          "IR2" in {c.crew_id for c in rc.candidates}, f"shortlist={[c.crew_id for c in rc.candidates]}")
    crep = next(rep for rep in rb.reports if rep.investigator == "Crew Intel")
    sample = next(iter(crep.assessments.values()))
    check("Crew Intel surfaces L2 required safety certs",
          "l2_required_safety_certs" in sample.signals,
          str(sample.signals.get("l2_required_safety_certs")))
    print()

    # ══ ACCURACY (top-1 over the 4 matched scenarios) ═══════════════════════════
    matched = [s for s in SCENARIOS if s["expect"]["status"] == "matched"]
    correct = 0
    for s in matched:
        r = await run(s["ctx"])
        if r.candidates and r.candidates[0].crew_id == s["expect"]["order"][0]:
            correct += 1
    acc = correct / len(matched) * 100
    check("accuracy: top-1 correct on matched scenarios == 100%", acc == 100.0, f"{acc:.0f}% ({correct}/{len(matched)})")

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILED → {FAILURES}", flush=True)
        return 1
    print("RESULT: ALL CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

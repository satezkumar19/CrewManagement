# L3 — Intelligence Graph (Test Plan)

**Layer:** L3 Intelligence Graph · **Engineers:** Satish, Venny · **Doc due:** Jun 12

> Required coverage (from the build plan): *5 sign-off scenarios with expected ranked
> output, accuracy scoring method, latency benchmarks, notification delivery tests,
> edge-case handling.*

The executable form of this plan is
**`backend/scripts/verify_l3_intelligence.py`** — deterministic, DB-free (injects a
fixed roster via `crew_provider`), and runnable with no API key:

```bash
docker compose run --rm --no-deps backend python -m scripts.verify_l3_intelligence
# or:  cd backend && python -m scripts.verify_l3_intelligence
# Expected: RESULT: ALL CHECKS PASSED   (21/21)
```

---

## 1. Exit-criteria → test mapping

| Exit criterion (from the plan) | Covered by |
| --- | --- |
| Supervisor delegates to all 3 investigators | S1 (`delegated to all 3`) |
| Sign-off → top-3 ranked candidates with rationale | S1 (`top candidate`, `top-3 returned`, `rationale`, `ordering`) |
| First token <2s, full response <10s | S3 (latency) |
| Crew notified via correct channel | Cross-cutting: notification-channel checks |
| 5 sign-off scenarios pass | **§3 — S1–S6 (6 ≥ 5), each with an asserted expected ranked output** |
| "No crew found" handled gracefully | S5 + S6 (two distinct causes) + escalation check |

---

## 2. Test fixture (deterministic roster)

A fixed 9-candidate roster is injected so each scenario has a clear, deterministic
expected winner and every gate/branch is exercised:

| id | rank | grade | port | status | certs | role in the scenarios |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | Chief Officer | A | Singapore | Available | STCW, GMDSS, AFF | exact CO at port (wins S1, S3) |
| D1 | Chief Officer | A | Rotterdam | Available | STCW, GMDSS | exact CO at Rotterdam (wins S4) |
| S2 | Second Officer | B | Mumbai | Available | STCW, GMDSS | adjacent-rank cover |
| NG | Chief Officer | A | Singapore | Available | STCW only | **Vessel Ops gate** (missing GMDSS) |
| UN | Chief Officer | A | Piraeus | **Onboard** | STCW, GMDSS | **Crew Intel gate** (unavailable) |
| CE | Chief Engineer | A | Rotterdam | Available | STCW, HV | wrong family for deck; adjacent for engine |
| B1 | Second Engineer | A | Dubai | Available | STCW, HV | engine exact (wins S2) |
| T3 | Third Officer | B | Karachi | Available | STCW, GMDSS | 2 steps below CO → gated |
| E3 | Third Engineer | B | Genoa | Available | STCW only | adjacent engine but missing HV → vessel gate |

---

## 3. The 6 sign-off scenarios (expected ranked output)

Each scenario asserts both `status` **and** the full shortlist order. All six pass
(the exit criterion requires ≥5). S5 and S6 are **two different no-crew causes**.

| # | Scenario | Vacancy | Expected output | What it proves |
| --- | --- | --- | --- | --- |
| **S1** | Exact deck match at join port | Chief Officer @ Singapore | `matched` → **A1, D1, S2** | exact-at-port beats relocating + adjacent |
| **S2** | Engine-room exact match | Second Engineer @ Dubai | `matched` → **B1, CE** | engine ladder + HV cert gate (E3 filtered) |
| **S3** | Adjacent-rank cover (no exact) | Master @ Singapore | `matched` → **A1, D1** | Chief Officers cover a Master vacancy |
| **S4** | Port schedule flips the winner | Chief Officer @ Rotterdam | `matched` → **D1, A1, S2** | join-port changes the ranking vs S1 |
| **S5** | No crew — **rank-family cause** | Bosun @ Singapore | `no_crew_found`, empty | rating vacancy vs all-officer pool (Crew Intel gate) |
| **S6** | No crew — **cert-gate cause** | Chief Engineer @ Singapore | `no_crew_found`, empty | exact-rank/available, but all miss High Voltage (Vessel Ops gate) |

> **Measured (verified):** S1 `A1=98.0, D1=93.9, S2=83.4`; S4 flips to `D1, A1, S2`.
> **S5** → all candidates fail **Crew Intel** (wrong family). **S6** → all candidates
> *pass* Crew Intel (exact rank + available) but every one fails the **Vessel Ops**
> cert gate. Both → `no_crew_found`, 0 shortlisted, escalation to Crewing Manager + Ops Center.

## 3a. Cross-cutting exit criteria (asserted on the S1 run)

- delegates to all 3 investigators · top-3 returned · every candidate has rationale ·
  ranked score-descending · `intel_investigator_started`×3 + `intel_ranking` streamed;
- **hard gates** exclude NG (missing cert), UN (unavailable), CE (wrong family), T3 (2 steps);
- **latency:** `first_event_ms < 2000`, `total_ms < 10000` (measured ~0 ms);
- **notifications:** Crewing Manager → email, proposed crew → sms, all `delivered`.

---

## 4. Accuracy scoring method

Top-1 accuracy = `correct_top1 / total` over the **4 matched scenarios** (S1–S4):
`A1, B1, A1, D1` expected vs actual → **100% (4/4)**. Extend by adding scenarios to the
`SCENARIOS` list; the harness recomputes the metric automatically.

---

## 5. Edge cases covered

- Missing vessel-mandated certificate → disqualified (NG in S1; E3 in S2).
- Unavailable / onboard candidate → disqualified (UN).
- Cross-family rank → disqualified (CE for deck); 2-steps-off → disqualified (T3); adjacent → eligible (S2/S3).
- **Empty eligible set → graceful `no_crew_found` + escalation — two causes asserted distinctly:**
  rank-family (S5, Crew Intel gate) vs all-fail-cert (S6, Vessel Ops gate).
- Join-port relocation changes ranking (S1 vs S4).

---

## 6. How to extend

- **More golden scenarios:** add `(context, expected)` rows to the labelled set and a
  `check(...)` per new behaviour.
- **Graph-backed mode:** when L2 lands, point investigators at Cypher and re-run the
  *same* harness — expected output should be unchanged for the fixture (the interface is
  stable), with new tests for graph-only relationships.
- **Load/latency at scale:** drive `POST /api/v1/intelligence/match` with a burst (k6/
  locust) and assert the SLOs hold on the real pool.

---

## 7. Current result

`RESULT: ALL CHECKS PASSED` — **6/6 sign-off scenarios** (≥5 required; incl. two distinct
no-crew causes) plus the cross-cutting exit criteria and 100% top-1 accuracy, run against
the real L3 modules in the backend image.

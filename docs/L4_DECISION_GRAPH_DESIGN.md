# L4 Decision Graph — Design & Implementation Doc

**Layer:** L4 — Decision Graph (decision memory & self-improvement)
**Stack:** PostgreSQL 16 (+ pgvector for similarity, optional) · FastAPI · Next.js
**Status:** All five planned capabilities **implemented & verified**, plus a rejection-retry resilience enhancement.

---

## 1. Purpose & scope

L3 (Master Agent + specialists) *makes* each crew-placement decision but leaves its working
scattered across the in-memory `WorkflowState` — when the workflow ends, the reasoning is gone.
**L4 makes those decisions durable, queryable, and self-improving.** It is a **read-only consumer
of L3**: it never re-decides anything; it captures what L3 produced, persists it, learns from the
history, and feeds that learning back into the next decision.

| # | Capability | Question it answers | Status |
|---|------------|--------------------|--------|
| 1 | **Decision Traces** | *What did we decide, and why?* | **Built** |
| 2 | **Precedent Index** | *Have we filled this kind of role before, and how did it go?* | **Built** |
| 3 | **Feedback into L3** | *Can past outcomes change who we recommend now?* | **Built** |
| 4 | **Pattern Detection** | *What keeps going wrong across all placements?* | **Built** |
| 5 | **Structural Embeddings** | *Who is structurally most similar to this crew?* | **Built** |
| + | **Rejection-retry loop** | *If the pick fails compliance, can we auto-recover?* | **Built** |

**Design tenets (enforced everywhere):**

- **Read-only over L3 state.** Capture assembles from `WorkflowState`; it never mutates the decision.
- **Best-effort, never breaks a turn.** Every L4 call is wrapped — a failure is logged and swallowed
  (mirrors the skill-sweep convention in `agents/managed/client.py`). A broken L4 never blocks a sign-off.
- **Fallback-first backends.** Heavy infra (Apache AGE, pgvector) is always optional behind a
  `*_BACKEND` toggle with an identical-shape Python fallback — the demo runs with zero extra infra.
- **Explainable.** Every score adjustment is surfaced as a human reason and a number; nothing is hidden.

---

## 2. Architecture & data flow

L4 hangs off the existing sign-off orchestration (`services/workflow_service.py`). One sign-off
produces exactly **one** decision trace, stamped with its outcome once compliance resolves.

```
User triggers sign-off (Dashboard)
        │
WorkflowService._run_sign_off_orchestration
  1. precedent_service.consult(rank,grade,port,nat)        → stash on workflow.memory  [Phase 2]
  2. MasterAgent.orchestrate_sign_off
        ├─ derive_guidance(precedent)  → set on matching agent                          [Phase 3]
        └─ inject departing + precedent embeddings → matching agent                     [Phase 5]
        → L3 ranks candidates (base + precedent_boost + similarity_boost)
  3. decision_trace_service.capture(state)                 → INSERT decision_traces     [Phase 1]
  4. _auto_compliance_and_signon  (retry loop, ≤3 candidates)                           [retry]
        ├─ record_outcome(signed_on|rejected, attempts, chosen override)
        └─ precedent_service.record_placement(decision)    → INSERT placement_precedents[Phase 2]
        │
Postgres: decision_traces + placement_precedents + crew.embedding
        │
   GET /decisions, /precedents, /patterns, /embeddings  ──►  /decisions page
   WS decision_logged / decision_outcome / precedent_consulted ──► live update
```

Pattern Detection [Phase 4] is a pure read-side aggregator over `decision_traces` — it adds no write path.

---

## 3. Data model

Two L4 tables plus one column on `crew`. All created by `database/db.py:init_db()` via
`Base.metadata.create_all` + idempotent `ADD COLUMN IF NOT EXISTS` migrations (no manual DDL, no recreate).

### 3.1 `decision_traces` (`database/decision_orm.py`)

| Column | Type | Meaning |
|--------|------|---------|
| `decision_id` (PK) | str (uuid) | the trace id |
| `workflow_id` | str, idx | L3 run that produced it (outcome is keyed on this) |
| `created_at` / `resolved_at` | datetime | captured at / outcome stamped at |
| `trigger`, `query_context` | str / JSON | departing crew profile + reason |
| `chosen_crew_id`, `chosen_crew` | str / JSON | the selected candidate |
| `confidence_score` | float | final match confidence (see §5) |
| `match_reasons`, `alternatives` | JSON | the "why" + ranked candidates not chosen |
| `trajectory` | JSON | flattened agent → tool → input → output steps |
| `is_repeat_query`, `consulted_precedents` | bool / JSON | Precedent Index lookup result [Phase 2] |
| `precedent_feedback` | JSON | re-rank measurement (lift, reranked, boosted) [Phase 3] |
| `attempts`, `pending_reason` | JSON / str | rejection-retry journey + why-pending [retry] |
| `outcome_status` | str | `pending` \| `signed_on` \| `rejected` |
| `compliance_status`, `compliance_score`, `outcome_reasons` | str/float/JSON | compliance verdict |
| `session_id`, `total_tokens`, `total_cost`, cache cols | — | cost of reaching the decision |

### 3.2 `placement_precedents` (`database/placement_precedent_orm.py`)

A flat row per **completed** placement — the lookup key is the vacancy profile (the *departing* crew's
attributes); the rest is the result (who was chosen + how it turned out).

```
LOOKUP KEY:  rank(idx), grade, port(idx), nationality
RESULT:      chosen_crew_id, chosen_crew_name, chosen_crew_rank,
             chosen_crew_nationality, chosen_crew_grade,        ← added for Phase 3 re-rank
             confidence_score, outcome_status, compliance_status, compliance_score
```

### 3.3 `crew.embedding` (`database/crew_orm.py`)

`JSON` float list (a structural vector, `EMBED_DIM = 58`). Stored as JSON so it works on any Postgres;
pgvector reads it via an inline `::vector` cast. Backfilled on startup and by `scripts.seed_crew`.

---

## 4. Capability designs

### 4.1 Decision Traces (Phase 1) — `services/decision_trace_service.py`

- `capture(workflow)` — after matching, `_assemble()` gathers query context, chosen crew, ranked
  **alternatives** (`ranked - chosen`), the flattened **trajectory**, and cost; `insert_decision()`
  persists it (`outcome_status="pending"`), then broadcasts `decision_logged`.
- `record_outcome(workflow_id, …)` — at the compliance gate, stamps `signed_on`/`rejected` +
  compliance score onto the same row (keyed by `workflow_id`, most-recent match) and broadcasts
  `decision_outcome`.
- `seed_demo()` — inserts 6 realistic fixtures *in order* so the Precedent Index builds up; used by the
  `/decisions/demo-seed` endpoint.

### 4.2 Precedent Index (Phase 2) — `services/precedent_service.py`

- `consult(rank, grade, port, nationality)` — runs at the **start** of every sign-off; `find_precedents`
  returns prior placements for the profile; `is_repeat=True` when ≥1 exists. Stashed on
  `workflow.memory.short_term.precedent`.
- `record_placement(decision)` — appends a row only for `signed_on`/`rejected` decisions (a completed
  placement), copying the chosen crew's nationality/grade so Phase 3 can key on them.

### 4.3 Feedback into L3 (Phase 3) — `precedent_service.derive_guidance` + `crew_matching_agent`

The consult result is **fed back into ranking**, not just displayed.

- `derive_guidance(precedent)` → `{prefer_nationalities, avoid_nationalities, prefer_grades, rationale}`,
  with weights in `[0,1]` scaled by the prior placement's compliance score.
- `MasterAgent.orchestrate_sign_off` injects it via `cm_agent.set_precedent_guidance(...)` and adds a
  "PRECEDENT (repeat vacancy)" block to the Phase-1 prompt.
- `_rank_crew._precedent_boost(crew)` → `+` for a nationality/grade matching a prior **signed-on**
  placement, `−` for a prior **rejected** one. Caps: `_PREFER_NAT_MAX=10`, `_PREFER_GRADE_MAX=4`,
  `_AVOID_NAT_MAX=12`.
- **Measurement:** `_build_precedent_feedback()` records `{applied, top_base_score, top_adjusted_score,
  lift, reranked, base_winner, adjusted_winner, boosted, rationale}` onto the trace. `lift == boost`
  exactly because base and adjusted share the same jitter.

### 4.4 Rejection-retry loop (enhancement) — `workflow_service._auto_compliance_and_signon`

Single-shot compliance is replaced by a loop over the ranked candidates (top match first, then the
precedent-boosted alternatives), `MAX_SIGNON_ATTEMPTS = 3`.

- First `passed`/`warning` → sign on, **break**; `chosen_crew` is overridden to that candidate (may be a
  fallback) so the Precedent Index records who actually went onboard.
- All fail → one final `rejected` with `"All N candidates failed compliance"`.
- Each attempt is recorded: `{order, crew_id, name, compliance_status, compliance_score, failures}`.
- `pending_reason` is set at capture and cleared at outcome — surfaced as the "why pending" banner.
- Events: `auto_compliance` (per attempt, with `is_retry`), `sign_on_attempt_rejected` (intermediate),
  `crew_signed_on` / `sign_on_rejected` (final).

### 4.5 Pattern Detection (Phase 4) — `services/pattern_service.py`

Read-only aggregator over `decision_traces` (`list_decisions`). For each decision it collects failure
strings from `attempts[].failures` + (when rejected) `outcome_reasons`, categorizes each via ordered
keyword rules → `visa | stcw | medical | passport | training | certification | other`, and counts the
**distinct decisions** each category blocks.

- **Recurring gap** = the category with the most distinct decisions affected where that count
  `>= RECURRING_THRESHOLD (2)`; attaches a per-category `recommendation`. `None` if nothing recurs.
- Distinct-decisions counting (not raw lines) is what makes it *recurring*: two visa lines in one
  rejection is not systemic; visa blocking two different placements is.

### 4.6 Structural Embeddings (Phase 5) — `services/embedding_service.py` + `database/embedding_repository.py`

- `embed_crew(crew)` — deterministic, L2-normalized vector over fixed vocabularies: one-hot
  rank/grade/nationality/port (+ "other" slot each), multi-hot certifications, normalized experience,
  stcw/visa validity bits. `EMBED_DIM = 58`. `vessel_centroid()` = mean of a vessel's crew.
- `find_similar_crew(vec, pool, limit)` — **dispatcher** on `VECTOR_BACKEND`:
  - `pgvector`: `ORDER BY (embedding::text)::vector <=> (:q)::vector` (JSON casts to a vector literal — no
    dedicated column, no python pgvector dep), `similarity = 1 - distance`.
  - `fallback`: load embeddings, `cosine()` in Python. Identical return shape.
- **Ranking use (both signals):** `_rank_crew._similarity_boost(crew)` blends
  `cosine(candidate, departing)` (0.6) and `max cosine(candidate, prior signed-on crew)` (0.4), capped at
  `_SIM_MAX = 8`. Injected by `MasterAgent._inject_similarity_context`. Surfaced as reasons + per-candidate
  `similarity_departing` / `similarity_precedent`.

---

## 5. The scoring model (how a candidate's confidence is composed)

`_rank_crew` produces a transparent, additive score so every contribution is auditable:

```
base_score        = rank(40) + grade(20) + port(15) + docs(15) + experience(10) + jitter(±2)   # clamp 0..100
precedent_boost   = ±(prefer/avoid nationality & grade)      [Phase 3]   cap +14 / −12
similarity_boost  = SIM_MAX · (0.6·sim_departing + 0.4·sim_precedent)   [Phase 5]   cap +8
confidence_score  = clamp(base_score + precedent_boost + similarity_boost, 0, 100)
```

With **no precedent and no embeddings injected, both boosts are 0** → the score equals the legacy
base_score (no behavior change). A consequence visible in the UI: after a retry the chosen candidate's
confidence can be *lower* than a rejected attempt's — because match confidence and the **compliance
score** (a separate gate) are distinct axes.

---

## 6. API interface

All under `/api/v1` (`main.py`).

| Method & path | Purpose |
|---------------|---------|
| `GET /decisions/` · `GET /decisions/{id}` | list / full decision trace |
| `POST /decisions/demo-seed` | seed mock decisions for the demo |
| `GET /precedents/` · `GET /precedents/lookup` | history list / vacancy-profile lookup |
| `GET /patterns/` | aggregate report + flagged recurring gap |
| `GET /embeddings/similar/{crew_id}` | structurally nearest crew (pgvector/fallback) |
| `POST /embeddings/backfill` | (re)compute crew embeddings |

WebSocket events: `decision_logged`, `decision_outcome`, `precedent_consulted`,
`precedent_feedback_applied`, `auto_compliance`, `sign_on_attempt_rejected`, `crew_signed_on`,
`sign_on_rejected`.

---

## 7. Frontend surfaces (all on `/decisions`)

| Component | Shows |
|-----------|-------|
| `DecisionGraph.tsx` | step-revealed flow — default `Query → Decision·L3 → Chosen → Outcome`; **retry** shape becomes an L3-centred loop (`rejected → feedback to L3 → next candidate`) |
| `PrecedentPanel.tsx` | Precedent Index (repeat/first) + **"Precedent feedback → L3"** strip (lift, re-ranked, boosted) |
| `PatternPanel.tsx` | aggregate counts + **recurring-gap** banner; builds up incrementally over *revealed* decisions |
| `SimilarCrewPanel.tsx` | **Structural Similarity Explorer** — nearest crew + similarity %, backend badge |
| `TrajectoryTrace` (page) | match reasons, **Compliance attempts** list, **why-pending** banner |

---

## 8. Backend toggles & how to run

```bash
# Backends (both default to the no-infra Python fallback):
GRAPH_BACKEND=fallback      # age      → Apache AGE compliance subgraph
VECTOR_BACKEND=fallback     # pgvector → pgvector <=> similarity (needs the pgvector image)

# Local run (fallback mode — no Docker rebuild needed):
cd backend
python -m scripts.seed_crew          # seeds crew + backfills 58-dim embeddings
uvicorn main:app --reload            # init_db creates L4 tables + runs migrations
# Frontend: cd frontend && npm run dev  → open /decisions → "Seed & play"

# pgvector path: docker-compose builds L2Knowledge_graph/deploy/postgres-age.Dockerfile
# (pgvector + AGE). Switching the image needs a one-time: docker-compose down -v
```

---

## 9. Verification / test scenarios

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Seed & play, select a signed-on decision | flow `Query → Decision·L3 → Chosen → Outcome`; trajectory + reasons |
| 2 | Repeat vacancy (Nikolai → Rohan, CO @ Singapore) | "Precedent feedback → L3": **+lift**, **re-ranked winner** |
| 3 | Success-after-retry (Liam → John Adams) | graph loop: Sergey **Rejected 38%** → feedback → John **Cleared 84%** → Signed On |
| 4 | Exhausted rejection (Maria Santos) | two failed attempts → **Rejected**, "alternatives exhausted" |
| 5 | Pending decision (Chen Wei) | **"Why pending"** banner |
| 6 | Pattern Detection over seed | recurring gap = **Visa / port-entry**, 2 placements (Rotterdam, Houston); STCW & medical at 1 |
| 7 | `embed_crew` determinism | fixed 58-dim, self-cosine ≈ 1.0; same-profile pair > dissimilar pair |
| 8 | Similarity ranking, no context | `similarity_boost = 0`, scores unchanged (regression guard) |
| 9 | `GET /embeddings/similar/{id}` | nearest crew ordered by similarity; backend badge matches `VECTOR_BACKEND` |
| 10 | First-time vacancy | `precedent_feedback.applied=false`, "no re-rank applied" |

---

## 10. Open items / future work

1. **pgvector index:** embeddings are read via an inline `::vector` cast (fine at demo scale). For large
   pools, add a dedicated `vector` column + `ivfflat` index.
2. **Semantic embeddings:** the current vector is *structural* (deterministic, no model). A text/semantic
   embedding could capture nuance the attribute one-hots miss — at the cost of an external provider.
3. **Pattern → action loop:** Pattern Detection currently *reports* the recurring gap; a future step could
   feed it back as an earlier matching pre-filter (e.g. visa pre-check before the compliance gate).
4. **Retry & multiple compliance runs:** the loop invokes `orchestrate_compliance` up to 3× per sign-off,
   which re-emits the terminal `workflow_completed` each time (benign; the UI keys off
   `crew_signed_on`/`sign_on_rejected`). A `finalize` flag could suppress the non-final emits.

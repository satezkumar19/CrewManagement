# Skill: Fallback Strategy

This skill defines what the Crew Matching agent does when no candidate
satisfies the primary filters.

## Trigger Conditions

Apply fallback when `searchCrew()` returns zero candidates that match the
required rank with all other filters applied, OR when every candidate has
a critical document concern (expired STCW, expired visa with no
alternative port).

## Fallback Order

Try the following relaxations in order, stopping as soon as candidates are
found:

1. **Drop nationality filter** — re-run `searchCrew()` without
   `nationality`.
2. **Drop port filter** — re-run without `port`. Note: Travel agent will
   need to assess feasibility.
3. **Drop grade filter** — re-run without `grade`. Cap any resulting top
   match at `confidence_score <= 75` and add `"GRADE_RELAXED"` to
   `match_reasons`.
4. **Allow adjacent ranks** — re-run with the canonical adjacent rank from
   `rank-equivalence.md`. Cap `confidence_score <= 70` and add
   `"ADJACENT_RANK"` to `match_reasons`.
5. **Allow expiring documents** — include candidates whose STCW or Medical
   expires within 60 days, on the assumption that Compliance can fast-track
   renewal. Cap `confidence_score <= 60` and add `"DOCS_EXPIRING"`.

Never relax more than one filter at a time per iteration. Always re-run
`rankCrew()` after each relaxation.

## Hard Stop

If all five relaxations fail, return a `top_match` of `null` and set:

- `summary`: "No viable candidates found after exhausting fallback
  relaxations."
- `confidence_score`: 0
- `ranked_candidates`: empty list
- Include a `recommendation` field with one of:
  - `"REQUEST_POOL_EXPANSION"` — the operator should add candidates to the
    sign-on pool.
  - `"DEFER_CREW_CHANGE"` — the coordinator should consider deferring the
    sign-off date.

Do NOT default to an arbitrary crew member when no real match exists. The
existing fallback in `_validate_and_format()` (picking the first crew_list
entry with score 75) is a legacy behaviour that should be removed in favour
of the explicit null return above.

## Audit Trail

Every relaxation step must be logged via the agent's `event_callback`. The
coordinator and compliance reviewers need a clear record of which filters
were dropped and why.

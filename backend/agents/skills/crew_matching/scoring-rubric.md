# Skill: Crew Match Scoring Rubric

This skill defines exactly how `rankCrew()` should weight candidate
attributes. The total possible score is 100. The candidate with the highest
score becomes `top_match`.

## Weights

| Dimension              | Weight | What it measures                                |
| ---------------------- | ------ | ----------------------------------------------- |
| Rank match             | 40%    | Candidate rank equals or is equivalent to the   |
|                        |        | sign-off rank.                                  |
| Grade match            | 20%    | Candidate grade (A/B/C/D) equals target grade.  |
| Port proximity         | 15%    | Candidate home/embarkation port equals preferred|
|                        |        | crew-change port.                               |
| Document validity      | 15%    | STCW, Medical, and Visa status all "Valid",     |
|                        |        | plus count of additional certifications.        |
| Experience             | 10%    | Years of sea-time, capped contribution.         |

## Scoring Rules

**Rank match (40 pts max)**

- Exact match on rank string (case-insensitive): 40 pts.
- Equivalent rank per `rank-equivalence.md`: 25 pts.
- Adjacent rank (one tier up or down): 15 pts.
- Otherwise: 10 pts, flag "Rank mismatch — similar".

**Grade match (20 pts max)**

- Exact grade match: 20 pts.
- Adjacent grade (A↔B, B↔C, C↔D): 12 pts.
- Otherwise: 8 pts.

**Port proximity (15 pts max)**

- Same port as preferred crew-change port: 15 pts.
- Same country/region: 8 pts.
- Otherwise: 5 pts. (Travel agent will assess feasibility separately.)

**Document validity (15 pts max)**

- STCW `Valid`: +5 pts.
- Visa `Valid` for destination country: +5 pts.
- Medical not expiring within 60 days of sign-on: +0 pts (assumed; flag if
  expiring sooner).
- Additional certifications: +1 pt each, capped at +5 pts.
- If `doc_score >= 12`, include "All documents valid" in `match_reasons`.

**Experience (10 pts max)**

- Score = `min(10, years * 0.7)`.
- If years >= 10, add `"{years} years experience"` to `match_reasons`.

## Tie-Breaking

When two candidates score within 2.0 of each other:

1. Prefer the candidate with the longer continuous validity window on STCW
   and Medical.
2. Then prefer the candidate already at the preferred embarkation port.
3. Then prefer higher experience years.

## Reporting

Always include the contributing factors in `match_reasons`. Never return a
bare score without reasons — the coordinator and human reviewers rely on
them for auditability.

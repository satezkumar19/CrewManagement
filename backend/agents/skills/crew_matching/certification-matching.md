# Skill: Certification Matching

This skill describes the certifications and document fields the Crew Matching
agent must inspect on every candidate, what counts as "valid", and how to
report concerns.

## Required Document Fields (per crew record)

- `stcw_status` — STCW 2010 amendments compliance. Values: `Valid`,
  `Expired`, `Expiring`.
- `medical_expiry` — ISO date of next medical fitness expiry.
- `visa_status` — Visa state for the destination country. Values: `Valid`,
  `Pending`, `Expired`, `NotRequired`.
- `certifications` — list of additional endorsements (GMDSS, ECDIS, Tanker
  Endorsement, BOSIET, etc.).

## Validity Rules

A candidate is considered **document-valid** for matching purposes when:

1. `stcw_status == "Valid"` AND
2. The candidate's `medical_expiry` is at least 60 days after the planned
   sign-on date AND
3. `visa_status` is `Valid` or `NotRequired` for the embarkation country.

Candidates failing rule 1 or rule 3 SHOULD NOT be ranked top unless no other
candidate is available. If they must be ranked, attach a clear flag in
`match_reasons` such as `"VISA_PENDING — Compliance review required"`.

## Vessel-Type Endorsements

Some vessels require specific endorsements. The coordinator will pass a
`vessel_type` hint when available:

- **Tanker (Oil/Chem/Gas)** — requires `Tanker Endorsement` for Officer
  ranks. Without it, score the candidate at 0 for rank match and flag
  `"MISSING_TANKER_ENDORSEMENT"`.
- **Passenger** — requires `Crowd Management`, `Crisis Management`, and
  `Passenger Safety` certificates for senior officers.
- **Offshore Supply** — requires `BOSIET` and `HUET`.

If `vessel_type` is not provided, do not penalize for missing vessel-specific
endorsements but DO include a note in `match_reasons` that vessel-type was
unspecified.

## Reporting

When document concerns exist:

- Surface the concern in `match_reasons` with a stable token prefix
  (`STCW_EXPIRED`, `VISA_PENDING`, `MEDICAL_EXPIRING`,
  `MISSING_TANKER_ENDORSEMENT`).
- The Compliance specialist parses these tokens downstream — keep the
  spelling consistent.

## Out of Scope

You do not validate the *content* of certificates (e.g., issuing authority,
authenticity). That is the Compliance agent's responsibility. Your job is
to read the structured fields on the crew record and apply the rules above.

# Shared Skill: Error & Flag Token Conventions

When any specialist surfaces a concern in `match_reasons`, `flags`, or
error fields, it should use stable token strings so downstream agents and
the audit trail can parse them deterministically.

## Token Format

`UPPER_SNAKE_CASE`, no spaces, no punctuation. Optional human-readable
suffix after a colon.

Examples:

- `STCW_EXPIRED`
- `VISA_PENDING:US`
- `MEDICAL_EXPIRING:2026-07-15`
- `MISSING_TANKER_ENDORSEMENT`
- `ACTING_RANK`
- `GRADE_RELAXED`
- `ADJACENT_RANK`
- `DOCS_EXPIRING`

## When to Emit

- During scoring: prepend the token to a human-readable reason in
  `match_reasons`. Example:
  `"VISA_PENDING:US — Compliance review required"`.
- On hard error: include in a top-level `error_code` field.

## Downstream Consumers

- **Compliance agent** parses these tokens to decide which workflows to
  run.
- **Notification agent** maps tokens to stakeholder message templates.
- The **frontend** colour-codes them in the workflow timeline.

Do not invent new tokens without documenting them here.

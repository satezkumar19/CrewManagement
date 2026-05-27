# Shared Skill: Maritime Glossary

Common terms every specialist agent should understand. Add new entries here
rather than redefining inside individual agent skill files.

## Crew & Rank

- **Sign-on** — the act of a crew member joining a vessel.
- **Sign-off** — the act of a crew member leaving a vessel at end of
  contract or for repatriation.
- **Relief** — the replacement crew member arriving for a sign-on.
- **Crew change** — the combined sign-off / sign-on event at a port.

## Documents

- **STCW** — Standards of Training, Certification and Watchkeeping for
  Seafarers (IMO convention, 2010 amendments current).
- **MLC** — Maritime Labour Convention, 2006. Governs rest hours, contract
  terms, repatriation.
- **CDC** — Continuous Discharge Certificate (seaman's record book).
- **GMDSS** — Global Maritime Distress and Safety System endorsement.

## Vessel & Operations

- **Port of embarkation** — port where the relief joins the vessel.
- **Port of disembarkation** — port where the signing-off crew leaves.
- **ETA / ETD** — Estimated Time of Arrival / Departure.
- **Crew change window** — the duration the vessel is alongside and able
  to perform crew exchanges.

## Status Fields (used in the crew database)

- `Available`, `Onboard`, `OnLeave`, `Unavailable` — crew availability
  state.
- `Valid`, `Expired`, `Expiring`, `Pending`, `NotRequired` — document
  states.
- Grades `A`, `B`, `C`, `D` — operator-defined performance tiers.

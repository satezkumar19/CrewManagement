# Skill: Rank Equivalence

Maritime rank titles vary across flags, operators, and trades. This skill
gives the Crew Matching agent a canonical hierarchy and equivalence map so
that "Chief Officer", "Chief Mate", and "C/O" all match the same vacancy.

## Canonical Rank Hierarchy (Deck)

1. Master / Captain
2. Chief Officer / Chief Mate
3. Second Officer / Second Mate
4. Third Officer / Third Mate
5. Deck Cadet
6. Bosun
7. Able Seaman (AB)
8. Ordinary Seaman (OS)

## Canonical Rank Hierarchy (Engine)

1. Chief Engineer
2. Second Engineer / First Assistant Engineer
3. Third Engineer
4. Fourth Engineer
5. Engine Cadet
6. Electro-Technical Officer (ETO)
7. Motorman / Oiler
8. Wiper

## Equivalence Aliases

Treat these as exact matches for ranking purposes:

| Canonical              | Aliases                                             |
| ---------------------- | --------------------------------------------------- |
| Master                 | Captain, Ship Master, Skipper                       |
| Chief Officer          | Chief Mate, C/O, 1st Officer, Chief Mate (CM)       |
| Second Officer         | Second Mate, 2/O, 2nd Mate                          |
| Third Officer          | Third Mate, 3/O, 3rd Mate                           |
| Chief Engineer         | C/E, CE, Chief Eng                                  |
| Second Engineer        | 2/E, First Assistant Engineer, 1st A/E              |
| Third Engineer         | 3/E                                                 |
| Fourth Engineer        | 4/E                                                 |
| Able Seaman            | AB, Able Bodied Seaman                              |
| Ordinary Seaman        | OS                                                  |
| Electro-Technical Off. | ETO, Electrical Officer                             |

## Adjacency Rules

For scoring purposes, "adjacent" means one tier above or below in the same
department (Deck or Engine). Cross-department matches are NOT adjacent
(e.g., Chief Officer is not adjacent to Chief Engineer).

## Promotion / Acting Rank

If a candidate's record shows `acting_rank` equal to the target rank but
their substantive `rank` is one tier lower, treat as an **exact match** for
the rank dimension but cap their `confidence_score` at 85 and add
`"ACTING_RANK"` to `match_reasons`.

## Out of Scope

Pay grade equivalence, union-specific titles, and licence authority mapping
are out of scope. The Compliance agent handles licence-authority validation.

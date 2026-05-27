# Skill: Port Proximity

The Crew Matching agent considers the candidate's current location and the
vessel's planned crew-change port. This skill defines the scoring buckets
and the region groupings used for "same region" matches.

## Scoring Buckets

- **Same port** — candidate's `port` exactly equals the vessel's preferred
  crew-change port (case-insensitive). Award full 15 pts.
- **Same region** — candidate is in the same regional grouping (see below).
  Award 8 pts.
- **Different region** — award 5 pts. The Travel agent will determine if
  the crew-change is feasible within the window.

## Regional Groupings

These groupings exist purely for proximity scoring. They are not navigation
or political designations.

- **NW Europe**: Rotterdam, Antwerp, Hamburg, Bremerhaven, Felixstowe,
  Le Havre, Zeebrugge.
- **Mediterranean**: Algeciras, Valencia, Barcelona, Genoa, Piraeus, Malta,
  Gioia Tauro.
- **Middle East / Gulf**: Jebel Ali, Fujairah, Khor Fakkan, Sohar,
  Dammam, Jeddah.
- **South Asia**: Mumbai, Chennai, Colombo, Karachi.
- **SE Asia**: Singapore, Port Klang, Tanjung Pelepas, Jakarta, Manila.
- **NE Asia**: Shanghai, Ningbo, Busan, Tokyo, Yokohama, Kaohsiung.
- **US East Coast**: New York, Norfolk, Savannah, Charleston, Miami.
- **US West Coast**: Los Angeles, Long Beach, Oakland, Seattle.
- **South America East**: Santos, Buenos Aires, Itajai.
- **South America West**: Callao, Valparaiso, Guayaquil.
- **West Africa**: Lagos, Tema, Abidjan, Lome, Pointe-Noire.
- **South Africa**: Durban, Cape Town, Port Elizabeth.

A port not listed above is treated as its own region.

## Reporting

When awarding "Same port", include `"Same port: <port>"` in
`match_reasons`. When awarding "Same region", include
`"Same region: <region>"`. Do not include a reason when the candidate is in
a different region — the absence is informative.

## Out of Scope

Cost of flights, visa transit feasibility, and crew-change window
constraints are not assessed here. They are the Travel agent's
responsibility.

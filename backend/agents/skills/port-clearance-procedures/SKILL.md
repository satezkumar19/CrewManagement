---
name: port-clearance-procedures
description: Port exit/immigration clearance procedures for crew sign-off — required documents, lead times, and port-agent coordination per authority. Use with the generatePortClearance tool.
---

# Port Clearance Procedures

Apply when generating a sign-off port clearance via `generatePortClearance`. The
clearance authorises a crew member to leave the vessel and the port, and to proceed
to the airport for repatriation.

> Procedures vary by port authority and flag state. The lead times and document
> lists below are sensible defaults — confirm specifics with the local port agent.

## Required documents (standard set)

- Valid passport and **Seafarer's Identity Document / CDC (seaman's book)**
- **Seafarer's Employment Agreement (SEA)** and sign-off entry
- **Crew list amendment** / Off-signer list lodged with immigration
- Onward **flight ticket** (proof of repatriation)
- Master's/agent's **clearance request** to the port authority
- Health/quarantine declaration where the port requires it

## Lead times

- Lodge the off-signer with the port agent and immigration **at least 24 hours**
  before sign-off (48h at busy hubs).
- Clearance is typically **valid for 72 hours** from issue — align it with the
  flight departure so it does not expire before travel.

## Port-agent coordination

- The local **port agent** is the primary interface with the authority. Address the
  clearance to the correct authority for the port. Known authorities include:
  - Singapore — Maritime and Port Authority of Singapore
  - Rotterdam — Port of Rotterdam Authority
  - Houston — Port of Houston Authority
  - Dubai — Dubai Ports World
  - Shanghai — Shanghai International Port Group
  - Hamburg — Hamburg Port Authority
  - Piraeus — Piraeus Port Authority
  - Manila — Philippine Ports Authority
  - Mumbai — Mumbai Port Trust
  - Busan — Busan Port Authority
- For ports not listed, address it to "&lt;Port&gt; Port Authority" and note that the
  agent must confirm the exact authority name.

## Special cases to flag

- **Expired or near-expiry documents** (passport, CDC, visa) — flag; clearance may be refused.
- **Crew under investigation / medical hold** — do not issue; escalate to Shore Manager.
- **Port restrictions or strikes** — note expected delay and adjust the flight lead time.

## Output

Issue the clearance with: crew member, rank, vessel, port, issuing authority,
clearance date, validity window, and status. List any document gaps or special-case
flags rather than silently approving.

---
name: crew-travel-policy
description: Booking policy for maritime crew sign-off travel — cabin class by rank, routing and layover rules, cost ceilings, booking lead time, and airline preferences. Use whenever arranging flights with the generateTicket tool.
---

# Crew Travel Booking Policy

Apply this policy whenever you arrange sign-off travel via `generateTicket`. The
goal is safe, timely, cost-appropriate repatriation that matches the crew
member's rank and the route.

> Values below are the company standard. Treat them as defaults; note any
> exception explicitly in the travel summary.

## Cabin class by rank and flight duration

| Rank tier | Short-haul (< 4h) | Medium (4–8h) | Long-haul (> 8h) |
|---|---|---|---|
| Master, Chief Engineer | Economy | Premium Economy | Business |
| Senior Officers (C/O, 2/E, additional) | Economy | Economy | Premium Economy |
| Junior Officers & Cadets | Economy | Economy | Economy |
| Ratings | Economy | Economy | Economy |

If a higher class is unavailable or exceeds the cost ceiling, book the next class
down and note it.

## Routing rules

- Prefer **direct flights**. Allow at most **one connection**.
- Keep total layover time **≤ 4 hours**; never book a layover requiring an overnight
  unless no alternative exists (then arrange transit accommodation separately).
- For crew finishing a long contract, **avoid red-eye departures** within 12 hours
  of sign-off — allow rest before travel.
- Route to the crew member's **home country / place of engagement** (see
  repatriation-rules), not merely the nearest hub.

## Booking lead time

- Book **48–72 hours** before the sign-off date where possible.
- For same-day or next-day sign-offs, book immediately and flag as expedited.

## Cost control

- Soft ceiling: **USD 1,500** per economy ticket, **USD 3,000** for business.
- Any ticket exceeding its ceiling requires **Shore Manager approval** — set the
  notification priority to `high` and state the amount and reason.

## Airline preference

- Prefer carriers with strong maritime-crew handling and the alliance partners the
  company has agreements with. Favor reputable full-service carriers over the
  cheapest option when the price difference is within ~10%.
- Record the airline, flight number, class, and price in the travel summary.

## Output

After booking, produce a concise travel summary stating: passenger, rank, route,
airline + flight, cabin class (and any downgrade/exception), price, and whether
approval is required.

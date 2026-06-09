"""
Operator notifications — L3's "notify operators via the correct channel".

(This is Venny's slice of L3.) Given the supervisor's outcome, build a message for
each operator who needs to know, route it to that operator's CORRECT channel, and
record a delivery result. The top-ranked candidate (the proposed crew) is also
notified on their own channel — satisfying the "crew notified via correct channel"
exit criterion.

Delivery here is a mock sink (returns a delivery record) so L3 is testable with no
SMTP/Slack infra; the channel-selection logic is the real, asserted behaviour. The
seam is the same one the existing NotificationAgent uses, so a productionised L3 can
swap `_deliver` for the real mailer/Slack client without touching routing.
"""
from typing import Any, Callable, Dict, List, Optional

from agents.intelligence.schemas import IntelResult, OperatorNotification, RankedCandidate, SignOffContext

# Recipient role → the correct channel for that role. Operators differ: shoreside
# managers get email, the vessel/port get the fastest reliable channel, ops watch
# rooms get Slack, and seafarers get SMS (often the only reliable channel at sea).
CHANNEL_BY_ROLE: Dict[str, str] = {
    "Crewing Manager": "email",
    "Fleet Manager": "email",
    "Vessel Master": "email",
    "Port Agent": "sms",
    "Ops Center": "slack",
    "Crew": "sms",
}
DEFAULT_CHANNEL = "email"


def channel_for(role: str) -> str:
    """The correct channel for a recipient role (falls back to email)."""
    return CHANNEL_BY_ROLE.get(role, DEFAULT_CHANNEL)


class OperatorNotifier:
    def __init__(self, event_callback: Optional[Callable] = None, sink: Optional[Callable] = None):
        self.event_callback = event_callback
        # sink(notification) -> bool delivered. Defaults to the mock sink.
        self._sink = sink or self._mock_sink

    async def notify(self, result: IntelResult, context: SignOffContext) -> List[OperatorNotification]:
        """Dispatch the operator + crew notifications for a supervisor outcome."""
        messages = (
            self._no_crew_messages(context)
            if result.status == "no_crew_found"
            else self._match_messages(context, result.candidates)
        )

        out: List[OperatorNotification] = []
        for role, recipient, subject, body in messages:
            channel = channel_for(role)
            delivered = False
            try:
                delivered = await self._maybe_async(self._sink, role, recipient, channel, subject, body)
            except Exception:
                delivered = False
            note = OperatorNotification(
                recipient=recipient, role=role, channel=channel,
                status="delivered" if delivered else "failed",
                subject=subject, body=body,
            )
            out.append(note)
            await self._emit("intel_notification_sent", note.to_dict())
        return out

    # ── message builders ───────────────────────────────────────────────────────────
    def _match_messages(self, ctx: SignOffContext, candidates: List[RankedCandidate]):
        top = candidates[0]
        shortlist = ", ".join(f"#{c.rank_position} {c.name} ({c.score})" for c in candidates)
        ops = [
            ("Crewing Manager", "crewing@marinecrewos.local",
             f"Replacement shortlist for {ctx.vacated_rank} on {ctx.vessel or 'vessel'}",
             f"Top-{len(candidates)} candidates for the {ctx.vacated_rank} vacancy at {ctx.port}: {shortlist}. "
             f"Recommended: {top.name} — {'; '.join(top.rationale)}."),
            ("Vessel Master", "master@marinecrewos.local",
             f"Proposed {ctx.vacated_rank}: {top.name}",
             f"{top.name} (score {top.score}) is proposed to cover the {ctx.vacated_rank} vacancy at {ctx.port}. "
             f"Rationale: {'; '.join(top.rationale)}."),
            # The proposed crew member — notified on the crew channel (SMS).
            ("Crew", f"crew:{top.crew_id}",
             f"You are proposed for a {ctx.vacated_rank} assignment",
             f"You have been shortlisted (#{top.rank_position}) for the {ctx.vacated_rank} role joining at "
             f"{ctx.port}. Please confirm availability."),
        ]
        return ops

    def _no_crew_messages(self, ctx: SignOffContext):
        return [
            ("Crewing Manager", "crewing@marinecrewos.local",
             f"NO ELIGIBLE CREW for {ctx.vacated_rank} on {ctx.vessel or 'vessel'}",
             f"No candidate in the pool cleared the eligibility gates for the {ctx.vacated_rank} vacancy at "
             f"{ctx.port}. Escalating for manual sourcing / pool expansion."),
            ("Ops Center", "ops@marinecrewos.local",
             f"Vacancy unfilled: {ctx.vacated_rank}",
             f"Automated matching returned no eligible crew for {ctx.vacated_rank} at {ctx.port}. Manual action required."),
        ]

    # ── delivery sink ──────────────────────────────────────────────────────────────
    async def _mock_sink(self, role, recipient, channel, subject, body) -> bool:
        # Stand-in for the real mailer/SMS/Slack client. Always "delivers" unless a
        # recipient/channel is missing — enough to exercise routing + delivery tests.
        return bool(recipient and channel)

    @staticmethod
    async def _maybe_async(fn: Callable, *args) -> bool:
        res = fn(*args)
        if hasattr(res, "__await__"):
            return await res
        return bool(res)

    async def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if not self.event_callback:
            return
        try:
            await self.event_callback(event_type=event_type, agent_name="Operator Notifier", data=data)
        except Exception:
            pass

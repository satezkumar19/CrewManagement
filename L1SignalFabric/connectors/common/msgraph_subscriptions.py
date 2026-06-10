"""Microsoft Graph change-subscription lifecycle — shared by Outlook + SharePoint.

A Graph subscription is what makes push *hands-off*: it tells Graph to POST a
change notification to our ``notificationUrl`` whenever the watched ``resource``
changes, instead of us polling. This module is the *send* side (create / list /
renew / delete a subscription); :mod:`connectors.common.msgraph_webhook` is the
*receive* side (validate the handshake + ``clientState`` on each notification).

Two facts shape the design:

  * **Validation at create time** — when a subscription is created or renewed,
    Graph immediately calls ``notificationUrl?validationToken=...`` and expects
    the token echoed back (text/plain, 200) within 10s. So the receiving server
    must already be running and publicly reachable (e.g. an ngrok tunnel) *before*
    :meth:`create` is called. Our webhook routes handle that handshake.
  * **Short max lifetime → renewal** — Graph caps mail and ``driveItem``
    subscriptions at roughly three days, so they must be renewed on a schedule
    (mirroring the Gmail ``watch`` renewal). :meth:`renew` PATCHes a new
    ``expirationDateTime``; :meth:`list` shows what is live and when it expires.

Auth is the same app-only client-credentials grant the Graph connectors already
use (``Mail.Read`` for mail; the granted site for ``driveItem``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .graph import GraphClient
from .logger import StructuredLogger

# Graph's documented maximum subscription length for mail and driveItem is ~4230
# minutes (just under three days); request slightly under it to avoid a boundary
# rejection. Graph echoes the allowed window in its 400 body if this is too long.
DEFAULT_EXPIRATION_MINUTES = 4200


def iso_expiration(minutes: int) -> str:
    """ISO-8601 UTC timestamp ``minutes`` from now, in Graph's expected shape."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


class GraphSubscriptionManager:
    """Create / list / renew / delete Graph change subscriptions (app-only).

        mgr = GraphSubscriptionManager(tenant_id, client_id, client_secret)
        sub = mgr.create(resource="users/x@y.com/messages", change_type="created",
                         notification_url="https://host/outlook/webhook",
                         client_state="secret")
        mgr.renew(sub["id"])          # before it expires
        mgr.delete(sub["id"])
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._graph = GraphClient(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            logger=logger,
        )

    def create(
        self,
        *,
        resource: str,
        change_type: str,
        notification_url: str,
        client_state: str = "",
        minutes: int = DEFAULT_EXPIRATION_MINUTES,
    ) -> Dict[str, Any]:
        """Create a subscription. Graph validates ``notification_url`` synchronously."""
        body: Dict[str, Any] = {
            "changeType": change_type,
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": iso_expiration(minutes),
        }
        if client_state:
            body["clientState"] = client_state
        return self._graph.post("/subscriptions", json=body)

    def list(self) -> List[Dict[str, Any]]:
        """All subscriptions currently visible to this app."""
        return self._graph.get("/subscriptions").get("value", [])

    def renew(self, subscription_id: str,
              minutes: int = DEFAULT_EXPIRATION_MINUTES) -> Dict[str, Any]:
        """Push the expiry out by ``minutes`` from now (PATCH)."""
        return self._graph.patch(
            f"/subscriptions/{subscription_id}",
            json={"expirationDateTime": iso_expiration(minutes)},
        )

    def delete(self, subscription_id: str) -> None:
        self._graph.delete(f"/subscriptions/{subscription_id}")

    @property
    def api_calls(self) -> int:
        return self._graph.api_calls

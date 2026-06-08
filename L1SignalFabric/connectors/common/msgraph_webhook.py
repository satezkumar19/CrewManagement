"""Microsoft Graph change-notification webhook verification (Outlook + SharePoint).

Graph webhooks have two inbound shapes:

  * **subscription validation** — when you create/renew a subscription, Graph
    issues a request with a ``validationToken`` query parameter that the endpoint
    must echo back verbatim (text/plain, 200) within 10s. → ``CHALLENGE``.
  * **change notification** — a JSON body ``{"value": [{clientState, resource,
    resourceData, changeType, subscriptionId}, ...]}``. Each item carries the
    ``clientState`` secret set at subscription time, which we compare in constant
    time to authenticate the push. → ``OK`` / ``REJECT``.

A dev bypass (no client state configured) accepts unverified notifications so the
fixture/replay demo runs without Azure — mirroring Slack/Gmail.
"""

from __future__ import annotations

import hmac
from typing import Any, List

from core.connector import InboundRequest, VerifyResult


def verify_graph_webhook(
    request: InboundRequest,
    *,
    client_state: str = "",
    dev_allow_unverified: bool = True,
) -> VerifyResult:
    # 1) subscription validation handshake
    token = request.q("validationToken")
    if token:
        return VerifyResult.challenge_with(token)

    # 2) change notification — verify clientState on every item
    body = request.json or {}
    items: List[dict] = body.get("value", []) if isinstance(body, dict) else []
    if client_state:
        for item in items:
            if not hmac.compare_digest(client_state, str(item.get("clientState", ""))):
                return VerifyResult.reject("clientState mismatch")
        return VerifyResult.ok()

    if dev_allow_unverified:
        return VerifyResult.ok()
    return VerifyResult.reject("no clientState configured")


def notification_items(raw: dict[str, Any]) -> List[dict]:
    """Pull the change-notification items out of a Graph webhook body."""
    if not isinstance(raw, dict):
        return []
    return raw.get("value", []) or []

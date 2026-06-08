"""Gmail Pub/Sub push ingress — ``POST /gmail/push``.

Google Cloud Pub/Sub delivers a JSON envelope carrying a base64 ``historyId``
notification. The GmailConnector verifies it (shared-secret ``?token=`` or OIDC
JWT), expands the new history to message *metadata*, and publishes EMAIL signals.
Thin transport glue — all logic lives in the connector.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ._push import handle_push

router = APIRouter(tags=["gmail"])


@router.post("/gmail/push")
async def gmail_push(request: Request) -> Response:
    return await handle_push(request, request.app.state.gmail, source="gmail")

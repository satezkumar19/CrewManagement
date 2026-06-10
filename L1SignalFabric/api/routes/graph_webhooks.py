"""Microsoft Graph change-notification ingress — Outlook & SharePoint.

``POST /outlook/webhook``   — Graph mail change notifications → OUTLOOK events.
``POST /sharepoint/webhook`` — Graph drive/list change notifications → a delta poll.

Both handle the Graph subscription-validation handshake (echo ``validationToken``)
and ``clientState`` authentication via the shared push helper. Thin glue only.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ._push import handle_push

router = APIRouter(tags=["msgraph"])


@router.post("/outlook/webhook")
async def outlook_webhook(request: Request) -> Response:
    return await handle_push(request, request.app.state.outlook, source="outlook")


@router.post("/sharepoint/webhook")
async def sharepoint_webhook(request: Request) -> Response:
    return await handle_push(request, request.app.state.sharepoint, source="sharepoint")

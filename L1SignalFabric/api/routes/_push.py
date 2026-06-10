"""Shared transport glue for push (webhook) connectors.

Adapts a Starlette ``Request`` into the framework-agnostic
:class:`~core.connector.InboundRequest`, runs the connector's verify → ingest →
publish path, and returns the right response for each :class:`VerifyOutcome`.
Every push route (Gmail Pub/Sub, Outlook/SharePoint Graph webhooks) is a thin
wrapper over :func:`handle_push`; all source-specific logic stays in the connector.
"""

from __future__ import annotations

import json
import logging

from fastapi import Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from core.connector import InboundRequest, VerifyOutcome

logger = logging.getLogger("signalfabric.api.push")


async def to_inbound(request: Request) -> tuple[InboundRequest, bytes]:
    raw_body = await request.body()
    try:
        body_json = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        body_json = None
    inbound = InboundRequest(
        headers={k: v for k, v in request.headers.items()},
        body=raw_body,
        json=body_json if isinstance(body_json, dict) else None,
        query={k: v for k, v in request.query_params.items()},
    )
    return inbound, raw_body


async def handle_push(request: Request, connector, *, source: str) -> Response:
    inbound, raw_body = await to_inbound(request)
    if inbound.json is None and raw_body:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    bus = request.app.state.bus
    result = connector.verify(inbound)

    # 1) handshake — echo the challenge token verbatim (Graph validationToken).
    if result.outcome == VerifyOutcome.CHALLENGE:
        return PlainTextResponse(result.challenge or "")
    # 2) rejected — inauthentic push.
    if result.outcome == VerifyOutcome.REJECT:
        logger.warning("%s push rejected: %s", source, result.reason)
        return JSONResponse({"error": "unauthorized", "reason": result.reason}, status_code=401)

    # 3) authentic — normalize and publish, then ack fast.
    if hasattr(bus, "note_ingress"):
        bus.note_ingress(source)
    events = await connector.ingest(inbound.json or {})
    for event in events:
        # carry the raw webhook push so the dashboard drawer can show
        # raw → normalized → L2 for live events (not just demo injections).
        event.metadata["_ingress_raw"] = inbound.json
        await bus.publish(event)
    return JSONResponse({"ok": True, "ingested": len(events)})

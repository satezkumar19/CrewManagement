"""Slack Events API ingress route.

Responsibilities (thin): read the raw request, hand it to the SlackConnector for
verification + normalization, publish resulting SignalEvents to the bus, and ack
fast (Slack requires a response within 3s). All Slack-specific logic lives in the
connector; this route is transport glue.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from core.connector import InboundRequest, VerifyOutcome

logger = logging.getLogger("signalfabric.api.slack")

router = APIRouter(tags=["slack"])


@router.post("/slack/events")
async def slack_events(request: Request) -> Response:
    raw_body = await request.body()
    try:
        body_json = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    inbound = InboundRequest(
        headers={k: v for k, v in request.headers.items()},
        body=raw_body,
        json=body_json,
    )

    connector = request.app.state.slack
    bus = request.app.state.bus

    result = connector.verify(inbound)

    # 1) URL verification handshake — echo the challenge plainly.
    if result.outcome == VerifyOutcome.CHALLENGE:
        return PlainTextResponse(result.challenge or "")

    # 2) Rejected — inauthentic request.
    if result.outcome == VerifyOutcome.REJECT:
        logger.warning("slack request rejected: %s", result.reason)
        return JSONResponse({"error": "unauthorized", "reason": result.reason}, status_code=401)

    # 3) Authentic — normalize and publish, then ack.
    events = await connector.ingest(body_json)
    for event in events:
        await bus.publish(event)

    return JSONResponse({"ok": True, "ingested": len(events)})

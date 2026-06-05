"""FastAPI application factory for L1 SignalFabric.

Wires the connectors (Slack push, ERP pull) and the event bus onto ``app.state``
and mounts the ingress routes. The bus defaults to the Day-1 placeholder
``LoggingEventBus``; pass Sruthy's ``InMemoryBus`` (same Protocol) to integrate.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI

from config import SERVICE_NAME, SERVICE_VERSION, Settings
from config import settings as default_settings
from connectors.erp import ErpConnector, InMemoryOutboxAdapter
from connectors.slack import SlackConnector
from core.bus import EventBus, LoggingEventBus

from .routes import health, slack

logging.basicConfig(level=logging.INFO)


def create_app(
    *,
    settings: Optional[Settings] = None,
    bus: Optional[EventBus] = None,
) -> FastAPI:
    cfg = settings or default_settings
    app = FastAPI(title=SERVICE_NAME, version=SERVICE_VERSION)

    # --- bus (placeholder until Sruthy's InMemoryBus is injected) ---
    app.state.bus = bus or LoggingEventBus()

    # --- connectors ---
    app.state.slack = SlackConnector(
        tenant_id=cfg.tenant_id,
        signing_secret=cfg.slack_signing_secret,
        dev_allow_unverified=cfg.slack_dev_allow_unverified,
        replay_window_sec=cfg.slack_replay_window_sec,
    )
    app.state.erp = ErpConnector(
        tenant_id=cfg.tenant_id,
        adapter=InMemoryOutboxAdapter(),  # mimic; swap for Postgres outbox on Day 4
    )
    app.state.connectors = [app.state.slack, app.state.erp]

    # --- routes ---
    app.include_router(health.router)
    app.include_router(slack.router)

    return app


# Module-level app for `uvicorn api.app:app`
app = create_app()

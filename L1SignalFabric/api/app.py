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
from core.bus import EventBus
from l2 import L2JsonlStore

from . import live
from .routes import health, slack

logging.basicConfig(level=logging.INFO)


def create_app(
    *,
    settings: Optional[Settings] = None,
    bus: Optional[EventBus] = None,
) -> FastAPI:
    cfg = settings or default_settings
    app = FastAPI(title=SERVICE_NAME, version=SERVICE_VERSION)
    app.state.tenant_id = cfg.tenant_id

    # --- bus: SSE-broadcasting viewer bus by default (drives the dashboard);
    #     pass Sruthy's InMemoryBus to create_app(bus=...) to integrate L2 ---
    bus_obj = bus or live.BroadcastBus()
    app.state.bus = bus_obj

    # --- L2 store + sink: project every published event into the L2 JSONL store
    #     (the downstream end of the demo pipe). Only when the bus supports a sink. ---
    app.state.l2_store = None
    if isinstance(bus_obj, live.BroadcastBus):
        store = L2JsonlStore(cfg.l2_store_path)
        app.state.l2_store = store
        bus_obj.set_sink(store.append)

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
    app.include_router(live.router)   # GET / (dashboard), /stream (SSE), /demo/*

    return app


# Module-level app for `uvicorn api.app:app`
app = create_app()

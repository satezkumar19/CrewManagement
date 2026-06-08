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
from connectors.database import (
    DatabaseConnector,
    InMemoryOutboxAdapter as DbInMemoryOutboxAdapter,
    OutboxAdapter,
)
from connectors.erp import ErpConnector, InMemoryOutboxAdapter
from connectors.gmail import GmailClient, GmailConnector
from connectors.notion import NotionClient, NotionConnector
from connectors.outlook import OutlookClient, OutlookConnector
from connectors.sharepoint import SharePointConnector
from connectors.slack import SlackConnector
from core.bus import EventBus, InMemoryBus
from core.watermark import FileWatermarkStore
from l2 import L2JsonlStore, OrgMap

from . import live
from .routes import graph_webhooks, gmail, health, slack

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("signalfabric.api.app")


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
    #     (the downstream end of the demo pipe). Wiring differs per bus:
    #       * BroadcastBus (SSE viewer, default) — single sink via set_sink()
    #       * InMemoryBus (core transport)       — subscribe() the sink (fan-out)
    #     Either way "no change to any connector or route" (README seam). ---
    #     Each projected record is also upserted into the in-memory OrgMap graph
    #     (the real-graph counterpart to the JSONL store) for the OrgMap viewer.
    app.state.l2_store = None
    app.state.orgmap = None
    if isinstance(bus_obj, (live.BroadcastBus, InMemoryBus)):
        store = L2JsonlStore(cfg.l2_store_path)
        orgmap = OrgMap()
        app.state.l2_store = store
        app.state.orgmap = orgmap

        def _l2_sink(event, _store=store, _orgmap=orgmap):
            rec = _store.append(event)
            try:
                _orgmap.upsert(rec)          # graph upsert is best-effort; never breaks the sink
            except Exception:
                logger.exception("orgmap upsert failed")
            return rec

        if isinstance(bus_obj, live.BroadcastBus):
            bus_obj.set_sink(_l2_sink)       # single sink (drives SSE counters)
        else:
            bus_obj.subscribe(_l2_sink)      # fan-out subscriber

    # --- connectors ---
    # Each real connector boots in dev/fixture mode when its credentials are
    # blank (no client, push connectors accept replayed fixtures, pull connectors
    # no-op) — so a fresh checkout runs the demo without any secrets. Supplying a
    # token/URL upgrades that connector to live with no other change.
    app.state.slack = SlackConnector(
        tenant_id=cfg.tenant_id,
        signing_secret=cfg.slack_signing_secret,
        dev_allow_unverified=cfg.slack_dev_allow_unverified,
        replay_window_sec=cfg.slack_replay_window_sec,
        token=cfg.slack_token,   # resolve channel/user ids → human names when set
    )
    app.state.erp = ErpConnector(
        tenant_id=cfg.tenant_id,
        adapter=InMemoryOutboxAdapter(),  # mimic; swap for Postgres outbox on Day 4
    )

    notion_client = (NotionClient(cfg.notion_token) if cfg.notion_token else None)
    app.state.notion = (
        NotionConnector(tenant_id=cfg.tenant_id, client=notion_client)
        if notion_client else None
    )

    gmail_client = (GmailClient(cfg.gmail_access_token) if cfg.gmail_access_token else None)
    app.state.gmail = GmailConnector(
        tenant_id=cfg.tenant_id,
        client=gmail_client,
        pubsub_token=cfg.gmail_pubsub_token,
        oidc_audience=cfg.gmail_oidc_audience,
        dev_allow_unverified=cfg.gmail_dev_allow_unverified,
    )

    outlook_client = _build_outlook_client(cfg)
    app.state.outlook = OutlookConnector(
        tenant_id=cfg.tenant_id,
        client=outlook_client,
        client_state=cfg.outlook_client_state,
        dev_allow_unverified=cfg.outlook_dev_allow_unverified,
    )

    app.state.sharepoint = SharePointConnector(
        tenant_id=cfg.tenant_id,
        client=None,  # targets/site ids are deployment-specific; wire via config
        client_state=cfg.sharepoint_client_state,
        dev_allow_unverified=cfg.sharepoint_dev_allow_unverified,
    )

    db_wm = FileWatermarkStore(cfg.database_watermark_path) if cfg.database_watermark_path else None
    db_adapter = (OutboxAdapter(url=cfg.database_url, table=cfg.database_outbox_table)
                  if cfg.database_url else DbInMemoryOutboxAdapter())
    app.state.database = DatabaseConnector(
        tenant_id=cfg.tenant_id, adapter=db_adapter, watermarks=db_wm,
    )

    app.state.connectors = [
        c for c in (app.state.slack, app.state.gmail, app.state.outlook,
                    app.state.sharepoint, app.state.notion, app.state.database,
                    app.state.erp)
        if c is not None
    ]

    # --- routes ---
    app.include_router(health.router)
    app.include_router(slack.router)
    app.include_router(gmail.router)              # POST /gmail/push
    app.include_router(graph_webhooks.router)     # POST /outlook/webhook, /sharepoint/webhook
    app.include_router(live.router)   # GET / (dashboard), /stream (SSE), /demo/*

    return app


def _build_outlook_client(cfg: Settings):
    """Build an OutlookClient if Graph creds are present, else None (fixture mode)."""
    from connectors.common import GraphClient
    if cfg.outlook_access_token:
        return OutlookClient(GraphClient(access_token=cfg.outlook_access_token))
    if cfg.ms_tenant_id and cfg.ms_client_id and cfg.ms_client_secret:
        return OutlookClient(GraphClient(tenant_id=cfg.ms_tenant_id,
                                         client_id=cfg.ms_client_id,
                                         client_secret=cfg.ms_client_secret))
    return None


# Module-level app for `uvicorn api.app:app`
app = create_app()

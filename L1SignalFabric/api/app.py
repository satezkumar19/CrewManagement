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
from connectors.sharepoint import SharePointClient, SharePointConnector
from connectors.slack import SlackConnector
from core.bus import EventBus, InMemoryBus
from core.watermark import FileWatermarkStore
from l2 import L2JsonlStore, L2Router, OrgMap, project_record

from . import live
from .routes import graph_records, graph_webhooks, gmail, health, slack

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
    #     The unified L2Router additionally fans each event's EntityMap + OpsMap
    #     facets (project_record) into their in-memory maps, so one bus event
    #     drives every map — exposed under /api/v1/graph/* alongside the wire
    #     ingress POST /api/v1/graph/records.
    app.state.l2_store = None
    app.state.orgmap = None
    app.state.l2_router = None
    if isinstance(bus_obj, (live.BroadcastBus, InMemoryBus)):
        store = L2JsonlStore(cfg.l2_store_path)
        orgmap = OrgMap()
        router = L2Router(orgmap=orgmap)     # OrgMap stays the legacy path; +Entity/Ops
        app.state.l2_store = store
        app.state.orgmap = orgmap
        app.state.l2_router = router

        def _l2_sink(event, _store=store, _orgmap=orgmap, _router=router):
            rec = _store.append(event)
            try:
                _orgmap.upsert(rec)          # graph upsert is best-effort; never breaks the sink
            except Exception:
                logger.exception("orgmap upsert failed")
            try:                             # EntityMap + OpsMap facets (unified record)
                _router.route_entity_ops(project_record(event))
            except Exception:
                logger.exception("l2 entity/ops routing failed")
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

    gmail_client = _build_gmail_client(cfg)
    gmail_wm = FileWatermarkStore(cfg.gmail_watermark_path) if cfg.gmail_watermark_path else None
    app.state.gmail = GmailConnector(
        tenant_id=cfg.tenant_id,
        client=gmail_client,
        pubsub_token=cfg.gmail_pubsub_token,
        oidc_audience=cfg.gmail_oidc_audience,
        dev_allow_unverified=cfg.gmail_dev_allow_unverified,
        watermarks=gmail_wm,   # persist historyId so pushes resume across restarts
    )

    outlook_client = _build_outlook_client(cfg)
    app.state.outlook = OutlookConnector(
        tenant_id=cfg.tenant_id,
        client=outlook_client,
        mark_read=cfg.outlook_mark_as_read,
        client_state=cfg.outlook_client_state,
        dev_allow_unverified=cfg.outlook_dev_allow_unverified,
    )

    sharepoint_client = _build_sharepoint_client(cfg)
    app.state.sharepoint = SharePointConnector(
        tenant_id=cfg.tenant_id,
        client=sharepoint_client,
        folder_paths=cfg.sharepoint_folder_paths,
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
    app.include_router(graph_records.router)       # POST /api/v1/graph/records (+ read views)
    app.include_router(live.router)   # GET / (dashboard), /stream (SSE), /demo/*

    return app


def _build_gmail_client(cfg: Settings):
    """Build a GmailClient, preferring the self-refreshing OAuth flow.

    Mirrors the CLI's credential resolution: a refresh-token trio
    (client id/secret + refresh token) yields a client that mints fresh access
    tokens on demand — so the server keeps expanding history on push past the
    ~1-hour access-token lifetime. Falls back to a static GMAIL_ACCESS_TOKEN,
    else None (dev/replay mode — pushes accept inlined fixtures only).
    """
    if cfg.gmail_client_id and cfg.gmail_client_secret and cfg.gmail_refresh_token:
        from connectors.gmail.auth import OAuthTokenProvider
        return GmailClient(OAuthTokenProvider(
            client_id=cfg.gmail_client_id,
            client_secret=cfg.gmail_client_secret,
            refresh_token=cfg.gmail_refresh_token,
        ), ingest_body=cfg.email_ingest_body)
    if cfg.gmail_access_token:
        return GmailClient(cfg.gmail_access_token, ingest_body=cfg.email_ingest_body)
    return None


def _build_outlook_client(cfg: Settings):
    """Build an OutlookClient if Graph app creds + mailbox are present (else None).

    App-only (client-credentials) auth, so a target mailbox UPN is required —
    there is no signed-in ``/me``. Blank creds => None (fixture/replay mode).
    """
    if (cfg.ms_tenant_id and cfg.ms_client_id and cfg.ms_client_secret
            and cfg.outlook_mailbox_upn):
        return OutlookClient(cfg.ms_tenant_id, cfg.ms_client_id, cfg.ms_client_secret,
                             cfg.outlook_mailbox_upn, ingest_body=cfg.email_ingest_body)
    return None


def _build_sharepoint_client(cfg: Settings):
    """Build a SharePointClient if Graph app creds + site are present (else None)."""
    if (cfg.ms_tenant_id and cfg.ms_client_id and cfg.ms_client_secret
            and cfg.sharepoint_hostname and cfg.sharepoint_site_path):
        return SharePointClient(cfg.ms_tenant_id, cfg.ms_client_id, cfg.ms_client_secret,
                                cfg.sharepoint_hostname, cfg.sharepoint_site_path)
    return None


# Module-level app for `uvicorn api.app:app`
app = create_app()

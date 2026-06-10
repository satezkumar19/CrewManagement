"""Runtime configuration for L1 SignalFabric.

Plain env-driven settings (no extra dependency). Everything has a dev-safe
default so a fresh checkout boots and the Day-1 demo runs without secrets.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# A .env file is inert until something loads it. Do so before the Settings
# field defaults below read os.environ, so credentials configured in .env
# (GMAIL_PUBSUB_TOKEN, access tokens, signing secrets, …) reach the server —
# not just the CLIs.
from connectors.common.secrets import load_env

load_env()

SERVICE_NAME = "l1-signalfabric"
SERVICE_VERSION = "0.1.0"


def _load_dotenv() -> None:
    """Populate os.environ from a local ``.env`` (dependency-free).

    Real process env always wins (an exported var overrides the file), so this
    only fills in what isn't already set. Searches this service dir first, then
    the sibling ``backend/.env`` (shared monorepo secrets). Skipped under pytest
    so the suite always runs against clean dev-safe defaults (no real secrets)."""
    if "pytest" in sys.modules:
        return
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / "backend" / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()


def _flag(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    tenant_id: str = os.getenv("L1_TENANT_ID", "maritime-acme")

    # Slack
    slack_signing_secret: str = os.getenv("SLACK_SIGNING_SECRET", "")
    slack_token: str = os.getenv("SLACK_TOKEN", "")  # bot token (xoxb-…) — channel/user enrichment
    # When no signing secret is configured (local dev / demo), accept unsigned
    # requests so the url_verification handshake and replayed fixtures work.
    slack_dev_allow_unverified: bool = _flag("SLACK_DEV_ALLOW_UNVERIFIED", True)
    # Reject requests whose timestamp is older than this (replay protection).
    slack_replay_window_sec: int = int(os.getenv("SLACK_REPLAY_WINDOW_SEC", "300"))

    # ERP
    erp_watermark_path: str = os.getenv("ERP_WATERMARK_PATH", "")  # "" => in-memory

    # --- real connectors (all dev-safe: blank creds => fixture/replay mode) ---
    # Notion
    notion_token: str = os.getenv("NOTION_TOKEN", "")
    notion_token_secret_arn: str = os.getenv("NOTION_TOKEN_SECRET_ARN", "")

    # Gmail (Pub/Sub push, metadata only)
    # Per-user OAuth: client_id+secret+refresh_token => self-refreshing access
    # tokens (durable; keeps a 7-day watch serviced). Falls back to the static,
    # ~1-hour gmail_access_token when the refresh trio is unset.
    gmail_client_id: str = os.getenv("GMAIL_CLIENT_ID", "")
    gmail_client_secret: str = os.getenv("GMAIL_CLIENT_SECRET", "")
    gmail_refresh_token: str = os.getenv("GMAIL_REFRESH_TOKEN", "")
    gmail_access_token: str = os.getenv("GMAIL_ACCESS_TOKEN", "")
    gmail_pubsub_token: str = os.getenv("GMAIL_PUBSUB_TOKEN", "")          # shared-secret push auth
    gmail_oidc_audience: str = os.getenv("GMAIL_OIDC_AUDIENCE", "")        # OIDC JWT audience
    gmail_dev_allow_unverified: bool = _flag("GMAIL_DEV_ALLOW_UNVERIFIED", True)
    gmail_watermark_path: str = os.getenv("GMAIL_WATERMARK_PATH", "")      # "" => in-memory (lost on restart)

    # Outlook (Microsoft Graph mail — app-only unread poll, metadata only).
    # Mailbox UPN is required for app-only (there is no signed-in /me).
    outlook_mailbox_upn: str = os.getenv("OUTLOOK_MAILBOX_UPN", "")
    outlook_mark_as_read: bool = _flag("OUTLOOK_MARK_AS_READ", True)
    outlook_client_state: str = os.getenv("OUTLOOK_CLIENT_STATE", "")
    outlook_dev_allow_unverified: bool = _flag("OUTLOOK_DEV_ALLOW_UNVERIFIED", True)

    # SharePoint (Microsoft Graph — app-only folder listing, metadata only).
    # One site (hostname + server-relative path) and one-or-more comma/newline
    # separated folder paths under its default document library.
    sharepoint_hostname: str = os.getenv("SHAREPOINT_HOSTNAME", "")
    sharepoint_site_path: str = os.getenv("SHAREPOINT_SITE_PATH", "")
    sharepoint_folder_path: str = os.getenv("SHAREPOINT_FOLDER_PATH", "")
    sharepoint_client_state: str = os.getenv("SHAREPOINT_CLIENT_STATE", "")
    sharepoint_dev_allow_unverified: bool = _flag("SHAREPOINT_DEV_ALLOW_UNVERIFIED", True)

    # Microsoft 365 app credentials (shared by Outlook + SharePoint app-only grant)
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")
    ms_client_secret: str = os.getenv("MS_CLIENT_SECRET", "")
    # Public HTTPS base (e.g. an ngrok tunnel) Graph pushes change notifications to.
    # The `subscribe` CLIs append /outlook/webhook and /sharepoint/webhook.
    ms_webhook_base_url: str = os.getenv("MS_WEBHOOK_BASE_URL", "")

    @property
    def sharepoint_folder_paths(self) -> list:
        """Configured folder paths, split on comma/newline (empty => none)."""
        raw = self.sharepoint_folder_path.replace("\n", ",")
        return [p.strip() for p in raw.split(",") if p.strip()]

    # Database (generic SQL CDC/outbox); "" => in-memory mimic adapter
    database_url: str = os.getenv("DATABASE_URL", "")
    database_outbox_table: str = os.getenv("DATABASE_OUTBOX_TABLE", "signal_outbox")
    database_watermark_path: str = os.getenv("DATABASE_WATERMARK_PATH", "")

    # E-mail body ingestion. When on, the Gmail/Outlook connectors fetch and
    # carry the message **body** (not just From/To/Subject metadata) so the L2
    # record and crew-change parsing see the full content. This relaxes the
    # metadata-only privacy boundary the connectors otherwise enforce; set
    # EMAIL_INGEST_BODY=0 to restore metadata-only ingestion.
    email_ingest_body: bool = _flag("EMAIL_INGEST_BODY", True)

    # L2 store (append-only JSONL written by the demo L2 sink)
    l2_store_path: str = os.getenv("L2_STORE_PATH", "./data/l2_store.jsonl")

    def __post_init__(self) -> None:
        if not self.slack_signing_secret and not self.slack_dev_allow_unverified:
            raise ValueError(
                "SLACK_SIGNING_SECRET is required unless SLACK_DEV_ALLOW_UNVERIFIED=1"
            )


settings = Settings()

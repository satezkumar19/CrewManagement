"""Runtime configuration for L1 SignalFabric.

Plain env-driven settings (no extra dependency). Everything has a dev-safe
default so a fresh checkout boots and the Day-1 demo runs without secrets.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

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
    gmail_access_token: str = os.getenv("GMAIL_ACCESS_TOKEN", "")
    gmail_pubsub_token: str = os.getenv("GMAIL_PUBSUB_TOKEN", "")          # shared-secret push auth
    gmail_oidc_audience: str = os.getenv("GMAIL_OIDC_AUDIENCE", "")        # OIDC JWT audience
    gmail_dev_allow_unverified: bool = _flag("GMAIL_DEV_ALLOW_UNVERIFIED", True)

    # Outlook (Microsoft Graph mail webhook, metadata only)
    outlook_access_token: str = os.getenv("OUTLOOK_ACCESS_TOKEN", "")
    outlook_client_state: str = os.getenv("OUTLOOK_CLIENT_STATE", "")
    outlook_dev_allow_unverified: bool = _flag("OUTLOOK_DEV_ALLOW_UNVERIFIED", True)

    # SharePoint (Microsoft Graph drives/lists webhook)
    sharepoint_access_token: str = os.getenv("SHAREPOINT_ACCESS_TOKEN", "")
    sharepoint_client_state: str = os.getenv("SHAREPOINT_CLIENT_STATE", "")
    sharepoint_dev_allow_unverified: bool = _flag("SHAREPOINT_DEV_ALLOW_UNVERIFIED", True)

    # Microsoft 365 app credentials (shared by Outlook + SharePoint client-credentials grant)
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")
    ms_client_secret: str = os.getenv("MS_CLIENT_SECRET", "")

    # Database (generic SQL CDC/outbox); "" => in-memory mimic adapter
    database_url: str = os.getenv("DATABASE_URL", "")
    database_outbox_table: str = os.getenv("DATABASE_OUTBOX_TABLE", "signal_outbox")
    database_watermark_path: str = os.getenv("DATABASE_WATERMARK_PATH", "")

    # L2 store (append-only JSONL written by the demo L2 sink)
    l2_store_path: str = os.getenv("L2_STORE_PATH", "./data/l2_store.jsonl")

    def __post_init__(self) -> None:
        if not self.slack_signing_secret and not self.slack_dev_allow_unverified:
            raise ValueError(
                "SLACK_SIGNING_SECRET is required unless SLACK_DEV_ALLOW_UNVERIFIED=1"
            )


settings = Settings()

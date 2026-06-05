"""Runtime configuration for L1 SignalFabric.

Plain env-driven settings (no extra dependency). Everything has a dev-safe
default so a fresh checkout boots and the Day-1 demo runs without secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "l1-signalfabric"
SERVICE_VERSION = "0.1.0"


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
    # When no signing secret is configured (local dev / demo), accept unsigned
    # requests so the url_verification handshake and replayed fixtures work.
    slack_dev_allow_unverified: bool = _flag("SLACK_DEV_ALLOW_UNVERIFIED", True)
    # Reject requests whose timestamp is older than this (replay protection).
    slack_replay_window_sec: int = int(os.getenv("SLACK_REPLAY_WINDOW_SEC", "300"))

    # ERP
    erp_watermark_path: str = os.getenv("ERP_WATERMARK_PATH", "")  # "" => in-memory

    # L2 store (append-only JSONL written by the demo L2 sink)
    l2_store_path: str = os.getenv("L2_STORE_PATH", "./data/l2_store.jsonl")

    def __post_init__(self) -> None:
        if not self.slack_signing_secret and not self.slack_dev_allow_unverified:
            raise ValueError(
                "SLACK_SIGNING_SECRET is required unless SLACK_DEV_ALLOW_UNVERIFIED=1"
            )


settings = Settings()

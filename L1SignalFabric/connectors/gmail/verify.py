"""Gmail Pub/Sub push verification.

Google Cloud Pub/Sub authenticates a push subscription two ways; we support both:

  1. **Shared-secret token** — the subscription's push endpoint is registered with
     a ``?token=...`` query parameter that Pub/Sub echoes on every delivery. We
     compare it in constant time. (Simple, works without extra libraries.)
  2. **OIDC JWT** — Pub/Sub signs an ``Authorization: Bearer <jwt>`` whose
     audience is the push endpoint. Verified via ``google-auth`` when available
     (imported lazily); skipped gracefully if the library is absent.

A dev bypass (no secret configured) accepts unsigned pushes so the replay /
fixture demo path works without GCP — mirroring the Slack connector.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Optional


@dataclass
class PushCheck:
    ok: bool
    reason: str = ""


def verify_pubsub_token(*, configured_token: str, received_token: str) -> PushCheck:
    if not received_token:
        return PushCheck(False, "missing pubsub token")
    if hmac.compare_digest(configured_token, received_token):
        return PushCheck(True)
    return PushCheck(False, "pubsub token mismatch")


def verify_oidc_jwt(*, bearer: str, audience: str,
                    expected_email: Optional[str] = None) -> PushCheck:
    """Verify a Pub/Sub OIDC bearer token (best-effort; needs ``google-auth``)."""
    if not bearer:
        return PushCheck(False, "missing bearer token")
    token = bearer.split(" ", 1)[1] if bearer.lower().startswith("bearer ") else bearer
    try:
        from google.auth.transport import requests as g_requests  # type: ignore
        from google.oauth2 import id_token  # type: ignore
    except ImportError:
        return PushCheck(False, "google-auth not installed")
    try:
        claims = id_token.verify_oauth2_token(token, g_requests.Request(), audience)
    except Exception as exc:  # noqa: BLE001
        return PushCheck(False, f"jwt invalid: {exc}")
    if expected_email and claims.get("email") != expected_email:
        return PushCheck(False, "jwt email mismatch")
    return PushCheck(True)

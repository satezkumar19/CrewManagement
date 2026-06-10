"""Slack request-signature verification.

Slack signs every Events API request with an HMAC-SHA256 over
``v0:{timestamp}:{raw_body}`` keyed by the app's signing secret, sent in the
``X-Slack-Signature`` header (``v0=...``) alongside
``X-Slack-Request-Timestamp``. We recompute it and compare in constant time, and
reject stale timestamps to block replay.

Ref: https://api.slack.com/authentication/verifying-requests-from-slack
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


@dataclass
class SignatureCheck:
    ok: bool
    reason: str = ""


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    replay_window_sec: int = 300,
    now: float | None = None,
) -> SignatureCheck:
    """Return whether ``signature`` is a valid Slack signature for ``body``."""
    if not timestamp or not signature:
        return SignatureCheck(False, "missing signature headers")

    try:
        ts = int(timestamp)
    except ValueError:
        return SignatureCheck(False, "bad timestamp header")

    current = now if now is not None else time.time()
    if abs(current - ts) > replay_window_sec:
        return SignatureCheck(False, "stale timestamp (replay window exceeded)")

    basestring = b"v0:" + str(timestamp).encode() + b":" + body
    digest = hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    expected = f"v0={digest}"

    if not hmac.compare_digest(expected, signature):
        return SignatureCheck(False, "signature mismatch")
    return SignatureCheck(True)

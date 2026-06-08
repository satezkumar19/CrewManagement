"""Gmail connector — Pub/Sub push + history pull, metadata only."""

from .client import GmailClient
from .connector import GmailConnector
from .mappers import is_sign_off, message_metadata_to_record, record_to_signal
from .verify import verify_oidc_jwt, verify_pubsub_token

__all__ = [
    "GmailConnector",
    "GmailClient",
    "record_to_signal",
    "message_metadata_to_record",
    "is_sign_off",
    "verify_pubsub_token",
    "verify_oidc_jwt",
]

"""Gmail connector CLI — ``test`` / ``watch`` / ``backfill``.

    python -m connectors.gmail.cli test     --token <access_token>
    python -m connectors.gmail.cli watch    --token <access_token> --topic projects/p/topics/t
    python -m connectors.gmail.cli backfill  --token <access_token> --query "newer_than:30d" \
        --output-dir ./output

Backfill fetches **metadata only** (From/To/Cc/Subject/Date/thread/labels).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Union

import click

from connectors.common import (
    OutputWriter,
    ScrapeMetrics,
    StructuredLogger,
    load_env,
    resolve_token,
)
from connectors.common.http import HTTPError
from core.signal import SourceSystem

from .auth import DEFAULT_SCOPE, OAuthError, OAuthTokenProvider, obtain_refresh_token
from .client import GmailClient
from .mappers import message_metadata_to_record, record_to_signal

# Pull GMAIL_ACCESS_TOKEN / GMAIL_PUBSUB_TOPIC / L1_TENANT_ID etc. out of .env
# so they need not be passed on the command line.
load_env()


def _credential(token, arn, logger) -> Union[str, Callable[[], str]]:
    """Resolve the Gmail credential, preferring the self-refreshing OAuth flow.

    If ``GMAIL_CLIENT_ID`` + ``GMAIL_CLIENT_SECRET`` + ``GMAIL_REFRESH_TOKEN``
    are all set, return an :class:`OAuthTokenProvider` that mints fresh access
    tokens on demand (so a 7-day ``watch`` keeps being serviced). Otherwise fall
    back to a static access token (``--token`` / ``GMAIL_ACCESS_TOKEN`` / a
    Secrets Manager ARN) — fine for quick tests, but it dies in ~1 hour.
    """
    client_id = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "")
    if client_id and client_secret and refresh_token:
        return OAuthTokenProvider(client_id=client_id, client_secret=client_secret,
                                  refresh_token=refresh_token, logger=logger)
    tok = resolve_token(value=token or "", env_var="GMAIL_ACCESS_TOKEN", secret_arn=arn)
    if not tok:
        raise click.ClickException(
            "no Gmail credentials. For durable per-user access set GMAIL_CLIENT_ID, "
            "GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN (run `gmail authorize` to "
            "mint the refresh token); or pass a short-lived --token / GMAIL_ACCESS_TOKEN.")
    return tok


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """Gmail real connector — metadata-only push + history pull."""


def _common(fn):
    fn = click.option("--token-secret-arn", default=None)(fn)
    fn = click.option("--token", default=None, help="OAuth access token")(fn)
    fn = click.option("--user-id", default="me")(fn)
    return fn


@cli.command()
@_common
def test(token, token_secret_arn, user_id) -> None:
    logger = StructuredLogger(console_output=True)
    client = GmailClient(_credential(token, token_secret_arn, logger),
                         logger, user_id=user_id)
    prof = client.get_profile()
    click.echo(f"Mailbox: {prof.get('emailAddress')}  historyId={prof.get('historyId')}  "
               f"messages={prof.get('messagesTotal')}")


@cli.command()
@click.option("--client-id", default=lambda: os.getenv("GMAIL_CLIENT_ID"),
              help="OAuth client ID (or env GMAIL_CLIENT_ID)")
@click.option("--client-secret", default=lambda: os.getenv("GMAIL_CLIENT_SECRET"),
              help="OAuth client secret (or env GMAIL_CLIENT_SECRET)")
@click.option("--scope", default=DEFAULT_SCOPE, help="OAuth scope to request")
@click.option("--port", default=0, type=int, help="Loopback redirect port (0 = pick free)")
@click.option("--no-browser", is_flag=True, help="Don't auto-open the browser")
def authorize(client_id, client_secret, scope, port, no_browser) -> None:
    """One-time consent: mint a durable refresh token for per-user access.

    Requires an OAuth client of type **Desktop app** (Google Cloud Console →
    Credentials). Opens the consent screen, captures the redirect on localhost,
    and prints a GMAIL_REFRESH_TOKEN line to paste into your .env.
    """
    if not client_id or not client_secret:
        raise click.ClickException(
            "set --client-id/--client-secret (or GMAIL_CLIENT_ID/GMAIL_CLIENT_SECRET)")
    try:
        refresh_token = obtain_refresh_token(
            client_id=client_id, client_secret=client_secret, scope=scope,
            port=port, open_browser=not no_browser, echo=click.echo)
    except OAuthError as e:
        raise click.ClickException(str(e)) from e
    click.echo("\nConsent complete. Add this to your .env (treat it as a secret):\n")
    click.echo(f"GMAIL_CLIENT_ID={client_id}")
    click.echo(f"GMAIL_CLIENT_SECRET={client_secret}")
    click.echo(f"GMAIL_REFRESH_TOKEN={refresh_token}")
    click.echo("\nReminder: if the OAuth consent screen is in 'Testing' mode this "
               "refresh token expires in 7 days — publish the app to 'In production' "
               "for a durable token.")


@cli.command()
@_common
@click.option("--topic", default=lambda: os.getenv("GMAIL_PUBSUB_TOPIC"),
              help="Pub/Sub topic projects/<p>/topics/<t> (or env GMAIL_PUBSUB_TOPIC)")
@click.option("--label", "labels", multiple=True, help="Restrict to label id(s)")
def watch(token, token_secret_arn, user_id, topic, labels) -> None:
    if not topic:
        raise click.ClickException(
            "no Pub/Sub topic (--topic or GMAIL_PUBSUB_TOPIC in .env)")
    logger = StructuredLogger(console_output=True)
    client = GmailClient(_credential(token, token_secret_arn, logger),
                         logger, user_id=user_id)
    try:
        resp = client.watch(topic, list(labels) or None)
    except HTTPError as e:
        detail = ""
        if isinstance(e.body, dict):
            detail = (e.body.get("error", {}) or {}).get("message", "") or str(e.body)
        # The OAuth Playground mints tokens owned by Google's own project, so
        # Gmail rejects any topic not under projects/google.com:oauth-2-playground/*.
        # watch() is impossible with such a token — surface actionable guidance.
        if "oauth-2-playground" in detail or "oauth-2-playground" in topic:
            raise click.ClickException(
                "watch failed: this access token was issued by the Google OAuth 2.0 "
                "Playground, whose tokens belong to Google's own project "
                "(google.com:oauth-2-playground). Gmail requires the Pub/Sub topic to "
                "live in the SAME project that owns the OAuth client, so push "
                "registration cannot work with a Playground token.\n"
                "  Fix: create an OAuth client in your own GCP project (gear icon ⚙ "
                "→ 'Use your own OAuth credentials' in the Playground), enable the "
                "Gmail + Pub/Sub APIs there, create the topic in that project, and pass "
                "--topic projects/<YOUR_PROJECT_ID>/topics/<TOPIC_ID>.\n"
                f"  (Gmail said: {detail or e})"
            ) from e
        raise click.ClickException(f"watch failed ({e}): {detail}") from e
    click.echo(f"watch registered: historyId={resp.get('historyId')} "
               f"expiration={resp.get('expiration')}")


@cli.command()
@_common
@click.option("--tenant-id", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--query", default="newer_than:30d", help="Gmail search query")
@click.option("--output-dir", default="./output")
def backfill(token, token_secret_arn, user_id, tenant_id, query, output_dir) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    client = GmailClient(_credential(token, token_secret_arn, logger), logger, user_id=user_id)
    metrics = ScrapeMetrics()
    writer = OutputWriter(output_dir, source="gmail", entity="emails", logger=logger)
    writer.open()
    try:
        for ref in client.list_messages(query=query):
            meta = client.get_message_metadata(ref.get("id", ""))
            signal = record_to_signal(message_metadata_to_record(meta), tenant_id,
                                      SourceSystem.GMAIL)
            writer.write_event(signal)
            metrics.records_total += 1
            metrics.signals_emitted += 1
    finally:
        writer.close()
    metrics.api_calls_total = client.api_calls
    metrics.api_rate_limit_hits = client.rate_limit_hits
    metrics.finalize()
    writer.write_manifest(SourceSystem.GMAIL.value, metrics.records_total)
    writer.write_metrics(metrics)
    click.echo(f"\nBackfill complete: {metrics.records_total} emails (metadata only), "
               f"{metrics.api_calls_total} API calls")
    logger.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

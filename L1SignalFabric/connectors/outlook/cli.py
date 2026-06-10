"""Outlook connector CLI — ``test`` / ``backfill`` (app-only Graph mail).

    python -m connectors.outlook.cli test \
        --tenant <id> --client-id <id> --client-secret <secret> --mailbox <upn>
    python -m connectors.outlook.cli backfill --output-dir ./output ...

Auth: Microsoft Graph **client-credentials (app-only)** — tenant id + app
(client) id + client secret + the target mailbox UPN. Credentials default from
the MS_* / OUTLOOK_MAILBOX_UPN environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from connectors.common import (
    GraphSubscriptionManager,
    OutputWriter,
    ScrapeMetrics,
    StructuredLogger,
    load_env,
)
from connectors.common.http import HTTPError
from core.signal import SourceSystem

from .client import OutlookClient
from .mappers import message_to_signal

# Pull MS_* / OUTLOOK_* / MS_WEBHOOK_BASE_URL out of .env so they need not be
# passed on the command line (no-op under L1_DISABLE_DOTENV; never clobbers).
load_env()


def _graph_error(exc: HTTPError) -> str:
    """Pull Graph's human message out of an HTTPError body for the CLI."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return (body.get("error", {}) or {}).get("message", "") or str(body)
    return str(exc)


def _auth_opts(fn):
    fn = click.option("--mailbox", envvar="OUTLOOK_MAILBOX_UPN", required=True,
                      help="Target mailbox UPN (app-only)")(fn)
    fn = click.option("--client-secret", envvar="MS_CLIENT_SECRET", required=True)(fn)
    fn = click.option("--client-id", envvar="MS_CLIENT_ID", required=True)(fn)
    fn = click.option("--tenant", envvar="MS_TENANT_ID", required=True,
                      help="Azure AD tenant id")(fn)
    return fn


def _client(tenant, client_id, client_secret, mailbox) -> OutlookClient:
    return OutlookClient(tenant, client_id, client_secret, mailbox)


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """Outlook real connector — Graph mail, app-only (metadata only)."""


@cli.command()
@_auth_opts
def test(tenant, client_id, client_secret, mailbox) -> None:
    client = _client(tenant, client_id, client_secret, mailbox)
    try:
        msgs = client.list_unread(top=5)
        click.echo(f"Graph mail access OK for {mailbox}; sampled {len(msgs)} "
                   f"unread message(s); API calls={client.api_calls}")
    finally:
        client.close()


@cli.command()
@_auth_opts
@click.option("--tenant-id", "l1_tenant", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--output-dir", default="./output")
def backfill(tenant, client_id, client_secret, mailbox, l1_tenant, output_dir) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    client = _client(tenant, client_id, client_secret, mailbox)
    metrics = ScrapeMetrics()
    writer = OutputWriter(output_dir, source="outlook", entity="emails", logger=logger)
    writer.open()
    try:
        for msg in client.list_unread(top=50):
            writer.write_event(message_to_signal(msg, l1_tenant))
            metrics.records_total += 1
            metrics.signals_emitted += 1
    finally:
        writer.close()
        client.close()
    metrics.api_calls_total = client.api_calls
    metrics.api_rate_limit_hits = client.rate_limit_hits
    metrics.finalize()
    writer.write_manifest(SourceSystem.OUTLOOK.value, metrics.records_total)
    writer.write_metrics(metrics)
    click.echo(f"\nBackfill complete: {metrics.records_total} unread emails (metadata only)")
    logger.close()


def _graph_opts(fn):
    fn = click.option("--client-secret", envvar="MS_CLIENT_SECRET", required=True)(fn)
    fn = click.option("--client-id", envvar="MS_CLIENT_ID", required=True)(fn)
    fn = click.option("--tenant", envvar="MS_TENANT_ID", required=True,
                      help="Azure AD tenant id")(fn)
    return fn


@cli.command()
@_graph_opts
@click.option("--mailbox", envvar="OUTLOOK_MAILBOX_UPN", required=True,
              help="Target mailbox UPN")
@click.option("--notification-url", envvar="MS_WEBHOOK_BASE_URL", required=True,
              help="Public HTTPS base (e.g. ngrok host) OR a full URL; "
                   "/outlook/webhook is appended to a bare base")
@click.option("--client-state", envvar="OUTLOOK_CLIENT_STATE", default="",
              help="Secret echoed in each notification + verified by the webhook")
@click.option("--change-type", default="created",
              help="created | updated | deleted (comma-separated)")
@click.option("--minutes", default=4200, type=int,
              help="Subscription lifetime in minutes (Graph caps mail at ~4230)")
def subscribe(tenant, client_id, client_secret, mailbox, notification_url,
              client_state, change_type, minutes) -> None:
    """Register a Graph change subscription so new mail is pushed hands-off.

    The receiving server must already be publicly reachable at the notification
    URL — Graph validates it synchronously when the subscription is created.
    """
    url = notification_url.rstrip("/")
    if not url.endswith("/outlook/webhook"):
        url = f"{url}/outlook/webhook"
    if not client_state:
        click.echo("WARNING: no --client-state / OUTLOOK_CLIENT_STATE — the webhook "
                   "will accept this push only in dev-unverified mode.", err=True)
    mgr = GraphSubscriptionManager(tenant, client_id, client_secret)
    try:
        sub = mgr.create(resource=f"users/{mailbox}/messages",
                         change_type=change_type, notification_url=url,
                         client_state=client_state, minutes=minutes)
    except HTTPError as e:
        raise click.ClickException(f"subscribe failed [{e.status}]: {_graph_error(e)}") from e
    click.echo(f"Outlook subscription created: id={sub.get('id')}")
    click.echo(f"  resource   : {sub.get('resource')}")
    click.echo(f"  notifyUrl  : {sub.get('notificationUrl')}")
    click.echo(f"  expires    : {sub.get('expirationDateTime')}  (renew before this)")


@cli.command(name="subscriptions")
@_graph_opts
def subscriptions(tenant, client_id, client_secret) -> None:
    """List the Graph subscriptions this app currently holds."""
    mgr = GraphSubscriptionManager(tenant, client_id, client_secret)
    subs = mgr.list()
    if not subs:
        click.echo("no active subscriptions")
        return
    for s in subs:
        click.echo(f"{s.get('id')}  {s.get('resource')}  -> {s.get('notificationUrl')}  "
                   f"expires={s.get('expirationDateTime')}")


@cli.command()
@_graph_opts
@click.argument("subscription_id")
@click.option("--minutes", default=4200, type=int)
def renew(tenant, client_id, client_secret, subscription_id, minutes) -> None:
    """Push a subscription's expiry out (run on a schedule before it lapses)."""
    mgr = GraphSubscriptionManager(tenant, client_id, client_secret)
    try:
        sub = mgr.renew(subscription_id, minutes)
    except HTTPError as e:
        raise click.ClickException(f"renew failed [{e.status}]: {_graph_error(e)}") from e
    click.echo(f"renewed {subscription_id}: expires={sub.get('expirationDateTime')}")


@cli.command()
@_graph_opts
@click.argument("subscription_id")
def unsubscribe(tenant, client_id, client_secret, subscription_id) -> None:
    """Delete a Graph subscription."""
    mgr = GraphSubscriptionManager(tenant, client_id, client_secret)
    try:
        mgr.delete(subscription_id)
    except HTTPError as e:
        raise click.ClickException(f"unsubscribe failed [{e.status}]: {_graph_error(e)}") from e
    click.echo(f"deleted {subscription_id}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

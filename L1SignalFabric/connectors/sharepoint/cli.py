"""SharePoint connector CLI — ``test`` / ``backfill`` (app-only Graph).

    python -m connectors.sharepoint.cli test \
        --tenant <id> --client-id <id> --client-secret <secret> \
        --hostname contoso.sharepoint.com --site-path /sites/Crew \
        --folder "Shared Documents/crew"
    python -m connectors.sharepoint.cli backfill --output-dir ./output ...

Auth: Microsoft Graph **client-credentials (app-only)** — tenant id + app
(client) id + client secret + the site hostname/path. Credentials default from
the MS_* / SHAREPOINT_* environment variables.
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

from .client import SharePointClient
from .mappers import folder_item_to_signal

# Pull MS_* / SHAREPOINT_* / MS_WEBHOOK_BASE_URL out of .env so they need not be
# passed on the command line (no-op under L1_DISABLE_DOTENV; never clobbers).
load_env()


def _graph_error(exc: HTTPError) -> str:
    """Pull Graph's human message out of an HTTPError body for the CLI."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return (body.get("error", {}) or {}).get("message", "") or str(body)
    return str(exc)


def _auth_opts(fn):
    fn = click.option("--site-path", envvar="SHAREPOINT_SITE_PATH", required=True,
                      help="e.g. /sites/Crew")(fn)
    fn = click.option("--hostname", envvar="SHAREPOINT_HOSTNAME", required=True,
                      help="e.g. contoso.sharepoint.com")(fn)
    fn = click.option("--client-secret", envvar="MS_CLIENT_SECRET", required=True)(fn)
    fn = click.option("--client-id", envvar="MS_CLIENT_ID", required=True)(fn)
    fn = click.option("--tenant", envvar="MS_TENANT_ID", required=True,
                      help="Azure AD tenant id")(fn)
    return fn


def _client(tenant, client_id, client_secret, hostname, site_path) -> SharePointClient:
    return SharePointClient(tenant, client_id, client_secret, hostname, site_path)


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """SharePoint real connector — Graph folder listing, app-only (metadata only)."""


@cli.command()
@_auth_opts
@click.option("--folder", envvar="SHAREPOINT_FOLDER_PATH", required=True,
              help="Document-library folder path to list")
def test(tenant, client_id, client_secret, hostname, site_path, folder) -> None:
    client = _client(tenant, client_id, client_secret, hostname, site_path)
    try:
        site_id = client.resolve_site_id()
        items = client.list_folder(folder)
        click.echo(f"Site OK: {hostname}{client.site_path} ({site_id})")
        click.echo(f"Folder {folder!r}: {len(items)} item(s); API calls={client.api_calls}")
        for it in items:
            kind = "DIR " if it["is_folder"] else "FILE"
            click.echo(f"  {kind}  {it['name']}")
    finally:
        client.close()


@cli.command()
@_auth_opts
@click.option("--tenant-id", "l1_tenant", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--folder", envvar="SHAREPOINT_FOLDER_PATH", required=True,
              help="Document-library folder path to backfill")
@click.option("--output-dir", default="./output")
def backfill(tenant, client_id, client_secret, hostname, site_path, l1_tenant,
             folder, output_dir) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    client = _client(tenant, client_id, client_secret, hostname, site_path)
    metrics = ScrapeMetrics()
    writer = OutputWriter(output_dir, source="sharepoint", entity="drive_items", logger=logger)
    writer.open()
    try:
        for it in client.list_folder(folder):
            writer.write_event(folder_item_to_signal(
                it, l1_tenant, hostname=hostname, site_path=client.site_path,
                folder_path=folder))
            metrics.records_total += 1
            metrics.signals_emitted += 1
    finally:
        writer.close()
        client.close()
    metrics.api_calls_total = client.api_calls
    metrics.api_rate_limit_hits = client.rate_limit_hits
    metrics.finalize()
    writer.write_manifest(SourceSystem.SHAREPOINT.value, metrics.records_total)
    writer.write_metrics(metrics)
    click.echo(f"\nBackfill complete: {metrics.records_total} items (metadata only)")
    logger.close()


def _graph_opts(fn):
    fn = click.option("--client-secret", envvar="MS_CLIENT_SECRET", required=True)(fn)
    fn = click.option("--client-id", envvar="MS_CLIENT_ID", required=True)(fn)
    fn = click.option("--tenant", envvar="MS_TENANT_ID", required=True,
                      help="Azure AD tenant id")(fn)
    return fn


@cli.command()
@_graph_opts
@click.option("--hostname", envvar="SHAREPOINT_HOSTNAME", required=True)
@click.option("--site-path", envvar="SHAREPOINT_SITE_PATH", required=True)
@click.option("--notification-url", envvar="MS_WEBHOOK_BASE_URL", required=True,
              help="Public HTTPS base (e.g. ngrok host) OR a full URL; "
                   "/sharepoint/webhook is appended to a bare base")
@click.option("--client-state", envvar="SHAREPOINT_CLIENT_STATE", default="",
              help="Secret echoed in each notification + verified by the webhook")
@click.option("--change-type", default="updated",
              help="driveItem supports 'updated' (fires on any change under the drive root)")
@click.option("--minutes", default=4200, type=int,
              help="Subscription lifetime in minutes (Graph caps driveItem at ~4230)")
def subscribe(tenant, client_id, client_secret, hostname, site_path,
              notification_url, client_state, change_type, minutes) -> None:
    """Register a Graph change subscription so site changes are pushed hands-off.

    Subscribes to the site's default document-library drive root; any change in
    the drive fires a notification, which kicks a folder poll. The receiving
    server must already be publicly reachable — Graph validates the URL on create.
    """
    url = notification_url.rstrip("/")
    if not url.endswith("/sharepoint/webhook"):
        url = f"{url}/sharepoint/webhook"
    if not client_state:
        click.echo("WARNING: no --client-state / SHAREPOINT_CLIENT_STATE — the webhook "
                   "will accept this push only in dev-unverified mode.", err=True)
    client = SharePointClient(tenant, client_id, client_secret, hostname, site_path)
    try:
        drive_id = client.resolve_drive_id()
    except Exception as e:  # noqa: BLE001 — surface any site/drive resolution failure
        raise click.ClickException(f"drive resolution failed: {e}") from e
    finally:
        client.close()
    mgr = GraphSubscriptionManager(tenant, client_id, client_secret)
    try:
        sub = mgr.create(resource=f"drives/{drive_id}/root",
                         change_type=change_type, notification_url=url,
                         client_state=client_state, minutes=minutes)
    except HTTPError as e:
        raise click.ClickException(f"subscribe failed [{e.status}]: {_graph_error(e)}") from e
    click.echo(f"SharePoint subscription created: id={sub.get('id')}")
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

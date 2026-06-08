"""SharePoint connector CLI — ``test`` / ``list-drives`` / ``backfill``.

    python -m connectors.sharepoint.cli test --token <graph_token> \
        --hostname contoso.sharepoint.com --site-path /sites/Crew
    python -m connectors.sharepoint.cli backfill --token <graph_token> \
        --drive-id <id> --output-dir ./output

Auth: a Graph access token (--token / SHAREPOINT_ACCESS_TOKEN) or app creds
(--tenant / --client-id / --client-secret).
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from connectors.common import (
    GraphClient,
    OutputWriter,
    ScrapeMetrics,
    StructuredLogger,
    resolve_token,
)
from core.signal import SourceSystem

from .client import SharePointClient
from .mappers import drive_item_to_signal


def _graph(token, arn, tenant, client_id, client_secret, logger) -> GraphClient:
    access = resolve_token(value=token or "", env_var="SHAREPOINT_ACCESS_TOKEN", secret_arn=arn)
    if not access and not (tenant and client_id and client_secret):
        raise click.ClickException("provide --token / SHAREPOINT_ACCESS_TOKEN or app creds")
    return GraphClient(access_token=access, tenant_id=tenant or "", client_id=client_id or "",
                       client_secret=client_secret or "", logger=logger)


def _auth_opts(fn):
    fn = click.option("--client-secret", default=None)(fn)
    fn = click.option("--client-id", default=None)(fn)
    fn = click.option("--tenant", default=None, help="Azure AD tenant id")(fn)
    fn = click.option("--token-secret-arn", default=None)(fn)
    fn = click.option("--token", default=None, help="Graph access token")(fn)
    return fn


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """SharePoint real connector — Graph drives/lists (metadata only)."""


@cli.command()
@_auth_opts
@click.option("--hostname", required=True, help="e.g. contoso.sharepoint.com")
@click.option("--site-path", required=True, help="e.g. /sites/Crew")
def test(token, token_secret_arn, tenant, client_id, client_secret, hostname, site_path) -> None:
    logger = StructuredLogger(console_output=True)
    client = SharePointClient(_graph(token, token_secret_arn, tenant, client_id,
                                     client_secret, logger))
    site = client.get_site(hostname, site_path)
    drives = client.list_site_drives(site["id"])
    click.echo(f"Site: {site.get('displayName')} ({site['id']})")
    click.echo(f"Drives: {len(drives)}")
    for d in drives:
        click.echo(f"  {d['id']}  {d.get('name')}")


@cli.command("list-drives")
@_auth_opts
@click.option("--hostname", required=True)
@click.option("--site-path", required=True)
def list_drives(token, token_secret_arn, tenant, client_id, client_secret, hostname, site_path) -> None:
    logger = StructuredLogger(console_output=True)
    client = SharePointClient(_graph(token, token_secret_arn, tenant, client_id,
                                     client_secret, logger))
    site = client.get_site(hostname, site_path)
    for d in client.list_site_drives(site["id"]):
        click.echo(f"{d['id']}  {d.get('name')}  ({d.get('driveType')})")


@cli.command()
@_auth_opts
@click.option("--tenant-id", "l1_tenant", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--drive-id", required=True, help="Drive id to backfill")
@click.option("--output-dir", default="./output")
def backfill(token, token_secret_arn, tenant, client_id, client_secret, l1_tenant,
             drive_id, output_dir) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    client = SharePointClient(_graph(token, token_secret_arn, tenant, client_id,
                                     client_secret, logger))
    metrics = ScrapeMetrics()
    writer = OutputWriter(output_dir, source="sharepoint", entity="drive_items", logger=logger)
    writer.open()
    try:
        items, _ = client.drive_delta(drive_id)
        for it in items:
            if "root" in it:  # drive-root anchor, not a real item
                continue
            writer.write_event(drive_item_to_signal(it, l1_tenant, drive_id))
            metrics.records_total += 1
            metrics.signals_emitted += 1
    finally:
        writer.close()
    metrics.api_calls_total = client.api_calls
    metrics.api_rate_limit_hits = client.rate_limit_hits
    metrics.finalize()
    writer.write_manifest(SourceSystem.SHAREPOINT.value, metrics.records_total)
    writer.write_metrics(metrics)
    click.echo(f"\nBackfill complete: {metrics.records_total} drive items (metadata only)")
    logger.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

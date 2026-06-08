"""Outlook connector CLI — ``test`` / ``subscribe`` / ``backfill``.

    python -m connectors.outlook.cli test     --token <graph_token>
    python -m connectors.outlook.cli backfill  --token <graph_token> --output-dir ./output

Auth: pass a Graph access token (--token / OUTLOOK_ACCESS_TOKEN) or app creds
(--tenant / --client-id / --client-secret) for the client-credentials grant.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import click

from connectors.common import (
    GraphClient,
    OutputWriter,
    ScrapeMetrics,
    StructuredLogger,
    resolve_token,
)
from core.signal import SourceSystem

from .client import OutlookClient
from .mappers import message_to_signal


def _graph(token, arn, tenant, client_id, client_secret, logger) -> GraphClient:
    access = resolve_token(value=token or "", env_var="OUTLOOK_ACCESS_TOKEN", secret_arn=arn)
    if not access and not (tenant and client_id and client_secret):
        raise click.ClickException("provide --token / OUTLOOK_ACCESS_TOKEN or app creds")
    return GraphClient(access_token=access, tenant_id=tenant or "", client_id=client_id or "",
                       client_secret=client_secret or "", logger=logger)


def _auth_opts(fn):
    fn = click.option("--client-secret", default=None)(fn)
    fn = click.option("--client-id", default=None)(fn)
    fn = click.option("--tenant", default=None, help="Azure AD tenant id")(fn)
    fn = click.option("--token-secret-arn", default=None)(fn)
    fn = click.option("--token", default=None, help="Graph access token")(fn)
    fn = click.option("--mailbox", default="me", help='"me" or a target UPN')(fn)
    return fn


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """Outlook real connector — Graph mail (metadata only)."""


@cli.command()
@_auth_opts
def test(mailbox, token, token_secret_arn, tenant, client_id, client_secret) -> None:
    logger = StructuredLogger(console_output=True)
    client = OutlookClient(_graph(token, token_secret_arn, tenant, client_id,
                                  client_secret, logger), mailbox=mailbox)
    n = sum(1 for _ in zip(range(5), client.list_messages(top=5)))
    click.echo(f"Graph mail access OK; sampled {n} inbox messages; "
               f"API calls={client.api_calls}")


@cli.command()
@_auth_opts
@click.option("--tenant-id", "l1_tenant", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--folder", default="inbox")
@click.option("--output-dir", default="./output")
def backfill(mailbox, token, token_secret_arn, tenant, client_id, client_secret,
             l1_tenant, folder, output_dir) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    client = OutlookClient(_graph(token, token_secret_arn, tenant, client_id,
                                  client_secret, logger), mailbox=mailbox)
    metrics = ScrapeMetrics()
    writer = OutputWriter(output_dir, source="outlook", entity="emails", logger=logger)
    writer.open()
    try:
        for msg in client.list_messages(folder=folder):
            writer.write_event(message_to_signal(msg, l1_tenant))
            metrics.records_total += 1
            metrics.signals_emitted += 1
    finally:
        writer.close()
    metrics.api_calls_total = client.api_calls
    metrics.api_rate_limit_hits = client.rate_limit_hits
    metrics.finalize()
    writer.write_manifest(SourceSystem.OUTLOOK.value, metrics.records_total)
    writer.write_metrics(metrics)
    click.echo(f"\nBackfill complete: {metrics.records_total} emails (metadata only)")
    logger.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

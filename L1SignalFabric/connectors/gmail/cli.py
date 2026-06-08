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
from typing import Optional

import click

from connectors.common import OutputWriter, ScrapeMetrics, StructuredLogger, resolve_token
from core.signal import SourceSystem

from .client import GmailClient
from .mappers import message_metadata_to_record, record_to_signal


def _resolve(token, arn) -> str:
    tok = resolve_token(value=token or "", env_var="GMAIL_ACCESS_TOKEN", secret_arn=arn)
    if not tok:
        raise click.ClickException("no Gmail access token (--token / GMAIL_ACCESS_TOKEN / --token-secret-arn)")
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
    client = GmailClient(_resolve(token, token_secret_arn),
                         StructuredLogger(console_output=True), user_id=user_id)
    prof = client.get_profile()
    click.echo(f"Mailbox: {prof.get('emailAddress')}  historyId={prof.get('historyId')}  "
               f"messages={prof.get('messagesTotal')}")


@cli.command()
@_common
@click.option("--topic", required=True, help="Pub/Sub topic projects/<p>/topics/<t>")
@click.option("--label", "labels", multiple=True, help="Restrict to label id(s)")
def watch(token, token_secret_arn, user_id, topic, labels) -> None:
    client = GmailClient(_resolve(token, token_secret_arn),
                         StructuredLogger(console_output=True), user_id=user_id)
    resp = client.watch(topic, list(labels) or None)
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
    client = GmailClient(_resolve(token, token_secret_arn), logger, user_id=user_id)
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

"""Database connector CLI — ``test`` / ``poll``.

    python -m connectors.database.cli test --url postgresql+psycopg2://u:p@h/db
    python -m connectors.database.cli poll --url <db_url> --mode outbox \
        --table signal_outbox --output-dir ./output

Modes:
  * outbox      — poll a transactional ``signal_outbox`` table (cursor = seq)
  * updated-at  — poll a business table by ``updated_at`` (needs --entity)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click

from connectors.common import OutputWriter, ScrapeMetrics, StructuredLogger
from core.signal import SourceSystem
from core.watermark import FileWatermarkStore

from .adapter import OutboxAdapter, UpdatedAtAdapter
from .connector import DatabaseConnector


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """Database real connector — generic SQL CDC/outbox."""


@cli.command()
@click.option("--url", required=True, help="SQLAlchemy DB URL")
@click.option("--table", default="signal_outbox")
def test(url, table) -> None:
    """Verify connectivity and count rows."""
    from sqlalchemy import create_engine, text
    eng = create_engine(url, future=True)
    with eng.connect() as conn:
        n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    click.echo(f"Connected OK; {table} has {n} rows")


@cli.command()
@click.option("--url", required=True, help="SQLAlchemy DB URL")
@click.option("--mode", type=click.Choice(["outbox", "updated-at"]), default="outbox")
@click.option("--table", default="signal_outbox")
@click.option("--entity", default=None, help="entity name (updated-at mode)")
@click.option("--key-col", default="id")
@click.option("--updated-col", default="updated_at")
@click.option("--tenant-id", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--watermark-path", default=None, help="JSON file to persist cursor")
@click.option("--output-dir", default="./output")
@click.option("--limit", default=None, type=int)
def poll(url, mode, table, entity, key_col, updated_col, tenant_id, watermark_path,
         output_dir, limit) -> None:
    """Poll changes since the watermark and write a Conduit-compatible bundle."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    if mode == "outbox":
        adapter = OutboxAdapter(url=url, table=table, key_field=key_col)
    else:
        if not entity:
            raise click.ClickException("--entity is required in updated-at mode")
        adapter = UpdatedAtAdapter(url=url, table=table, entity=entity,
                                   key_col=key_col, updated_col=updated_col)
    wm = FileWatermarkStore(watermark_path) if watermark_path else None
    connector = DatabaseConnector(tenant_id=tenant_id, adapter=adapter, watermarks=wm)

    metrics = ScrapeMetrics()
    writer = OutputWriter(output_dir, source="database", entity=entity or table, logger=logger)
    writer.open()
    try:
        signals = asyncio.run(connector.poll(limit=limit))
        for s in signals:
            writer.write_event(s)
        metrics.records_total = metrics.signals_emitted = len(signals)
    finally:
        writer.close()
    metrics.finalize()
    writer.write_manifest(SourceSystem.DATABASE.value, metrics.records_total)
    writer.write_metrics(metrics)
    click.echo(f"\nPoll complete: {metrics.records_total} changes (cursor={connector.position()})")
    logger.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

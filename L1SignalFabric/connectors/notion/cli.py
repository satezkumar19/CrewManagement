"""Notion connector CLI — ``test`` / ``list-pages`` / ``scrape``.

    python -m connectors.notion.cli test    --token ntn_...
    python -m connectors.notion.cli list-pages --token ntn_... --type page
    python -m connectors.notion.cli scrape  --token ntn_... --since 2024-01-01 \
        --output-dir ./output
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import click
import yaml

from connectors.common import (
    OutputWriter,
    StructuredLogger,
    parse_timestamp,
    resolve_token,
)

from .client import NotionClient
from .connector import NotionConnector


def _load_yaml(path: Optional[str]) -> dict:
    if path and Path(path).exists():
        return yaml.safe_load(Path(path).read_text()) or {}
    return {}


def _resolve(token, arn, cfg) -> str:
    tok = resolve_token(value=token or cfg.get("token", ""), env_var="NOTION_TOKEN",
                        secret_arn=arn or cfg.get("token_secret_arn"))
    if not tok:
        raise click.ClickException("no Notion token (use --token / NOTION_TOKEN / --token-secret-arn)")
    return tok


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """Notion real connector — pages/databases/blocks."""


def _token_opts(fn):
    fn = click.option("--config", "config_path", default=None, help="YAML config file")(fn)
    fn = click.option("--token-secret-arn", default=None, help="AWS Secrets Manager ARN")(fn)
    fn = click.option("--token", default=None, help="Notion integration token (ntn_...)")(fn)
    return fn


@cli.command()
@_token_opts
def test(token, token_secret_arn, config_path) -> None:
    """Verify API access."""
    cfg = _load_yaml(config_path)
    client = NotionClient(_resolve(token, token_secret_arn, cfg),
                          StructuredLogger(console_output=True))
    info = client.get_self()
    click.echo(f"API access OK; sample results: {info['results_count']}")
    click.echo(f"API calls: {client.api_calls}")


@cli.command("list-pages")
@_token_opts
@click.option("--type", "filter_type", default=None, help='Filter: "page" or "database"')
def list_pages(token, token_secret_arn, config_path, filter_type) -> None:
    """List accessible pages/databases."""
    cfg = _load_yaml(config_path)
    client = NotionClient(_resolve(token, token_secret_arn, cfg))
    n = 0
    click.echo(f"{'Type':<10} {'ID':<36} Title")
    for obj in client.search_all(filter_type=filter_type):
        n += 1
        props = obj.get("properties", {})
        title = "Untitled"
        for p in props.values():
            if p.get("type") == "title" and p.get("title"):
                title = "".join(t.get("plain_text", "") for t in p["title"]) or "Untitled"
                break
        click.echo(f"{obj.get('object', ''):<10} {obj.get('id', ''):<36} {title[:38]}")
    click.echo(f"Total: {n} items")


@cli.command()
@_token_opts
@click.option("--tenant-id", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--since", default=None, help="Only pages edited since (ISO 8601 or epoch)")
@click.option("--rate-limit-delay", default=350, type=int, help="ms between calls")
@click.option("--output-dir", default="./output")
def scrape(token, token_secret_arn, config_path, tenant_id, since, rate_limit_delay,
           output_dir) -> None:
    """Backfill pages + database items to a batch-compatible JSONL bundle."""
    cfg = _load_yaml(config_path)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    client = NotionClient(_resolve(token, token_secret_arn, cfg), logger,
                          rate_limit_delay_ms=rate_limit_delay)
    connector = NotionConnector(tenant_id=tenant_id, client=client, logger=logger)
    writer = OutputWriter(output_dir, source="notion", entity="pages", logger=logger)
    metrics = connector.scrape(writer=writer, since=parse_timestamp(since))
    click.echo(f"\nScrape complete: {metrics.records_total} pages, "
               f"{metrics.extra['databases']['items_total']} db items, "
               f"{metrics.extra['blocks_fetched']} blocks, "
               f"{metrics.api_calls_total} API calls in {metrics.duration_seconds:.1f}s")
    click.echo(f"Output: {writer.jsonl_path}")
    logger.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

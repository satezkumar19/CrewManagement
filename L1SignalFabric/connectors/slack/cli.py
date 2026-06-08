"""Slack connector CLI — ``test`` / ``list-channels`` / ``scrape``.

Parity with the Conduit Slack scraper's CLI, but the ``scrape`` output is the
canonical SignalEvent stream (``slack.jsonl`` + ``manifest.json`` + ``metrics.json``)
so a backfill is wire-identical to the live bus.

    python -m connectors.slack.cli test     --token xoxb-...
    python -m connectors.slack.cli list-channels --token xoxb-...
    python -m connectors.slack.cli scrape   --token xoxb-... --channels all \
        --since 2024-01-01 --output-dir ./output
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

from .backfill import SlackBackfillConfig, SlackBackfillConnector
from .client import SlackClient


def _load_yaml(path: Optional[str]) -> dict:
    if path and Path(path).exists():
        return yaml.safe_load(Path(path).read_text()) or {}
    return {}


def _resolve(token: Optional[str], arn: Optional[str], cfg: dict) -> str:
    tok = resolve_token(value=token or cfg.get("token", ""), env_var="SLACK_TOKEN",
                        secret_arn=arn or cfg.get("token_secret_arn"))
    if not tok:
        raise click.ClickException("no Slack token (use --token / SLACK_TOKEN / --token-secret-arn)")
    return tok


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """Slack real connector — Web API backfill + diagnostics."""


_token_opts = [
    click.option("--token", default=None, help="Slack bot token (xoxb-...)"),
    click.option("--token-secret-arn", default=None, help="AWS Secrets Manager ARN"),
    click.option("--config", "config_path", default=None, help="YAML config file"),
]


def _with_token_opts(fn):
    for opt in reversed(_token_opts):
        fn = opt(fn)
    return fn


@cli.command()
@_with_token_opts
def test(token, token_secret_arn, config_path) -> None:
    """Verify the token and list member channels."""
    cfg = _load_yaml(config_path)
    client = SlackClient(_resolve(token, token_secret_arn, cfg),
                         StructuredLogger(console_output=True))
    info = client.test_auth()
    click.echo(f"Auth OK: team={info['team']} user={info['user']} team_id={info['team_id']}")
    member = [c for c in client.list_channels() if c.is_member]
    click.echo(f"Member channels: {len(member)}")
    for c in member[:20]:
        click.echo(f"  {c.id}  #{c.name}  ({c.num_members} members)")


@cli.command("list-channels")
@_with_token_opts
@click.option("--all", "show_all", is_flag=True, help="Show all channels, not just member")
def list_channels(token, token_secret_arn, config_path, show_all) -> None:
    """List channels visible to the bot."""
    cfg = _load_yaml(config_path)
    client = SlackClient(_resolve(token, token_secret_arn, cfg))
    chans = client.list_channels()
    if not show_all:
        chans = [c for c in chans if c.is_member]
    click.echo(f"{'Channel ID':<14} {'Name':<24} {'Members':>8} Member?")
    for c in chans:
        click.echo(f"{c.id:<14} {('#' + c.name):<24} {c.num_members:>8} {c.is_member}")
    click.echo(f"Total: {len(chans)}")


@cli.command()
@_with_token_opts
@click.option("--tenant-id", default=lambda: os.getenv("L1_TENANT_ID", "maritime-acme"))
@click.option("--channels", default="all", help='"all" or comma-separated channel ids')
@click.option("--since", default=None, help="Start (ISO 8601 or epoch)")
@click.option("--until", default=None, help="End (ISO 8601 or epoch)")
@click.option("--exclude-thread-replies", is_flag=True)
@click.option("--exclude-bots", is_flag=True)
@click.option("--max-replies", default=20, type=int)
@click.option("--rate-limit-delay", default=1200, type=int, help="ms between calls")
@click.option("--output-dir", default="./output")
def scrape(token, token_secret_arn, config_path, tenant_id, channels, since, until,
           exclude_thread_replies, exclude_bots, max_replies, rate_limit_delay,
           output_dir) -> None:
    """Backfill channel history (+threads) to a Conduit-compatible JSONL bundle."""
    cfg = _load_yaml(config_path)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(Path(output_dir) / "scrape.log", console_output=True)
    client = SlackClient(_resolve(token, token_secret_arn, cfg), logger,
                         rate_limit_delay_ms=rate_limit_delay)
    chan_sel = "all" if channels == "all" else [c.strip() for c in channels.split(",")]
    backfill = SlackBackfillConnector(
        tenant_id=tenant_id, client=client, logger=logger,
        config=SlackBackfillConfig(
            channels=chan_sel,
            since_timestamp=parse_timestamp(since),
            until_timestamp=parse_timestamp(until),
            exclude_thread_replies=exclude_thread_replies,
            exclude_bots=exclude_bots,
            max_replies_per_thread=max_replies,
        ),
    )
    writer = OutputWriter(output_dir, source="slack", entity="messages", logger=logger)
    metrics = backfill.scrape(writer=writer)
    click.echo(f"\nScrape complete: {metrics.records_total} messages, "
               f"{metrics.api_calls_total} API calls in {metrics.duration_seconds:.1f}s")
    click.echo(f"Output: {writer.jsonl_path}")
    logger.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

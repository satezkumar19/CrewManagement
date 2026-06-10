"""Token / secret resolution + timestamp parsing — shared by every connector.

Ported (and unified) from the upstream scrapers' ``config.py`` helpers so every
real connector resolves credentials the same way:

    1. an explicit literal value (CLI flag / config file)
    2. an environment variable
    3. an AWS Secrets Manager ARN (``boto3``, imported lazily)

``boto3`` is optional — it is only imported when an ARN is actually resolved, so
connectors import cleanly in environments without AWS libraries.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union


def load_env(path: Optional[Union[str, Path]] = None) -> None:
    """Populate ``os.environ`` from a ``.env`` file (idempotent, never clobbers).

    A ``.env`` file is inert on its own — Python only sees it once something
    loads it. CLIs call this at startup so ``GMAIL_ACCESS_TOKEN`` & friends can
    live in ``.env`` instead of on the command line.

    Uses ``python-dotenv`` when installed (handles quoting, ``export`` prefixes,
    multi-line values); otherwise falls back to a minimal ``KEY=value`` parser
    that strips quotes and ``# inline comments``. Existing environment variables
    always win, so an exported value or a ``--flag`` still overrides ``.env``.

    Set ``L1_DISABLE_DOTENV=1`` to make this a no-op — used by the test suite so
    a developer's local ``.env`` cannot leak real secrets into hermetic tests.
    """
    if os.getenv("L1_DISABLE_DOTENV"):
        return
    env_path = Path(path) if path else Path.cwd() / ".env"
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass

    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip an inline comment only when the value is not quoted.
        if value and value[0] not in "\"'" and " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


# JSON keys the scrapers probed inside a Secrets Manager secret, generalised.
_SECRET_KEYS = ("token", "value", "secret", "api_key", "access_token",
                "slack_token", "notion_token", "SLACK_TOKEN", "NOTION_TOKEN")


def get_secret_value(secret_arn: str) -> str:
    """Fetch a secret string from AWS Secrets Manager (lazy ``boto3`` import).

    Accepts either a raw string secret or a JSON document; for JSON it returns
    the first of :data:`_SECRET_KEYS` present, else the sole value, else the raw
    document. Mirrors the scrapers' ``_get_secret_value``.
    """
    try:
        import boto3  # type: ignore
    except ImportError as exc:  # pragma: no cover - env without boto3
        raise RuntimeError(
            "boto3 is required to resolve a Secrets Manager ARN; "
            "`pip install boto3` or supply the token directly"
        ) from exc

    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_arn)
    raw = resp.get("SecretString", "")
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(doc, dict):
        for key in _SECRET_KEYS:
            if key in doc:
                return str(doc[key])
        if len(doc) == 1:
            return str(next(iter(doc.values())))
    return raw


def resolve_token(
    *,
    value: str = "",
    env_var: Optional[str] = None,
    secret_arn: Optional[str] = None,
) -> str:
    """Resolve a credential by priority: literal → env var → Secrets Manager ARN."""
    if value:
        return value
    if env_var:
        env_val = os.getenv(env_var)
        if env_val:
            return env_val
    if secret_arn:
        return get_secret_value(secret_arn)
    return ""


# Timestamp formats the scrapers accepted, in probe order.
_TS_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def parse_timestamp(value: Union[str, int, float, datetime, None]) -> Optional[datetime]:
    """Parse an ISO-8601 string or Unix epoch into a tz-aware UTC datetime.

    Returns ``None`` for ``None``/empty input. Mirrors the scrapers'
    ``_parse_timestamp`` but always returns timezone-aware UTC (L1 requires it).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:  # ISO with offset, e.g. "...+00:00"
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:  # bare epoch as string
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"unrecognized timestamp: {value!r}") from exc

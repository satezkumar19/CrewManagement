"""Pytest bootstrap — keep the suite hermetic.

Disable ``.env`` auto-loading before anything imports ``config`` (which calls
``load_env()`` at import time). Without this, a developer's local ``.env`` would
populate ``os.environ`` and flip connectors out of dev-mode defaults — e.g. a
real ``SLACK_SIGNING_SECRET`` or ``GMAIL_PUBSUB_TOKEN`` would make the
unsigned-fixture tests start rejecting requests. Tests that want a credential
configured pass an explicit ``Settings(...)`` instead.
"""

import os

os.environ.setdefault("L1_DISABLE_DOTENV", "1")

"""Gmail OAuth 2.0 — refresh-token auth + one-time consent helper.

The Day-2 connector originally took a *static* ``GMAIL_ACCESS_TOKEN`` (a bearer
token minted by the OAuth Playground). That token dies in ~1 hour, but a
``users.watch`` push registration lasts 7 days — so push silently stops being
serviced once the token goes stale. The fix is the standard per-user flow:
store a long-lived **refresh token** and mint short-lived access tokens on
demand.

This module stays consistent with the codebase's "talk to the REST API
directly, no Google client libraries" design (the same way Outlook/SharePoint
hand-roll the Microsoft client-credentials grant). ``requests`` is imported
lazily so the package still imports without it.

Two pieces:

* :class:`OAuthTokenProvider` — a callable that returns a currently-valid access
  token, refreshing transparently ~60 s before expiry. Pass it straight to
  :class:`~connectors.gmail.client.GmailClient`.
* :func:`obtain_refresh_token` — a one-shot loopback (installed-app) flow that
  walks the user through consent and prints a refresh token to paste into
  ``.env``. Backs the ``gmail authorize`` CLI command.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Callable, Optional

from connectors.common import StructuredLogger

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
# Broadest read scope the connector might use; it only ever fetches metadata
# (format=metadata), so the tighter ".../auth/gmail.metadata" also works and is
# preferable for least-privilege — but it forbids body-text search queries.
DEFAULT_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class OAuthError(RuntimeError):
    """OAuth token endpoint returned an error (e.g. invalid_grant)."""


def _post_token(data: dict, *, timeout: float = 30.0) -> dict:
    """POST form-encoded ``data`` to Google's token endpoint, return the JSON.

    Raises :class:`OAuthError` with the provider's ``error`` /
    ``error_description`` on any non-2xx — ``invalid_grant`` here almost always
    means the refresh token was revoked, expired (consent screen still in
    "Testing" → 7-day expiry), or the user changed their password.
    """
    import requests  # lazy: only needed for live calls

    resp = requests.post(GOOGLE_TOKEN_URI, data=data, timeout=timeout)
    try:
        doc = resp.json()
    except ValueError:
        doc = {}
    if resp.status_code >= 400:
        err = doc.get("error", resp.reason or "error")
        desc = doc.get("error_description", "")
        hint = ""
        if err == "invalid_grant":
            hint = (" — the refresh token is no longer valid (revoked, expired, "
                    "or password changed). If your OAuth consent screen is still "
                    "in 'Testing', refresh tokens expire after 7 days; publish the "
                    "app to 'In production', then re-run `gmail authorize`.")
        raise OAuthError(f"token endpoint {resp.status_code}: {err} {desc}{hint}".strip())
    return doc


class OAuthTokenProvider:
    """Callable that yields a valid Gmail access token, refreshing on demand.

    ``provider()`` (or ``provider.token()``) returns the cached access token,
    transparently exchanging the refresh token for a new one once the current
    token is within ``refresh_skew_sec`` of expiry. Designed to be handed to
    ``GmailClient`` / ``RateLimitedClient(auth_provider=...)``.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        logger: Optional[StructuredLogger] = None,
        refresh_skew_sec: float = 60.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        if not (client_id and client_secret and refresh_token):
            raise ValueError("OAuthTokenProvider needs client_id, client_secret and refresh_token")
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self.logger = logger or StructuredLogger(console_output=False)
        self._skew = refresh_skew_sec
        self._now = now
        self._access_token = ""
        self._expires_at = 0.0

    def __call__(self) -> str:
        return self.token()

    def token(self) -> str:
        if not self._access_token or self._now() >= (self._expires_at - self._skew):
            self._refresh()
        return self._access_token

    def _refresh(self) -> None:
        doc = _post_token({
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
        })
        self._access_token = doc.get("access_token", "")
        if not self._access_token:
            raise OAuthError("token endpoint returned no access_token")
        self._expires_at = self._now() + float(doc.get("expires_in", 3600))
        self.logger.info("refreshed Gmail access token",
                         expires_in=int(doc.get("expires_in", 3600)))


def obtain_refresh_token(
    *,
    client_id: str,
    client_secret: str,
    scope: str = DEFAULT_SCOPE,
    port: int = 0,
    open_browser: bool = True,
    echo: Callable[[str], None] = print,
) -> str:
    """Run the installed-app loopback flow and return a durable refresh token.

    Spins up a localhost web server, sends the user to Google's consent page
    with ``access_type=offline&prompt=consent`` (which forces a refresh token to
    be issued), catches the redirect with the authorization ``code``, and
    exchanges it for tokens. The OAuth client must be of type **Desktop app**
    (or have ``http://localhost`` registered as a redirect URI).

    Blocks until the redirect arrives. ``port=0`` picks a free port.
    """
    import http.server
    import threading
    import webbrowser

    captured: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib casing)
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            captured["code"] = (params.get("code") or [""])[0]
            captured["error"] = (params.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = ("Authorization received — you can close this tab and return to "
                   "the terminal." if captured["code"] else
                   f"Authorization failed: {captured['error'] or 'no code returned'}")
            self.wfile.write(f"<html><body><h3>{msg}</h3></body></html>".encode())

        def log_message(self, *_args) -> None:  # silence the default stderr logging
            pass

    # Bind (port=0 picks a free port); the bound port drives the redirect_uri.
    httpd = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    bound_port = httpd.server_address[1]
    redirect_uri = f"http://localhost:{bound_port}/"

    auth_url = GOOGLE_AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
    })

    echo("\nOpen this URL in your browser to authorize (offline access):\n")
    echo(auth_url + "\n")
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:  # pragma: no cover - headless boxes have no browser
            pass

    # Serve exactly one request (the redirect), then stop.
    t = threading.Thread(target=httpd.handle_request)
    t.start()
    t.join()
    httpd.server_close()

    if captured.get("error"):
        raise OAuthError(f"consent denied: {captured['error']}")
    code = captured.get("code")
    if not code:
        raise OAuthError("no authorization code received on the redirect")

    doc = _post_token({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    })
    refresh_token = doc.get("refresh_token", "")
    if not refresh_token:
        raise OAuthError(
            "Google returned no refresh_token. This happens when the account "
            "already granted consent without offline access — revoke the app at "
            "https://myaccount.google.com/permissions and re-run, or ensure "
            "access_type=offline & prompt=consent (this flow sets both).")
    return refresh_token

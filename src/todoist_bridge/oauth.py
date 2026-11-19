"""Loopback OAuth flow that obtains a Google refresh token for the Tasks scope."""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import urllib.parse
import webbrowser

import httpx

from todoist_bridge.google_tasks import SCOPE, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def obtain_refresh_token(client_id: str, client_secret: str) -> str:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    received: dict[str, str] = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"todoist-bridge: authorization received, you can close this tab.")

        def log_message(self, format: str, *args: object) -> None:
            pass

    with http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}"
        url = (
            AUTH_URL
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": SCOPE,
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": state,
                    "code_challenge": challenge.decode().rstrip("="),
                    "code_challenge_method": "S256",
                }
            )
        )
        print(f"Opening the browser. If it does not open, visit:\n{url}\n")
        webbrowser.open(url)
        while "code" not in received and "error" not in received:
            server.handle_request()

    if "error" in received:
        raise SystemExit(f"Google refused the authorization: {received['error']}")
    if received.get("state") != state:
        raise SystemExit("OAuth state mismatch, aborting.")

    response = httpx.post(
        TOKEN_URL,
        data={
            "code": received["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["refresh_token"]

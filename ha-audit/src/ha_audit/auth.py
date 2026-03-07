from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests

APP_DIR = Path.home() / ".config" / "ha-audit"
AUTH_FILE = APP_DIR / "auth.json"


class AuthError(RuntimeError):
    pass


@dataclass(slots=True)
class TokenSet:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int | None = None
    client_id: str | None = None


class TokenStore:
    def __init__(self, path: Path = AUTH_FILE) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def save_all(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def load(self, base_url: str) -> dict[str, Any] | None:
        return self.load_all().get(base_url)

    def save(self, base_url: str, payload: dict[str, Any]) -> None:
        data = self.load_all()
        data[base_url] = payload
        self.save_all(data)

    def delete(self, base_url: str) -> bool:
        data = self.load_all()
        existed = base_url in data
        if existed:
            del data[base_url]
            self.save_all(data)
        return existed


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "HAAuditAuth/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html("Authentication in progress. You can keep this tab open.")
            return

        if parsed.path != "/callback":
            self.send_error(404)
            return

        params = parse_qs(parsed.query)
        self.server.result = {k: v[0] for k, v in params.items()}  # type: ignore[attr-defined]
        self.server.result_event.set()  # type: ignore[attr-defined]
        if "code" in params:
            self._send_html("Authentication complete. You can close this tab.")
        else:
            self._send_html("Authentication failed. Return to the terminal for details.", status=400)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send_html(self, message: str, status: int = 200) -> None:
        body = (
            "<html><body style='font-family: sans-serif; margin: 2rem'>"
            f"<p>{message}</p></body></html>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CallbackServer(HTTPServer):
    def __init__(self, host: str, port: int) -> None:
        super().__init__((host, port), _CallbackHandler)
        self.result: dict[str, str] | None = None
        self.result_event = threading.Event()


def normalize_base_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if not parsed.scheme or not parsed.netloc:
        raise AuthError(f"Invalid Home Assistant URL: {url}")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def start_browser_login(base_url: str, timeout_seconds: int = 180) -> TokenSet:
    server = CallbackServer("127.0.0.1", 0)
    host, port = server.server_address
    client_id = f"http://{host}:{port}"
    redirect_uri = f"{client_id}/callback"
    state = secrets.token_urlsafe(24)
    auth_url = (
        urljoin(base_url + "/", "auth/authorize")
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    opened = webbrowser.open(auth_url, new=1, autoraise=True)
    if not opened:
        print(f"Open this URL in your browser to authenticate:\n{auth_url}")

    if not server.result_event.wait(timeout_seconds):
        server.shutdown()
        raise AuthError("Timed out waiting for the browser authentication callback")

    server.shutdown()
    result = server.result or {}
    if result.get("state") != state:
        raise AuthError("Authentication state mismatch")
    if "error" in result:
        raise AuthError(f"Authentication failed: {result['error']}")
    code = result.get("code")
    if not code:
        raise AuthError("Missing authorization code in callback")

    response = requests.post(
        urljoin(base_url + "/", "auth/token"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise AuthError(f"Token exchange failed: {response.status_code} {response.text}")

    payload = response.json()
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        token_type=payload.get("token_type", "Bearer"),
        expires_in=payload.get("expires_in"),
        client_id=client_id,
    )


def refresh_access_token(base_url: str, refresh_token: str, client_id: str) -> TokenSet:
    response = requests.post(
        urljoin(base_url + "/", "auth/token"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise AuthError(f"Refresh token exchange failed: {response.status_code} {response.text}")
    payload = response.json()
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", refresh_token),
        token_type=payload.get("token_type", "Bearer"),
        expires_in=payload.get("expires_in"),
    )


def get_access_token(base_url: str, force_login: bool = False, store: TokenStore | None = None) -> str:
    normalized = normalize_base_url(base_url)
    store = store or TokenStore()

    if not force_login:
        saved = store.load(normalized)
        if saved:
            try:
                token = refresh_access_token(
                    normalized,
                    saved["refresh_token"],
                    saved["client_id"],
                )
                store.save(
                    normalized,
                    {
                        "refresh_token": token.refresh_token,
                        "client_id": saved["client_id"],
                        "token_type": token.token_type,
                    },
                )
                return token.access_token
            except Exception:
                pass

    token = start_browser_login(normalized)
    store.save(
        normalized,
        {
            "refresh_token": token.refresh_token,
            "client_id": token.client_id,
            "token_type": token.token_type,
        },
    )
    return token.access_token

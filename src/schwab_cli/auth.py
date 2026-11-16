"""Schwab OAuth authentication using macOS Keychain and an ephemeral callback server."""
from __future__ import annotations

import base64
import json
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import conf

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"

_KEYCHAIN_SERVICES = {
    "client_id": conf.KEYCHAIN_SERVICE_CLIENT_ID,
    "client_secret": conf.KEYCHAIN_SERVICE_CLIENT_SECRET,
    "access_token": conf.KEYCHAIN_SERVICE_ACCESS_TOKEN,
    "refresh_token": conf.KEYCHAIN_SERVICE_REFRESH_TOKEN,
    "access_expires_at": conf.KEYCHAIN_SERVICE_ACCESS_EXPIRES_AT,
}


class AuthError(RuntimeError):
    pass


def _keychain(service: str, *, value: str | None = None) -> str | None:
    if value is None:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", service, "-s", conf.KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True,
        )
        return result.stdout.rstrip("\n") if result.returncode == 0 else None
    result = subprocess.run(
        ["security", "add-generic-password", "-U", "-a", service, "-s", conf.KEYCHAIN_ACCOUNT, "-w", value],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise AuthError(f"Unable to write Keychain service {service!r}: {result.stderr.strip()}")
    return None


def get_secret(name: str) -> str | None:
    return _keychain(_KEYCHAIN_SERVICES[name])


def set_secret(name: str, value: str) -> None:
    _keychain(_KEYCHAIN_SERVICES[name], value=value)


def _credentials() -> tuple[str, str]:
    client_id = get_secret("client_id")
    client_secret = get_secret("client_secret")
    if not client_id or not client_secret:
        raise AuthError("Missing Keychain credentials. Store the configured Keychain client_id and client_secret services first.")
    return client_id, client_secret


def _token_request(data: dict[str, str]) -> dict:
    client_id, client_secret = _credentials()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(data).encode(),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=conf.HTTP_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not print token request payloads or credentials.
        raise AuthError(f"Schwab token endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"Schwab token endpoint unavailable: {exc.reason}") from exc
    if not payload.get("access_token"):
        raise AuthError("Schwab token response did not contain an access_token")
    return payload


def _save_tokens(payload: dict, *, prior_refresh: str | None = None) -> None:
    # Schwab may rotate refresh tokens. Preserve the old token only if no new one is returned.
    refresh = payload.get("refresh_token") or prior_refresh
    if refresh:
        set_secret("refresh_token", refresh)
    set_secret("access_token", payload["access_token"])
    expires_in = int(payload.get("expires_in", conf.ACCESS_TOKEN_FALLBACK_SECONDS))
    set_secret("access_expires_at", str(int(time.time()) + expires_in))


def status() -> dict[str, object]:
    expires = get_secret("access_expires_at")
    try:
        seconds = int(expires) - int(time.time()) if expires else None
    except ValueError:
        seconds = None
    return {
        "credentials_configured": bool(get_secret("client_id") and get_secret("client_secret")),
        "access_token_present": bool(get_secret("access_token")),
        "refresh_token_present": bool(get_secret("refresh_token")),
        "access_token_seconds_remaining": seconds,
    }


def refresh_access_token() -> str:
    refresh = get_secret("refresh_token")
    if not refresh:
        raise AuthError("No refresh token in Keychain. Run 'auth login' interactively.")
    payload = _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    _save_tokens(payload, prior_refresh=refresh)
    return payload["access_token"]


def get_access_token(*, allow_interactive: bool = False) -> str:
    token = get_secret("access_token")
    expires = get_secret("access_expires_at")
    try:
        valid = token is not None and expires is not None and int(expires) > time.time() + conf.TOKEN_REFRESH_BUFFER_SECONDS
    except ValueError:
        valid = False
    if valid:
        return token
    try:
        return refresh_access_token()
    except AuthError:
        if not allow_interactive:
            raise
    return login()


def login(*, timeout: int = conf.LOGIN_TIMEOUT_SECONDS) -> str:
    client_id, _ = _credentials()
    state = secrets.token_urlsafe(32)
    outcome: dict[str, str] = {}
    completed = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            params = urllib.parse.parse_qs(parsed.query)
            received_state = params.get("state", [""])[0]
            if not secrets.compare_digest(received_state, state):
                self.send_error(400, "Invalid OAuth state")
                return
            if "error" in params:
                outcome["error"] = params["error"][0]
            elif "code" in params:
                outcome["code"] = params["code"][0]
            else:
                self.send_error(400, "Missing authorization code")
                return
            body = b"Schwab authorization received. You may close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            completed.set()

        def log_message(self, format, *args):
            # OAuth codes are in query strings; never log callback URLs.
            pass

    try:
        server = HTTPServer((conf.CALLBACK_HOST, conf.CALLBACK_PORT), CallbackHandler)
    except OSError as exc:
        raise AuthError(f"Cannot listen on {conf.CALLBACK_HOST}:{conf.CALLBACK_PORT}: {exc}") from exc
    server.timeout = conf.CALLBACK_POLL_SECONDS
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": conf.REDIRECT_URI,
        "response_type": "code",
        "state": state,
    })
    url = f"{AUTHORIZE_URL}?{query}"
    print("Opening Schwab authorization in your browser...")
    print(f"Waiting up to {timeout} seconds for HTTPS callback at {conf.REDIRECT_URI}")
    try:
        webbrowser.open(url)
        deadline = time.monotonic() + timeout
        while not completed.is_set() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not completed.is_set():
        raise AuthError("Timed out waiting for Schwab OAuth callback")
    if "error" in outcome:
        raise AuthError(f"Schwab authorization denied: {outcome['error']}")
    payload = _token_request({
        "grant_type": "authorization_code",
        "code": outcome["code"],
        "redirect_uri": conf.REDIRECT_URI,
    })
    _save_tokens(payload)
    return payload["access_token"]

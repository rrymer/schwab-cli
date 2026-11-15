"""Read-only Schwab Trader API client.

Authentication is delegated to schwab_cli.auth.get_access_token().
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .auth import get_access_token

BASE_URL = "https://api.schwabapi.com/trader/v1"


class SchwabAPIError(RuntimeError):
    """Schwab API request failed."""


def _get(path: str, *, params: dict[str, str] | None = None) -> Any:
    url = BASE_URL + path
    if params:
        url += "?" + urlencode(params)
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {get_access_token()}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        # Do not echo response bodies: they may contain account information.
        raise SchwabAPIError(f"Schwab API returned HTTP {exc.code} for {path}") from exc
    except (URLError, TimeoutError) as exc:
        raise SchwabAPIError(f"Could not reach Schwab API: {exc.reason if isinstance(exc, URLError) else exc}") from exc


def account_numbers() -> str:
    """Return the unique Schwab account hash."""
    result = _get("/accounts/accountNumbers")

    if not isinstance(result, list) or len(result) != 1:
        raise SchwabAPIError(
            "Expected exactly one account from Schwab."
        )

    hash_value = result[0].get("hashValue")

    if not isinstance(hash_value, str) or not hash_value:
        raise SchwabAPIError("Account mapping is missing hashValue")

    return hash_value


def get_positions() -> list[dict[str, Any]]:
    """Retrieve positions for the unique Schwab account."""
    hash_value = account_numbers()

    payload = _get(
        f"/accounts/{quote(hash_value, safe='')}",
        params={"fields": "positions"},
    )

    if not isinstance(payload, dict) or not isinstance(
        payload.get("securitiesAccount"), dict
    ):
        raise SchwabAPIError("Unexpected account positions response")

    return [payload]

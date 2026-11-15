"""Read-only Schwab Market Data API client.

The raw response shapes follow the saved Schwab Market Data Production documentation.
No account hash is needed for market data. All calls use the existing OAuth token.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .api import SchwabAPIError
from .auth import get_access_token

BASE_URL = "https://api.schwabapi.com/marketdata/v1"
PERIODS = {"day": {1, 2, 3, 4, 5, 10},
           "month": {1, 2, 3, 6},
           "year": {1, 2, 3, 5, 10, 15, 20},
           "ytd": {1}}
FREQUENCIES = {"minute": {1, 5, 10, 15, 30},
               "daily": {1}, "weekly": {1}, "monthly": {1}}
PERIOD_FREQUENCIES = {"day": {"minute"}, "month": {"daily", "weekly"},
                      "year": {"daily", "weekly", "monthly"},
                      "ytd": {"daily", "weekly"}}


def _symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A nonempty symbol is required")
    return symbol.strip().upper()


def _request(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = BASE_URL + path + "?" + urlencode(params)
    request = Request(
        url,
        headers={"Authorization": f"Bearer {get_access_token()}",
                 "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise SchwabAPIError(
            f"Schwab Market Data returned HTTP {exc.code} for {path}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise SchwabAPIError(f"Schwab Market Data unavailable: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchwabAPIError(f"Unexpected response structure for {path}")
    return payload


def _epoch_ms(value: datetime | date | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Date must be a datetime, date, or epoch milliseconds")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("Epoch milliseconds must be nonnegative")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Datetime must include a timezone")
        return int(value.timestamp() * 1000)
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day,
                            tzinfo=timezone.utc).timestamp() * 1000)
    raise TypeError("Expected date, timezone-aware datetime, or epoch milliseconds")


def get_price_history(
    symbol: str, *, period_type: str = "year", period: int = 1,
    frequency_type: str = "daily", frequency: int = 1,
    start: datetime | date | int | None = None,
    end: datetime | date | int | None = None,
    extended_hours: bool = False, previous_close: bool = False,
) -> dict[str, Any]:
    """Get raw OHLCV response, including `candles` and `datetime` in epoch ms."""
    symbol = _symbol(symbol)
    if period_type not in PERIODS or period not in PERIODS[period_type]:
        raise ValueError("Invalid period/period_type combination")
    if (frequency_type not in FREQUENCIES
            or frequency not in FREQUENCIES[frequency_type]
            or frequency_type not in PERIOD_FREQUENCIES[period_type]):
        raise ValueError("Invalid frequency/period_type combination")
    start_ms, end_ms = _epoch_ms(start), _epoch_ms(end)
    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise ValueError("start must precede end")
    params: dict[str, Any] = {
        "symbol": symbol, "periodType": period_type, "period": period,
        "frequencyType": frequency_type, "frequency": frequency,
        "needExtendedHoursData": str(extended_hours).lower(),
        "needPreviousClose": str(previous_close).lower(),
    }
    if start_ms is not None:
        params["startDate"] = start_ms
    if end_ms is not None:
        params["endDate"] = end_ms
    payload = _request("/pricehistory", params)
    if not isinstance(payload.get("candles"), list):
        raise SchwabAPIError("Price history response missing candles list")
    return payload


def get_option_expirations(symbol: str) -> list[dict[str, Any]]:
    """Return expirationList entries (date, DTE, expiration type, standard)."""
    payload = _request("/expirationchain", {"symbol": _symbol(symbol)})
    expirations = payload.get("expirationList")
    if not isinstance(expirations, list):
        raise SchwabAPIError("Expiration chain response missing expirationList")
    return expirations


def nearest_expiration(symbol: str, *, include_0dte: bool = False) -> dict[str, Any] | None:
    """Return nearest available expiration, or None. No dates are synthesized."""
    expirations = get_option_expirations(symbol)
    eligible = [x for x in expirations
                if isinstance(x, dict)
                and isinstance(x.get("daysToExpiration"), int)
                and x["daysToExpiration"] >= (0 if include_0dte else 1)
                and isinstance(x.get("expirationDate"), str)]
    return min(eligible, key=lambda x: (x["daysToExpiration"], x["expirationDate"])) if eligible else None


def get_option_chain(
    symbol: str, *, contract_type: str = "ALL",
    from_date: date | str | None = None, to_date: date | str | None = None,
    strike_count: int | None = None, include_underlying_quote: bool = False,
) -> dict[str, Any]:
    """Return Schwab chain payload with callExpDateMap and putExpDateMap."""
    symbol = _symbol(symbol)
    contract_type = contract_type.upper()
    if contract_type not in {"CALL", "PUT", "ALL"}:
        raise ValueError("contract_type must be CALL, PUT, or ALL")
    if strike_count is not None and (type(strike_count) is not int or strike_count < 1):
        raise ValueError("strike_count must be a positive integer")
    params: dict[str, Any] = {
        "symbol": symbol, "contractType": contract_type,
        "includeUnderlyingQuote": str(include_underlying_quote).lower(),
        "strategy": "SINGLE",
    }
    for param, val in (("fromDate", from_date), ("toDate", to_date)):
        if val is not None:
            try:
                params[param] = date.fromisoformat(val).isoformat() if isinstance(val, str) else val.isoformat()
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{param} must be a YYYY-MM-DD date") from exc
    if "fromDate" in params and "toDate" in params and params["fromDate"] > params["toDate"]:
        raise ValueError("from_date must not exceed to_date")
    if strike_count is not None:
        params["strikeCount"] = strike_count
    payload = _request("/chains", params)
    if not any(isinstance(payload.get(key), dict)
               for key in ("callExpDateMap", "putExpDateMap")):
        raise SchwabAPIError("Option chain response missing expiration maps")
    return payload

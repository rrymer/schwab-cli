"""Read-only Schwab market-data endpoints.

Contracts: Schwab Developer Portal, Market Data Production (saved HTML).
Library functions return native API objects; there is no pandas dependency.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import conf
from .api import SchwabAPIError
from .auth import get_access_token

BASE_URL = "https://api.schwabapi.com/marketdata/v1"
PERIODS = {
    "day": (1, 2, 3, 4, 5, 10),
    "month": (1, 2, 3, 6),
    "year": (1, 2, 3, 5, 10, 15, 20),
    "ytd": (1,),
}
FREQUENCIES = {"minute": (1, 5, 10, 15, 30),
               "daily": (1,), "weekly": (1,), "monthly": (1,)}
PERIOD_FREQUENCIES = {
    "day": ("minute",),
    "month": ("daily", "weekly"),
    "year": ("daily", "weekly", "monthly"),
    "ytd": ("daily", "weekly"),
}


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Symbol must be a nonempty string")
    normalized = value.strip().upper()
    # Schwab uses $SPX for the S&P 500 index, not SPX or $SPX.X.
    return "$SPX" if normalized in {"SPX", "$SPX.X"} else normalized


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = BASE_URL + path + "?" + urlencode(params)
    request = Request(
        url,
        headers={"Authorization": f"Bearer {get_access_token()}",
                 "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=conf.HTTP_TIMEOUT_SECONDS) as response:
            result = json.load(response)
    except HTTPError as exc:
        # A Schwab response body may contain account or token information.
        raise SchwabAPIError(
            f"Schwab Market Data returned HTTP {exc.code} for {path}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise SchwabAPIError(f"Schwab Market Data request failed for {path}") from exc
    if not isinstance(result, dict):
        raise SchwabAPIError(f"Unexpected response structure for {path}")
    return result


def _epoch_ms(value: int | date | datetime | None) -> int | None:
    """Convert dates (UTC midnight) or timezone-aware datetimes to epoch ms."""
    if value is None:
        return None
    if type(value) is int:
        if value < 0:
            raise ValueError("Epoch milliseconds must be nonnegative")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must have an explicit timezone")
        return int(value.timestamp() * 1000)
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day,
                            tzinfo=timezone.utc).timestamp() * 1000)
    raise TypeError("Expected an integer epoch ms, date, or timezone-aware datetime")


def get_price_history(
    symbol: str, *, period_type: str = conf.HISTORY_PERIOD_TYPE, period: int = conf.HISTORY_PERIOD,
    frequency_type: str = conf.HISTORY_FREQUENCY_TYPE, frequency: int = conf.HISTORY_FREQUENCY,
    start: int | date | datetime | None = None,
    end: int | date | datetime | None = None,
    extended_hours: bool = False, previous_close: bool = False,
) -> dict[str, Any]:
    """Return raw price-history response, with OHLCV candles in epoch milliseconds."""
    symbol = _symbol(symbol)
    if period_type not in PERIODS or type(period) is not int or period not in PERIODS[period_type]:
        raise ValueError("Invalid period and period_type combination")
    if (frequency_type not in FREQUENCIES
            or type(frequency) is not int
            or frequency not in FREQUENCIES[frequency_type]
            or frequency_type not in PERIOD_FREQUENCIES[period_type]):
        raise ValueError("Invalid frequency and period_type combination")
    start_ms, end_ms = _epoch_ms(start), _epoch_ms(end)
    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise ValueError("start must precede end")
    params: dict[str, Any] = {
        "symbol": symbol, "periodType": period_type, "period": period,
        "frequencyType": frequency_type, "frequency": frequency,
        "needExtendedHoursData": str(bool(extended_hours)).lower(),
        "needPreviousClose": str(bool(previous_close)).lower(),
    }
    if start_ms is not None:
        params["startDate"] = start_ms
    if end_ms is not None:
        params["endDate"] = end_ms
    result = _get("/pricehistory", params)
    if not isinstance(result.get("candles"), list):
        raise SchwabAPIError("Price history response has no candles list")
    return result


def get_option_expirations(symbol: str) -> list[dict[str, Any]]:
    """Return expirationList objects (expirationDate, daysToExpiration, etc.)."""
    result = _get("/expirationchain", {"symbol": _symbol(symbol)})
    expirations = result.get("expirationList")
    if not isinstance(expirations, list):
        raise SchwabAPIError("Expiration chain response has no expirationList")
    return expirations


def nearest_expiration(symbol: str, *, include_0dte: bool = False) -> dict[str, Any] | None:
    """Return the nearest nonexpired expiration or None (0DTE excluded by default)."""
    eligible = [
        item for item in get_option_expirations(symbol)
        if isinstance(item, dict)
        and type(item.get("daysToExpiration")) is int
        and item["daysToExpiration"] >= (0 if include_0dte else 1)
        and isinstance(item.get("expirationDate"), str)
    ]
    return min(eligible, key=lambda x: (x["daysToExpiration"], x["expirationDate"])) if eligible else None


def _iso_date(value: str | date, field: str) -> str:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError(f"{field} must be YYYY-MM-DD") from exc
    if type(value) is date:
        return value.isoformat()
    raise ValueError(f"{field} must be YYYY-MM-DD")


def get_option_chain(
    symbol: str, *, contract_type: str = "ALL",
    from_date: str | date | None = None,
    to_date: str | date | None = None,
    strike_count: int | None = None,
    include_underlying_quote: bool = False,
) -> dict[str, Any]:
    """Return raw Schwab option-chain response, including expiration/strike maps."""
    symbol = _symbol(symbol)
    contract_type = contract_type.upper()
    if contract_type not in ("CALL", "PUT", "ALL"):
        raise ValueError("contract_type must be CALL, PUT, or ALL")
    if strike_count is not None and (type(strike_count) is not int or strike_count < 1):
        raise ValueError("strike_count must be a positive integer")
    params: dict[str, Any] = {
        "symbol": symbol,
        "contractType": contract_type,
        "strategy": "SINGLE",
        "includeUnderlyingQuote": str(bool(include_underlying_quote)).lower(),
    }
    if from_date is not None:
        params["fromDate"] = _iso_date(from_date, "from_date")
    if to_date is not None:
        params["toDate"] = _iso_date(to_date, "to_date")
    if (from_date is not None and to_date is not None
            and params["fromDate"] > params["toDate"]):
        raise ValueError("from_date must not exceed to_date")
    if strike_count is not None:
        params["strikeCount"] = strike_count
    result = _get("/chains", params)
    if not any(isinstance(result.get(k), dict)
               for k in ("callExpDateMap", "putExpDateMap")):
        raise SchwabAPIError("Option chain response has no expiration/strike maps")
    return result


def iter_option_contracts(chain: dict[str, Any]):
    """Yield (side, expiration_key, strike, contract) from Schwab chain maps."""
    for side, map_name in (("CALL", "callExpDateMap"), ("PUT", "putExpDateMap")):
        for expiration, strikes in (chain.get(map_name) or {}).items():
            for strike, contracts in strikes.items():
                if isinstance(contracts, dict):
                    contracts = [contracts]
                if isinstance(contracts, list):
                    for contract in contracts:
                        if isinstance(contract, dict):
                            yield side, expiration, strike, contract

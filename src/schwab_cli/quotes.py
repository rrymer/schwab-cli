"""Read-only batched Schwab quotes for account position displays.

Market Data Production /quotes returns a symbol-keyed mapping containing
quote prices and, for options, reference.underlying and quote.underlyingPrice.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from .market_data import _get as _market_get, _symbol


def get_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch available quote objects in one request; do not infer missing prices."""
    symbols = list(dict.fromkeys(_symbol(x) for x in symbols))
    if not symbols:
        return {}
    result = _market_get("/quotes", {"symbols": ",".join(symbols)})
    return {symbol: value for symbol, value in result.items()
            if isinstance(value, dict)}


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not isfinite(value):
        return None
    return float(value)


def quote_price(quote: dict[str, Any] | None, *, option: bool = False) -> float | None:
    """Option mark first; other instruments last trade first. Never treat 0 as absent."""
    if not isinstance(quote, dict):
        return None
    fields = ("mark", "lastPrice") if option else ("lastPrice", "mark")
    data = quote.get("quote")
    if not isinstance(data, dict):
        return None
    for key in fields:
        price = _numeric(data.get(key))
        if price is not None:
            return price
    return None


def option_underlying(quote: dict[str, Any] | None) -> str | None:
    """Use Schwab option quote reference, not an OCC symbol parser."""
    if not isinstance(quote, dict):
        return None
    reference = quote.get("reference")
    symbol = reference.get("underlying") if isinstance(reference, dict) else None
    return _symbol(symbol) if isinstance(symbol, str) and symbol.strip() else None


def option_underlying_price(quote: dict[str, Any] | None) -> float | None:
    if not isinstance(quote, dict):
        return None
    data = quote.get("quote")
    return _numeric(data.get("underlyingPrice")) if isinstance(data, dict) else None


def prices_for_positions(
    positions: list[dict[str, Any]],
    *,
    quote_loader=None,
) -> dict[str, tuple[float | None, float | None]]:
    """Map instrument keys to (instrument price, underlying price).

    For options, prioritize the explicitly quoted underlying; use the option
    quote's underlyingPrice as a fallback. Missing data stays None.
    """
    from .store import PositionStore

    loader = quote_loader or get_quotes
    instruments = [position.get("instrument") or {} for position in positions]
    symbols = [item["symbol"] for item in instruments]
    quotes = loader(symbols) if symbols else {}
    underlying_symbols = []
    for item in instruments:
        if item.get("assetType", "").upper() == "OPTION":
            option = quotes.get(_symbol(item["symbol"]))
            underlying = option_underlying(option)
            if underlying and underlying not in quotes:
                underlying_symbols.append(underlying)
    if underlying_symbols:
        quotes = {**quotes, **loader(list(dict.fromkeys(underlying_symbols)))}
    prices = {}
    for item in instruments:
        symbol = _symbol(item["symbol"])
        is_option = item.get("assetType", "").upper() == "OPTION"
        quote = quotes.get(symbol)
        price = quote_price(quote, option=is_option)
        underlying_price = None
        if is_option:
            underlying = option_underlying(quote)
            if underlying:
                underlying_price = quote_price(quotes.get(underlying))
            if underlying_price is None:
                underlying_price = option_underlying_price(quote)
        prices[PositionStore.key(item)] = (price, underlying_price)
    return prices

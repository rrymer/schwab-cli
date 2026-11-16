"""Optional technical columns for position dashboards.

The metric symbol is the instrument for equities/collective investments, or
the quoted underlying for options. Request history once per unique symbol.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .analytics import rsi, to_frame
from .api import SchwabAPIError
from .market_data import _symbol, get_price_history
from .quotes import get_quotes, option_underlying
from .store import PositionStore

_EASTERN = ZoneInfo("America/New_York")


def _regular_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only US equity RTH candles, even if the API supplies extra bars."""
    result = []
    for candle in candles:
        timestamp = candle.get("datetime")
        if type(timestamp) is not int:
            continue
        local = datetime.fromtimestamp(timestamp / 1000, tz=_EASTERN)
        minute = local.hour * 60 + local.minute
        if local.weekday() < 5 and 570 <= minute < 960:
            result.append(candle)
    return result


def latest_technicals(
    symbol: str, *, history_loader=None,
) -> dict[str, float | None]:
    """5-minute Wilder RSI(14), session volume, previous ten days' ADV.

    The latest regular session in the minute response is used for volume. The
    last ten complete sessions strictly before that date provide ADV.
    """
    loader = history_loader or get_price_history
    intraday = loader(
        symbol, period_type="day", period=5,
        frequency_type="minute", frequency=5, extended_hours=False,
    ).get("candles") or []
    candles = _regular_candles(intraday)
    if not candles:
        return {"rsi_5m": None, "session_volume": None, "adv_10d": None}
    last_date = datetime.fromtimestamp(candles[-1]["datetime"] / 1000, tz=_EASTERN).date()
    session = [
        bar for bar in candles
        if datetime.fromtimestamp(bar["datetime"] / 1000, tz=_EASTERN).date() == last_date
    ]
    volume = [bar.get("volume") for bar in session]
    current_volume = (float(sum(volume)) if volume and
                      all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in volume)
                      else None)
    try:
        current_rsi = rsi(to_frame(candles), period=14)
    except (ValueError, TypeError):
        current_rsi = None

    daily = loader(
        symbol, period_type="month", period=1,
        frequency_type="daily", frequency=1, extended_hours=False,
    ).get("candles") or []
    previous = []
    for bar in daily:
        timestamp, size = bar.get("datetime"), bar.get("volume")
        if type(timestamp) is not int or type(size) not in (int, float):
            continue
        date = datetime.fromtimestamp(timestamp / 1000, tz=_EASTERN).date()
        if date < last_date and math.isfinite(size) and size >= 0:
            previous.append((date, float(size)))
    # One observation per date, with an explicit minimum sample size.
    previous_by_date = dict(previous)
    sample = [previous_by_date[day] for day in sorted(previous_by_date)[-10:]]
    adv = sum(sample) / 10 if len(sample) == 10 else None
    return {"rsi_5m": current_rsi, "session_volume": current_volume, "adv_10d": adv}


def technicals_for_positions(
    positions: list[dict[str, Any]], *, history_loader=None, quote_loader=None,
) -> dict[str, dict[str, float | None]]:
    """Resolve instruments, deduplicate underlyings, then fetch each once."""
    metrics: dict[str, dict[str, float | None]] = {}
    if not positions:
        return metrics
    load_quotes = quote_loader or get_quotes
    option_symbols = [
        _symbol((pos.get("instrument") or {})["symbol"])
        for pos in positions
        if (pos.get("instrument") or {}).get("assetType", "").upper() == "OPTION"
    ]
    try:
        quotes = load_quotes(option_symbols) if option_symbols else {}
    except (SchwabAPIError, ValueError):
        quotes = {}
    instruments: dict[str, str | None] = {}
    for pos in positions:
        instrument = pos.get("instrument") or {}
        key = PositionStore.key(instrument)
        symbol = _symbol(instrument["symbol"])
        if instrument.get("assetType", "").upper() == "OPTION":
            symbol = option_underlying(quotes.get(symbol))
        instruments[key] = symbol
    cache: dict[str, dict[str, float | None]] = {}
    for key, symbol in instruments.items():
        if symbol is None:
            metrics[key] = {"rsi_5m": None, "session_volume": None, "adv_10d": None}
            continue
        if symbol not in cache:
            try:
                cache[symbol] = latest_technicals(symbol, history_loader=history_loader)
            except (SchwabAPIError, ValueError, TypeError, OverflowError):
                cache[symbol] = {"rsi_5m": None, "session_volume": None, "adv_10d": None}
        metrics[key] = cache[symbol]
    return metrics

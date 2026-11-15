"""Small, pure OHLCV analytics functions with no Schwab or CLI dependencies.

Candles are chronological dictionaries with Schwab's open/high/low/close/volume
and millisecond datetime properties. Indicators return their latest valid value.
Insufficient history raises ValueError instead of fabricating a signal.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def _prices(candles: list[dict[str, Any]], *fields: str) -> list[tuple[float, ...]]:
    if not candles:
        raise ValueError("No candles available")
    result = []
    previous_timestamp = None
    for candle in candles:
        timestamp = candle.get("datetime")
        if type(timestamp) not in (int, float) or not isfinite(timestamp):
            raise ValueError("Candle datetime must be an epoch-millisecond number")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("Candles must have strictly increasing timestamps")
        previous_timestamp = timestamp
        values = []
        for field in fields:
            value = candle.get(field)
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError(f"Candle {field} must be a finite number")
            values.append(float(value))
        result.append(tuple(values))
    return result


def _period(period: int) -> None:
    if type(period) is not int or period < 1:
        raise ValueError("Indicator period must be a positive integer")


def rsi(candles: list[dict[str, Any]], period: int = 14) -> float:
    """Wilder RSI on closes, seeded with simple mean gains/losses.

    A flat series returns 50; all-gain/all-loss series return 100/0.
    Requires period+1 candles for the initial period price changes.
    """
    _period(period)
    closes = [row[0] for row in _prices(candles, "close")]
    if len(closes) <= period:
        raise ValueError(f"RSI({period}) needs at least {period + 1} candles")
    changes = [now - prior for prior, now in zip(closes, closes[1:])]
    gains = [max(0.0, x) for x in changes]
    losses = [max(0.0, -x) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    """Wilder ATR seeded using period true ranges.

    First bar's true range is high-low because no earlier close is supplied.
    Requires at least period candles; for research-quality accuracy, supply
    adequate warmup history before the desired evaluation timestamp.
    """
    _period(period)
    prices = _prices(candles, "high", "low", "close")
    if len(prices) < period:
        raise ValueError(f"ATR({period}) needs at least {period} candles")
    true_ranges = []
    last_close = None
    for high, low, close in prices:
        if high < low or not low <= close <= high:
            raise ValueError("Invalid candle: expected low <= close <= high")
        tr = high - low if last_close is None else max(
            high - low, abs(high - last_close), abs(low - last_close)
        )
        true_ranges.append(tr)
        last_close = close
    average = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        average = (average * (period - 1) + tr) / period
    return average


def session_vwap(candles: list[dict[str, Any]]) -> float:
    """Latest completed regular-session bar VWAP using typical-price approximation.

    Schwab minute OHLCV bars represent volume at a range of prices, so this is
    NOT trade-level VWAP. Session boundaries use US/Eastern, 09:30-16:00.
    Extended-hours bars are excluded. Zero total session volume is an error.
    """
    prices = _prices(candles, "high", "low", "close", "volume")
    sessions = []
    for candle, (high, low, close, volume) in zip(candles, prices):
        if high < low or not low <= close <= high:
            raise ValueError("Invalid candle: expected low <= close <= high")
        if volume < 0:
            raise ValueError("Volume cannot be negative")
        local = datetime.fromtimestamp(candle["datetime"] / 1000, tz=timezone.utc).astimezone(_EASTERN)
        if _OPEN <= local.time() < _CLOSE and local.weekday() < 5:
            sessions.append((local.date(), (high + low + close) / 3.0, volume))
    if not sessions:
        raise ValueError("No regular-session intraday candles available")
    last_session = sessions[-1][0]
    sampled = [(p, v) for date_, p, v in sessions if date_ == last_session]
    total_volume = sum(volume for _, volume in sampled)
    if total_volume <= 0:
        raise ValueError("Latest regular session has no traded volume for VWAP")
    return sum(price * volume for price, volume in sampled) / total_volume

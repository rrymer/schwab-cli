"""Pure technical indicators using pandas frames and NumPy calculations.

Input is a validated, chronologically ordered pandas.DataFrame of Schwab
OHLCV candles with a 'datetime' column in Unix milliseconds. Functions return
the latest valid scalar; API fetching and output formatting belong elsewhere.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_EASTERN = "America/New_York"
_SESSION_OPEN_MINUTE = 9 * 60 + 30
_SESSION_CLOSE_MINUTE = 16 * 60


def to_frame(candles: list[dict] | pd.DataFrame) -> pd.DataFrame:
    """Normalize Schwab candle records into a validated, reusable DataFrame.

    Does not fetch or mutate the original data. The datetime field stays as
    epoch milliseconds for stable machine-readable provenance.
    """
    frame = candles.copy() if isinstance(candles, pd.DataFrame) else pd.DataFrame(candles)
    if frame.empty:
        raise ValueError("No candles available")
    if "datetime" not in frame:
        raise ValueError("Candles need a datetime column")
    try:
        timestamps = pd.to_numeric(frame["datetime"], errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Candle datetime must be epoch milliseconds") from exc
    if not np.all(np.isfinite(timestamps)) or not np.all(timestamps == np.floor(timestamps)):
        raise ValueError("Candle datetime must be finite integer epoch milliseconds")
    if len(timestamps) > 1 and np.any(np.diff(timestamps) <= 0):
        raise ValueError("Candles must have strictly increasing timestamps")
    frame["datetime"] = timestamps.astype(np.int64)
    return frame.reset_index(drop=True)


def _values(frame: pd.DataFrame, *fields: str) -> tuple[np.ndarray, ...]:
    values = []
    for field in fields:
        if field not in frame:
            raise ValueError(f"Missing candle column: {field}")
        try:
            array = pd.to_numeric(frame[field], errors="raise").to_numpy(dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Candle {field} must be numeric") from exc
        if not np.all(np.isfinite(array)):
            raise ValueError(f"Candle {field} must be finite")
        values.append(array)
    return tuple(values)


def _period(period: int) -> None:
    if type(period) is not int or period < 1:
        raise ValueError("Indicator period must be a positive integer")


def _wilder(values: np.ndarray, period: int) -> float:
    """Seed with SMA over the first period observations, then Wilder's RMA."""
    result = float(np.mean(values[:period]))
    for value in values[period:]:
        result += (float(value) - result) / period
    return result


def rsi(frame: pd.DataFrame, period: int = 14) -> float:
    """Wilder RSI of closing prices; flat=50, all-gain=100, all-loss=0."""
    _period(period)
    (close,) = _values(frame, "close")
    if len(close) <= period:
        raise ValueError(f"RSI({period}) needs at least {period + 1} candles")
    change = np.diff(close)
    avg_gain = _wilder(np.maximum(change, 0.0), period)
    avg_loss = _wilder(np.maximum(-change, 0.0), period)
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


def atr(frame: pd.DataFrame, period: int = 14) -> float:
    """Wilder ATR with the first supplied bar's high-low as the initial TR."""
    _period(period)
    high, low, close = _values(frame, "high", "low", "close")
    if len(close) < period:
        raise ValueError(f"ATR({period}) needs at least {period} candles")
    if np.any(high < low) or np.any(close < low) or np.any(close > high):
        raise ValueError("Invalid candle: expected low <= close <= high")
    previous = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum.reduce((high - low, np.abs(high - previous), np.abs(low - previous)))
    return _wilder(tr, period)


def _session_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return last available regular US equity session, DST aware."""
    high, low, close, volume = _values(frame, "high", "low", "close", "volume")
    if np.any(high < low) or np.any(close < low) or np.any(close > high):
        raise ValueError("Invalid candle: expected low <= close <= high")
    if np.any(volume < 0):
        raise ValueError("Volume cannot be negative")
    timestamps = pd.to_datetime(frame["datetime"], unit="ms", utc=True).dt.tz_convert(_EASTERN)
    minutes = timestamps.dt.hour * 60 + timestamps.dt.minute
    regular = (
        (timestamps.dt.dayofweek < 5)
        & (minutes >= _SESSION_OPEN_MINUTE)
        & (minutes < _SESSION_CLOSE_MINUTE)
    )
    eligible = frame.loc[regular].copy()
    if eligible.empty:
        raise ValueError("No regular-session intraday candles available")
    dates = timestamps.loc[regular].dt.date
    return eligible.loc[dates == dates.iloc[-1]]


def session_vwap(frame: pd.DataFrame) -> float:
    """Typical-price minute-bar approximation of VWAP for latest regular session."""
    session = _session_frame(frame)
    high, low, close, volume = _values(session, "high", "low", "close", "volume")
    total = float(np.sum(volume))
    if total <= 0:
        raise ValueError("Latest regular session has no traded volume for VWAP")
    typical_price = (high + low + close) / 3.0
    return float(np.dot(typical_price, volume) / total)


def session_last_timestamp(frame: pd.DataFrame) -> int:
    """Epoch ms of the latest regular-session candle used for VWAP."""
    return int(_session_frame(frame)["datetime"].iloc[-1])

"""Metric registry and Schwab data adapter.

Consumers call calculate_market() and receive a plain dictionary. Indicators
operate only on supplied candles; this adapter owns network retrieval.
No position-specific analytics accepts a ticker in place of a UUID.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import conf
from .analytics import atr, rsi, session_vwap, session_last_timestamp, to_frame
from .field_selection import parse_fields
from .market_data import get_price_history

HistoryLoader = Callable[..., dict[str, Any]]
_METRIC = re.compile(r"(rsi|atr)_([1-9][0-9]*)\Z")


def resolve_metric(name: str) -> tuple[str, int | None]:
    """Resolve an extensible metric name to (source, period).

    Supported: rsi_N, atr_N (N positive integer) and vwap_session.
    Future metrics register here, without changing the CLI parsing scheme.
    """
    if name == "vwap_session":
        return "intraday", None
    match = _METRIC.fullmatch(name)
    if match:
        return "daily", int(match.group(2))
    raise ValueError(f"Unknown analytics metric: {name}")


def calculate_market(
    symbol: str,
    fields: str | tuple[str, ...],
    *,
    history_loader: HistoryLoader | None = None,
) -> dict[str, Any]:
    """Calculate requested latest metrics, fetching each source exactly once.

    The return value is intended for other Python features to consume:
    symbol, metrics (mapping of field names to floats), as_of (epoch ms per
    metric), and sources (input frequency for each metric).
    """
    names = parse_fields(fields) if isinstance(fields, str) else fields
    if not names:
        raise ValueError("At least one metric is required")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate analytics fields")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("Symbol must not be empty")
    resolved = {name: resolve_metric(name) for name in names}
    loader = history_loader or get_price_history
    candles_by_source: dict[str, Any] = {}
    for source, _ in resolved.values():
        if source in candles_by_source:
            continue
        options = (
            {"period_type": conf.ANALYTICS_DAILY_PERIOD_TYPE,
             "period": conf.ANALYTICS_DAILY_PERIOD,
             "frequency_type": conf.HISTORY_FREQUENCY_TYPE,
             "frequency": conf.HISTORY_FREQUENCY}
            if source == "daily" else
            {"period_type": conf.ANALYTICS_INTRADAY_PERIOD_TYPE,
             "period": conf.ANALYTICS_INTRADAY_PERIOD,
             "frequency_type": "minute", "frequency": 1}
        )
        response = loader(normalized, **options)
        candles = response.get("candles")
        if not isinstance(candles, list) or not candles:
            raise ValueError(f"No {source} history available for {normalized}")
        candles_by_source[source] = to_frame(candles)

    values: dict[str, float] = {}
    as_of: dict[str, int] = {}
    sources: dict[str, str] = {}
    for name in names:
        source, period = resolved[name]
        candles = candles_by_source[source]
        if name.startswith("rsi_"):
            value = rsi(candles, period)
        elif name.startswith("atr_"):
            value = atr(candles, period)
        else:
            value = session_vwap(candles)
        values[name] = value
        as_of[name] = (session_last_timestamp(candles)
                       if name == "vwap_session" else int(candles["datetime"].iloc[-1]))
        sources[name] = source
    return {"symbol": normalized, "metrics": values, "as_of": as_of, "sources": sources}

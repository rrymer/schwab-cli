"""Polling-based terminal alerts with a metric registry and edge-triggered rules.

Notifications are emitted as OSC 9 terminal sequences. The terminal emulator,
not Python, decides whether to publish a macOS Notification Center notification.
"""
from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .api import get_positions
from .market_data import _symbol
from .quotes import get_quotes, quote_price
from .store import PositionStore


@dataclass(frozen=True)
class AlertTarget:
    symbol: str | None = None
    position_id: str | None = None

    def __post_init__(self):
        if bool(self.symbol) == bool(self.position_id):
            raise ValueError("Select exactly one of --symbol and --position-id")


def _position_instrument(position_id: str) -> dict[str, Any]:
    """Resolve an active position UUID, never interpreting it as a ticker."""
    accounts = get_positions()
    if len(accounts) != 1:
        raise ValueError("Expected exactly one account snapshot")
    positions = accounts[0]["securitiesAccount"].get("positions") or []
    with PositionStore() as store:
        ids = store.reconcile(positions)
    for position in positions:
        instrument = position.get("instrument") or {}
        if ids[PositionStore.key(instrument)] == position_id:
            return instrument
    raise ValueError(f"Position UUID has no active holding: {position_id}")


def price_metric(target: AlertTarget) -> float | None:
    """Latest quoted price for a symbol or the instrument of an active UUID."""
    instrument = (_position_instrument(target.position_id)
                  if target.position_id else {"symbol": _symbol(target.symbol)})
    symbol = _symbol(instrument["symbol"])
    quotes = get_quotes([symbol])
    entry = quotes.get(symbol)
    is_option = (instrument.get("assetType", "").upper() == "OPTION"
                 or (entry or {}).get("assetMainType") == "OPTION")
    return quote_price(entry, option=is_option)


# Add future indicator evaluators here without changing polling or delivery.
MetricEvaluator = Callable[[AlertTarget], float | None]
METRICS: dict[str, MetricEvaluator] = {"price": price_metric}


@dataclass
class ThresholdRule:
    direction: str
    threshold: float
    active: bool = False

    def __post_init__(self):
        if self.direction not in {"above", "below"}:
            raise ValueError("Alert direction must be above or below")
        if not math.isfinite(self.threshold):
            raise ValueError("Alert threshold must be finite")

    def update(self, value: float | None) -> bool:
        """Fire once on entering the condition; rearm when it becomes false."""
        if value is None:
            return False  # A missing quote must not accidentally reset the rule.
        if not math.isfinite(value):
            raise ValueError("Alert metric must be finite")
        satisfied = (value >= self.threshold if self.direction == "above"
                     else value <= self.threshold)
        fired = satisfied and not self.active
        self.active = satisfied
        return fired


def notify_terminal(message: str, *, stream=None) -> None:
    """Request a terminal-owned system notification using OSC 9."""
    stream = stream or sys.stdout
    # A terminal message is untrusted output; never allow embedded control
    # characters to terminate or forge additional OSC sequences.
    safe = "".join(ch if ch.isprintable() and ch not in "\x1b\x07" else " "
                   for ch in message).strip()
    stream.write(f"\x1b]9;{safe}\x07")
    stream.flush()


def alert(
    *, symbol: str | None = None, position_id: str | None = None,
    metric: str = "price", above: float | None = None,
    below: float | None = None, interval: float = 15.0,
    once: bool = False,
    metric_evaluators: dict[str, MetricEvaluator] | None = None,
    notifier: Callable[[str], None] | None = None,
) -> None:
    """Poll a registered metric and notify when its threshold is first met."""
    target = AlertTarget(symbol=symbol, position_id=position_id)
    if interval <= 0 or not math.isfinite(interval):
        raise ValueError("Alert interval must be positive and finite")
    if (above is None) == (below is None):
        raise ValueError("Specify exactly one of --above and --below")
    threshold = above if above is not None else below
    rule = ThresholdRule("above" if above is not None else "below", threshold)
    evaluators = METRICS if metric_evaluators is None else metric_evaluators
    if metric not in evaluators:
        raise ValueError(f"Unknown alert metric: {metric}")
    evaluator = evaluators[metric]
    publish = notify_terminal if notifier is None else notifier
    label = target.symbol or f"position {target.position_id}"
    try:
        while True:
            value = evaluator(target)
            if rule.update(value):
                publish(f"Schwab alert: {label} {metric} {value:g} "
                        f"{rule.direction} {rule.threshold:g}")
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        return

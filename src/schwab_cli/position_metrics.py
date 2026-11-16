"""Live position-level mark-to-cost P&L for TradePlans and CLI consumers."""
from __future__ import annotations

import json
import math
from typing import Any

from . import conf
from .api import get_positions
from .store import PositionStore


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def unrealized_pnl(position: dict[str, Any]) -> float | None:
    """Position signed market value less signed average-cost basis.

    Values are supplied by Schwab's aggregate position snapshot. This does not
    include realized profit/loss, commissions, fees or intraday trade cashflow.
    Options default to a 100-share multiplier when Schwab does not supply one.
    """
    instrument = position.get("instrument") or {}
    long_qty = _finite_number(position.get("longQuantity"))
    short_qty = _finite_number(position.get("shortQuantity"))
    average = _finite_number(position.get("averagePrice"))
    market_value = _finite_number(position.get("marketValue"))
    if None in (long_qty, short_qty, average, market_value):
        return None
    multiplier = _finite_number(instrument.get("multiplier"))
    if multiplier is None:
        multiplier = (conf.STANDARD_OPTION_MULTIPLIER
                      if instrument.get("assetType", "").upper() == "OPTION" else 1.0)
    if multiplier <= 0 or long_qty < 0 or short_qty < 0 or (long_qty and short_qty):
        return None
    signed_cost = (long_qty - short_qty) * average * multiplier
    return market_value - signed_cost


def calculate_position(position_id: str, metric: str = "pnl") -> dict[str, Any]:
    """Resolve an active UUID, returning a JSON-friendly snapshot metric."""
    if metric != "pnl":
        raise ValueError(f"Unknown position metric: {metric}")
    if not position_id.strip():
        raise ValueError("A position UUID is required")
    accounts = get_positions()
    if len(accounts) != 1:
        raise ValueError("Expected exactly one Schwab account")
    positions = accounts[0]["securitiesAccount"].get("positions") or []
    with PositionStore() as store:
        identities = store.reconcile(positions)
    for position in positions:
        instrument = position.get("instrument") or {}
        if identities[PositionStore.key(instrument)] == position_id:
            value = unrealized_pnl(position)
            if value is None:
                raise ValueError("Required P&L inputs are missing or invalid")
            return {
                "position_id": position_id,
                "symbol": instrument.get("symbol"),
                "metric": metric,
                "value": value,
                "unit": "USD",
                "basis": "unrealized_average_cost",
            }
    raise ValueError(f"No active position found for UUID: {position_id}")


def show_position_metric(position_id: str, metric: str = "pnl") -> None:
    print(json.dumps(calculate_position(position_id, metric), indent=2))

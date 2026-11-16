"""Read-only Schwab account balance selection and compact display."""
from __future__ import annotations

import json
import math
from typing import Any

from .api import get_positions

# Abbreviations are deliberately unique. These fields are distinct in Schwab.
BALANCE_FIELDS = (
    ("C", "cashBalance"),
    ("T", "cashAvailableForTrading"),
    ("A", "availableFunds"),
    ("B", "buyingPower"),
    ("O", "optionBuyingPower"),
    ("D", "dayTradingBuyingPower"),
)


def account_funds(account: dict[str, Any]) -> dict[str, float | None]:
    """Read currentBalances, leaving unavailable fields missing (never zero)."""
    account_data = account.get("securitiesAccount")
    if not isinstance(account_data, dict):
        raise ValueError("Missing securitiesAccount")
    balances = account_data.get("currentBalances")
    if not isinstance(balances, dict):
        balances = {}  # Some account types and fixtures omit balances. Show unavailable, not zero.
    result: dict[str, float | None] = {}
    for _, name in BALANCE_FIELDS:
        value = balances.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            result[name] = None
        elif not math.isfinite(value):
            result[name] = None
        else:
            result[name] = float(value)
    return result


def _thousands(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1000:,.1f}k"


def render_funds(account: dict[str, Any]) -> str:
    """Single line of uppercase single-letter labels and 0.1k USD amounts."""
    values = account_funds(account)
    return "  ".join(f"{letter}:{_thousands(values[name])}"
                     for letter, name in BALANCE_FIELDS)


def show_funds(*, as_json: bool = False) -> None:
    accounts = get_positions()
    if len(accounts) != 1:
        raise ValueError("Expected exactly one Schwab account")
    selected = account_funds(accounts[0])
    print(json.dumps(selected, indent=2) if as_json else render_funds(accounts[0]))

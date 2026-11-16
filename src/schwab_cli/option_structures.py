"""Conservative display-only option structure inference.

Schwab position snapshots are individual instruments, not user-defined strategies.
Never infer a pairing when expirations, quantities, or strikes are ambiguous.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .store import PositionStore

# Schwab OCC-style symbols, e.g. "SPY   261220P00500000".
_OPTION = re.compile(r"^(?P<root>.+?)\s+(?P<date>\d{6})(?P<side>[CP])(?P<strike>\d{8})$")


@dataclass(frozen=True)
class _Leg:
    key: str
    root: str
    expiry: str
    side: str
    strike: int  # integer thousandths: exact strike comparisons
    signed_quantity: float


def _parse(position: dict[str, Any]) -> _Leg | None:
    instrument = position.get("instrument") or {}
    if str(instrument.get("assetType", "")).upper() != "OPTION":
        return None
    symbol = str(instrument.get("symbol") or "")
    match = _OPTION.fullmatch(symbol)
    if not match:
        return None
    try:
        expiry = date.strptime(match["date"], "%y%m%d").isoformat()
    except (ValueError, AttributeError):
        # date.strptime is unavailable in the supported Python 3.11 runtime.
        from datetime import datetime
        try:
            expiry = datetime.strptime(match["date"], "%y%m%d").date().isoformat()
        except ValueError:
            return None
    long_qty, short_qty = position.get("longQuantity", 0), position.get("shortQuantity", 0)
    if (type(long_qty) not in (int, float) or type(short_qty) not in (int, float)
            or long_qty < 0 or short_qty < 0 or bool(long_qty) == bool(short_qty)):
        return None
    return _Leg(
        PositionStore.key(instrument), match["root"].strip().upper(),
        expiry, match["side"], int(match["strike"]), float(long_qty - short_qty),
    )


def _vertical(legs: list[_Leg]) -> str | None:
    if len(legs) != 2 or legs[0].side != legs[1].side:
        return None
    left, right = sorted(legs, key=lambda leg: leg.strike)
    if (left.strike == right.strike or left.signed_quantity != -right.signed_quantity):
        return None
    # At expiration, a long higher-strike call is bullish, and a long
    # higher-strike put is bullish as well (debit put vertical / bear call etc.).
    bullish = right.signed_quantity > 0
    return "BV" if bullish else "CV"


def _iron(legs: list[_Leg]) -> bool:
    if len(legs) != 4:
        return False
    puts = sorted((x for x in legs if x.side == "P"), key=lambda x: x.strike)
    calls = sorted((x for x in legs if x.side == "C"), key=lambda x: x.strike)
    if len(puts) != 2 or len(calls) != 2:
        return False
    p_low, p_high = puts
    c_low, c_high = calls
    if not (p_low.strike < p_high.strike <= c_low.strike < c_high.strike):
        return False  # shared inner strike is an iron fly
    if (p_low.signed_quantity != -p_high.signed_quantity or
            c_low.signed_quantity != -c_high.signed_quantity):
        return False
    # One short/long put vertical paired with the corresponding call vertical.
    return (p_high.signed_quantity < 0 and c_low.signed_quantity < 0 and
            p_low.signed_quantity == c_high.signed_quantity and
            p_high.signed_quantity == c_low.signed_quantity) or (
        p_high.signed_quantity > 0 and c_low.signed_quantity > 0 and
        p_low.signed_quantity == c_high.signed_quantity and
        p_high.signed_quantity == c_low.signed_quantity
    )


def option_codes(positions: list[dict[str, Any]]) -> dict[str, str]:
    """Map instrument keys to C, P, BV, CV or IC; never alter positions."""
    result: dict[str, str] = {}
    grouped: dict[tuple[str, str], list[_Leg]] = {}
    for position in positions:
        instrument = position.get("instrument") or {}
        if str(instrument.get("assetType", "")).upper() != "OPTION":
            continue
        key = PositionStore.key(instrument)
        parsed = _parse(position)
        # Fallback to a simple option type if explicit type metadata is present.
        if parsed:
            result[key] = parsed.side
            grouped.setdefault((parsed.root, parsed.expiry), []).append(parsed)
        else:
            kind = str(instrument.get("putCall") or instrument.get("type") or "").upper()
            result[key] = {"CALL": "C", "PUT": "P", "C": "C", "P": "P"}.get(kind, "O")

    for legs in grouped.values():
        if _iron(legs):
            code = "IC"
        else:
            code = _vertical(legs)
        if code:
            result.update({leg.key: code for leg in legs})
    return result

"""Position display, shared by single-snapshot and continuously refreshed commands."""
from __future__ import annotations

import json
from typing import Any

from .api import get_positions
from .funds import render_funds
from .quotes import prices_for_positions
from .store import PositionStore


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{digits}f}"
    except (ValueError, TypeError):
        return str(value)


def render_positions(
    accounts: list[dict[str, Any]],
    identities: dict[str, str],
    prices: dict[str, tuple[float | None, float | None]],
    *,
    include_ids: bool = False,
    position_id: str | None = None,
) -> str:
    """Render one snapshot without I/O, network access, or persistence."""
    lines = []
    for i, item in enumerate(accounts, 1):
        sec = item["securitiesAccount"]
        positions = sec.get("positions") or []
        if position_id:
            positions = [
                p for p in positions
                if identities[PositionStore.key(p.get("instrument") or {})] == position_id
            ]
        number = str(sec.get("accountNumber", ""))
        label = f"...{number[-4:]}" if number else f"#{i}"
        lines.append(f"Account {label}: {len(positions)} position(s)")
        lines.append(render_funds(item))
        if not positions:
            continue
        headings = ["Symbol", "Asset", "Long", "Short", "Avg trade", "Price", "Underlying", "Market value", "P/L"]
        if include_ids:
            headings.insert(0, "UUID")
        rows = []
        for pos in positions:
            instrument = pos.get("instrument") or {}
            key = PositionStore.key(instrument)
            instrument_price, underlying_price = prices.get(key, (None, None))
            row = [
                str(instrument.get("symbol") or "?"),
                str(instrument.get("assetType") or "?"),
                _fmt(pos.get("longQuantity")),
                _fmt(pos.get("shortQuantity")),
                _fmt(pos.get("averagePrice")),
                _fmt(instrument_price),
                _fmt(underlying_price),
                _fmt(pos.get("marketValue")),
                _fmt(pos.get("currentDayProfitLoss")),
            ]
            if include_ids:
                row.insert(0, identities[key])
            rows.append(row)
        widths = [max(len(h), *(len(str(row[j])) for row in rows)) for j, h in enumerate(headings)]
        lines.append("  ".join(h.ljust(widths[j]) for j, h in enumerate(headings)))
        lines.append("  ".join("-" * width for width in widths))
        lines.extend("  ".join(str(value).ljust(widths[j]) for j, value in enumerate(row)) for row in rows)
    return "\n".join(lines)


def list_positions(
    *, account: str | None = None, as_json: bool = False, include_ids: bool = False,
) -> None:
    accounts = get_positions()
    if len(accounts) != 1:
        raise ValueError("Expected exactly one account snapshot")
    positions = accounts[0]["securitiesAccount"].get("positions") or []
    with PositionStore() as store:
        identities = store.reconcile(positions)
    prices = prices_for_positions(positions)
    if as_json:
        # Do not mutate Schwab's response; include calculated prices in distinct fields.
        payload = json.loads(json.dumps(accounts))
        for pos in payload[0]["securitiesAccount"].get("positions") or []:
            key = PositionStore.key(pos.get("instrument") or {})
            instrument_price, underlying_price = prices.get(key, (None, None))
            pos["currentPrice"] = instrument_price
            if (pos.get("instrument") or {}).get("assetType", "").upper() == "OPTION":
                pos["underlyingPrice"] = underlying_price
            if include_ids:
                pos["localPositionId"] = identities[key]
        print(json.dumps(payload, indent=2))
        return
    print(render_positions(accounts, identities, prices, include_ids=include_ids))

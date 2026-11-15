"""Presentation of Schwab account positions for the CLI."""
from __future__ import annotations

import json
from typing import Any

from .api import get_positions


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{digits}f}"
    except (ValueError, TypeError):
        return str(value)


def list_positions(*, as_json: bool = False) -> None:
    accounts = get_positions()
    if as_json:
        print(json.dumps(accounts, indent=2))
        return

    if not accounts:
        print("No accounts returned by Schwab.")
        return

    for i, item in enumerate(accounts, 1):
        sec = item["securitiesAccount"]
        positions = sec.get("positions") or []
        # Avoid exposing full account identifiers in ordinary terminal output.
        number = str(sec.get("accountNumber", ""))
        label = f"...{number[-4:]}" if number else f"#{i}"
        print(f"Account {label}: {len(positions)} position(s)")
        if not positions:
            continue
        headings = ("Symbol", "Asset", "Long", "Short", "Market value", "P/L")
        rows = []
        for pos in positions:
            instrument = pos.get("instrument") or {}
            rows.append((
                str(instrument.get("symbol") or "?"),
                str(instrument.get("assetType") or "?"),
                _fmt(pos.get("longQuantity")),
                _fmt(pos.get("shortQuantity")),
                _fmt(pos.get("marketValue")),
                _fmt(pos.get("currentDayProfitLoss")),
            ))
        widths = [max(len(str(h)), *(len(str(r[j])) for r in rows)) for j, h in enumerate(headings)]
        print("  ".join(str(h).ljust(widths[j]) for j, h in enumerate(headings)))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print("  ".join(str(value).ljust(widths[j]) for j, value in enumerate(row)))
        print()

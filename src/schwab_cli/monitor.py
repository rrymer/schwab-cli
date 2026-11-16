"""Read-only terminal dashboard for Schwab positions."""
from __future__ import annotations

import sys
import time

from .api import get_positions
from .positions import render_positions
from .quotes import prices_for_positions
from .store import PositionStore


def monitor(
    *, position_id: str | None = None, interval: float = 15.0,
    once: bool = False, include_ids: bool = False, in_place: bool = False,
) -> None:
    if interval <= 0:
        raise ValueError("interval must be greater than zero")
    try:
        while True:
            accounts = get_positions()
            if len(accounts) != 1:
                raise ValueError("Expected exactly one Schwab account")
            positions = accounts[0]["securitiesAccount"].get("positions") or []
            with PositionStore() as store:
                ids = store.reconcile(positions)
                unknown = None
                if position_id and position_id not in ids.values():
                    pending = store.pending(position_id)
                    if pending:
                        unknown = (f'{position_id}: order tracking ID, state={pending["state"]}; '
                                   "no confirmed active position with this UUID")
                    else:
                        unknown = (f"{position_id}: no active position found "
                                   "(closed, unknown, or not yet visible)")
            selected = [
                pos for pos in positions
                if not position_id or ids[PositionStore.key(pos.get("instrument") or {})] == position_id
            ]
            quotes = prices_for_positions(selected)
            display = (unknown or render_positions(
                accounts, ids, quotes, position_id=position_id, include_ids=include_ids
            ))
            # Explicit opt-in: never guess terminal capabilities from isatty().
            # Keep --once useful for scripts and deterministic tests.
            if in_place and not once:
                sys.stdout.write("\x1b[H\x1b[2J")
            print(display, flush=True)
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")

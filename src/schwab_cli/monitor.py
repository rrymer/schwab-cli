"""Read-only CLI monitoring of Schwab position snapshots."""
from __future__ import annotations

import time
from .api import get_positions
from .store import PositionStore


def monitor(*, position_id: str | None = None, interval: float = 15.0, once: bool = False) -> None:
    if interval <= 0:
        raise ValueError('interval must be greater than zero')
    try:
        while True:
            accounts = get_positions()
            if len(accounts) != 1:
                raise ValueError('Expected exactly one Schwab account')
            positions = accounts[0]['securitiesAccount'].get('positions') or []
            with PositionStore() as store:
                ids = store.reconcile(positions)
                if position_id and position_id not in ids.values():
                    pending = store.pending(position_id)
                    if pending:
                        print(f'{position_id}: order tracking ID, state={pending["state"]}; no confirmed active position with this UUID')
                    else:
                        print(f'{position_id}: no active position found (closed, unknown, or not yet visible)')
            for pos in positions:
                instrument = pos.get('instrument') or {}
                id_ = ids[PositionStore.key(instrument)]
                if position_id and id_ != position_id:
                    continue
                print(f'{id_}  {instrument.get("symbol", "?")}  '
                      f'long={pos.get("longQuantity", 0)} short={pos.get("shortQuantity", 0)} '
                      f'value={pos.get("marketValue", "-")} dayP/L={pos.get("currentDayProfitLoss", "-")}')
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print('\nMonitoring stopped.')

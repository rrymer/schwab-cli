"""Local position identity persistence. Replace this adapter to change databases.

IDs refer to Schwab's aggregate instrument position, NOT tax lots, executions,
or multi-leg strategies. A fully closed position loses its ID on later re-entry.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any


def default_db_path() -> Path:
    base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return base / 'schwab-cli' / 'positions.sqlite3'


class PositionStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=15000')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                instrument_key TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS active_instrument
                ON positions(instrument_key) WHERE active=1;
            CREATE TABLE IF NOT EXISTS pending_orders (
                id TEXT PRIMARY KEY,
                instrument_key TEXT NOT NULL,
                schwab_location TEXT,
                state TEXT NOT NULL DEFAULT 'submitted_unknown',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        ''')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @staticmethod
    def key(instrument: dict[str, Any]) -> str:
        symbol = instrument.get('symbol')
        asset = instrument.get('assetType')
        if not isinstance(symbol, str) or not symbol.strip() or not isinstance(asset, str) or not asset.strip():
            raise ValueError('Schwab position missing instrument symbol or assetType')
        return f'{asset.strip().upper()}:{symbol.strip().upper()}'

    def active_id(self, instrument_key: str) -> str | None:
        row = self.db.execute('SELECT id FROM positions WHERE instrument_key=? AND active=1', (instrument_key,)).fetchone()
        return str(row['id']) if row else None

    def reserve_order(self, instrument_key: str) -> str:
        """Return a tracking ID; not proof that a position was opened."""
        id_ = str(uuid.uuid4())
        with self.db:
            self.db.execute('INSERT INTO pending_orders(id,instrument_key) VALUES (?,?)', (id_, instrument_key))
        return id_

    def record_order_location(self, id_: str, location: str | None) -> None:
        with self.db:
            self.db.execute('UPDATE pending_orders SET schwab_location=?,state=? WHERE id=?',
                            (location, 'accepted', id_))

    def reconcile(self, positions: list[dict[str, Any]]) -> dict[str, str]:
        """Map keys to stable UUIDs; retire missing holdings after a successful full snapshot.

        A pending order UUID is promoted only when exactly one pending order matches
        a previously unseen instrument. Ambiguous pending orders get a fresh position
        UUID instead, avoiding arbitrary attribution.
        """
        seen = set()
        ids = {}
        for pos in positions:
            k = self.key(pos.get('instrument') or {})
            if k in seen:
                raise ValueError(f'Duplicate Schwab position instrument: {k}')
            seen.add(k)
        with self.db:
            for k in sorted(seen):
                existing = self.active_id(k)
                if existing:
                    id_ = existing
                else:
                    pending = self.db.execute(
                        "SELECT id FROM pending_orders WHERE instrument_key=? AND state IN ('accepted','submitted_unknown')",
                        (k,)).fetchall()
                    id_ = str(pending[0]['id']) if len(pending) == 1 else str(uuid.uuid4())
                    self.db.execute('INSERT INTO positions(id,instrument_key,last_seen) VALUES (?,?,CURRENT_TIMESTAMP)', (id_, k))
                    if len(pending) == 1:
                        self.db.execute("UPDATE pending_orders SET state='matched' WHERE id=?", (id_,))
                self.db.execute('UPDATE positions SET last_seen=CURRENT_TIMESTAMP WHERE id=?', (id_,))
                ids[k] = id_
            for row in self.db.execute('SELECT id,instrument_key FROM positions WHERE active=1').fetchall():
                if row['instrument_key'] not in seen:
                    self.db.execute('UPDATE positions SET active=0 WHERE id=?', (row['id'],))
        return ids

    def lookup(self, id_: str) -> dict[str, str] | None:
        row = self.db.execute('SELECT id,instrument_key FROM positions WHERE id=? AND active=1', (id_,)).fetchone()
        return dict(row) if row else None

    def pending(self, id_: str) -> dict[str, str] | None:
        row = self.db.execute('SELECT * FROM pending_orders WHERE id=?', (id_,)).fetchone()
        return dict(row) if row else None

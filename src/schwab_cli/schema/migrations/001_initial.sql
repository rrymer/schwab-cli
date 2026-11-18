-- Current schema, version 1. Keep synchronized with migrations.
CREATE TABLE positions (
    id TEXT PRIMARY KEY,
    instrument_key TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT
);
CREATE UNIQUE INDEX active_instrument
    ON positions(instrument_key) WHERE active=1;
CREATE TABLE pending_orders (
    id TEXT PRIMARY KEY,
    instrument_key TEXT NOT NULL,
    schwab_location TEXT,
    state TEXT NOT NULL DEFAULT 'submitted_unknown',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

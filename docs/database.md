# SQLite schema

Current version: **1** (SQLite PRAGMA user_version). The authoritative SQL
is [schema.sql](../src/schwab_cli/schema/schema.sql). Migration 001 creates
the same layout for empty databases or adopts a validated legacy version 0
database without changing its records.

| Table | Column | Type | Meaning |
| --- | --- | --- | --- |
| positions | id | TEXT PRIMARY KEY | Local aggregate position UUID |
| positions | instrument_key | TEXT NOT NULL | Uppercase asset type and symbol, separated by colon |
| positions | active | INTEGER NOT NULL DEFAULT 1 | Active holding flag; retired records remain |
| positions | created_at | TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP | SQLite UTC creation time |
| positions | last_seen | TEXT | UTC last observed position time |
| pending_orders | id | TEXT PRIMARY KEY | Local order tracking UUID |
| pending_orders | instrument_key | TEXT NOT NULL | Instrument key used for reconciliation |
| pending_orders | schwab_location | TEXT | Broker order Location, if supplied |
| pending_orders | state | TEXT NOT NULL DEFAULT 'submitted_unknown' | submitted_unknown, accepted, or matched |
| pending_orders | created_at | TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP | SQLite UTC creation time |

The partial unique index active_instrument on positions(instrument_key)
WHERE active=1 prevents multiple active identities per instrument.
Retired entries remain for history. Position UUIDs represent aggregate holdings,
not individual tax lots or strategies. When exactly one pending order matches
a newly observed instrument, its tracking UUID may become the position UUID.
This is not proof of a particular fill. No foreign-key relationship between
the tables is asserted in version 1.

## Migration policy

Update the static schema and add a numbered migration for every future schema
change. Extend the migration dispatcher and version-specific validation.
Migration SQL executes inside BEGIN IMMEDIATE and updates user_version in
the same transaction; errors roll back both DDL and the version. Never
downgrade an unknown future version. Test migration against a realistic
prior-version database and ensure packaged SQL resources are available.
The validator currently recognizes only the version 1 shape and its legacy
equivalent; version 0 with unrelated tables is rejected.

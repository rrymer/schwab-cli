"""Database lifecycle and relocation regression tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from schwab_cli import conf, database
from schwab_cli.cli import main
from schwab_cli.store import PositionStore
from tests.helpers import position


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.target = self.root / "current.sqlite3"
        self.legacy = self.root / "legacy.sqlite3"
        for name, value in (("DB_PATH", str(self.target)),
                            ("DB_BACKUP_DIR", str(self.root / "backups")),
                            ("CONFIG_PATH", self.root / "config.yaml")):
            p = patch.object(conf, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(database, "legacy_db_path", return_value=self.legacy)
        p.start()
        self.addCleanup(p.stop)

    def legacy_connection(self):
        db = sqlite3.connect(self.legacy)
        db.executescript(database.schema_sql())
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO positions(id,instrument_key) VALUES ('old-id','EQUITY:AAPL')")
        db.execute("INSERT INTO pending_orders(id,instrument_key) VALUES ('pending-id','EQUITY:MSFT')")
        db.commit()
        return db

    def test_init_status_and_idempotency(self):
        self.assertFalse(database.status()["exists"])
        self.assertFalse(self.target.exists())
        self.assertEqual(database.initialize(), 1)
        self.assertEqual(database.initialize(), 1)
        self.assertEqual(database.status()["schema_version"], 1)
        with PositionStore() as store:
            self.assertEqual(store.path, self.target)
            self.assertEqual(store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_legacy_guard_and_relocation_with_wal(self):
        with closing(self.legacy_connection()):
            self.assertTrue(Path(str(self.legacy) + "-wal").exists())
            with self.assertRaisesRegex(database.DatabaseError, "Legacy database"):
                PositionStore()
            self.assertFalse(self.target.exists())
            self.assertEqual(database.backup(self.target, source=self.legacy), self.target)
            self.assertEqual(database.status()["schema_version"], 0)
            with self.assertRaisesRegex(database.DatabaseError, "needs migration"):
                PositionStore()
            self.assertEqual(database.migrate(), 1)
            with PositionStore() as store:
                self.assertEqual(store.active_id("EQUITY:AAPL"), "old-id")
                self.assertEqual(store.pending("pending-id")["instrument_key"], "EQUITY:MSFT")
            with closing(sqlite3.connect(self.legacy)) as original:
                self.assertEqual(original.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_backup_defaults_directory_and_no_overwrite(self):
        with PositionStore() as store:
            original = store.reconcile([position()])["EQUITY:AAPL"]
            target = database.backup()
            self.assertEqual(target.parent, self.root / "backups")
            directory = self.root / "other"
            directory.mkdir()
            second = database.backup(directory)
            self.assertEqual(second.parent, directory)
            with PositionStore(second) as copied:
                self.assertEqual(copied.active_id("EQUITY:AAPL"), original)
            with self.assertRaises(database.DatabaseError):
                database.backup(second)
            with self.assertRaises(database.DatabaseError):
                database.backup(self.target)

    def test_reject_unknown_schema_without_modifying(self):
        with closing(sqlite3.connect(self.target)) as db:
            db.execute("CREATE TABLE unrelated (id INTEGER)")
        with self.assertRaises(database.DatabaseError):
            database.migrate()
        self.assertEqual(database.status()["schema_version"], 0)
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchone()[0], "unrelated")

    def test_future_version_is_not_opened_or_downgraded(self):
        database.initialize()
        with closing(sqlite3.connect(self.target)) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(database.DatabaseError):
            PositionStore()
        with self.assertRaises(database.DatabaseError):
            database.migrate()
        self.assertEqual(database.status()["schema_version"], 99)

    def test_migration_rolls_back_on_validation_failure(self):
        with closing(sqlite3.connect(self.target)):
            pass
        with patch.object(database, "_validate_v1", side_effect=database.DatabaseError("failed")):
            with self.assertRaises(database.DatabaseError):
                database.migrate()
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_concurrent_reconciliation_preserves_identity(self):
        database.initialize()
        def reconcile(_):
            with PositionStore() as store:
                return store.reconcile([position()])["EQUITY:AAPL"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(reconcile, range(8)))
        self.assertEqual(len(set(ids)), 1)

    def test_cli_commands(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["db", "status"]), 0)
            self.assertEqual(main(["db", "init"]), 0)
            self.assertEqual(main(["db", "schema"]), 0)
            self.assertEqual(main(["db", "migrate"]), 0)
            self.assertEqual(main(["db", "backup", str(self.root / "copy.sqlite3")]), 0)
        self.assertIn("CREATE TABLE positions", output.getvalue())
        self.assertTrue((self.root / "copy.sqlite3").exists())

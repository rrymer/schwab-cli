import tempfile
import unittest
import uuid
from pathlib import Path
from schwab_cli.store import PositionStore
from tests.helpers import position


class PositionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'nested' / 'positions.sqlite3'

    def test_uuid_stays_stable_across_quantity_and_restart(self):
        with PositionStore(self.path) as db:
            first = db.reconcile([position(long=1)])['EQUITY:AAPL']
            self.assertEqual(uuid.UUID(first).version, 4)
            self.assertEqual(db.reconcile([position(long=5)])['EQUITY:AAPL'], first)
        with PositionStore(self.path) as db:
            self.assertEqual(db.reconcile([position(long=3)])['EQUITY:AAPL'], first)
            self.assertEqual(db.lookup(first)['instrument_key'], 'EQUITY:AAPL')

    def test_closed_and_reopened_position_gets_new_uuid(self):
        with PositionStore(self.path) as db:
            old = db.reconcile([position()])['EQUITY:AAPL']
            db.reconcile([])
            self.assertIsNone(db.lookup(old))
            new = db.reconcile([position()])['EQUITY:AAPL']
            self.assertNotEqual(old, new)

    def test_one_pending_order_promotes_tracking_uuid(self):
        with PositionStore(self.path) as db:
            tracking = db.reserve_order('EQUITY:AAPL')
            db.record_order_location(tracking, '/orders/1')
            self.assertEqual(db.reconcile([position()])['EQUITY:AAPL'], tracking)
            self.assertEqual(db.pending(tracking)['state'], 'matched')

    def test_ambiguous_pending_orders_do_not_arbitrarily_match(self):
        with PositionStore(self.path) as db:
            p1 = db.reserve_order('EQUITY:AAPL')
            p2 = db.reserve_order('EQUITY:AAPL')
            created = db.reconcile([position()])['EQUITY:AAPL']
            self.assertNotIn(created, (p1, p2))

    def test_reconcile_rejects_duplicate_instruments_without_mutation(self):
        with PositionStore(self.path) as db:
            with self.assertRaises(ValueError):
                db.reconcile([position(), position(long=2)])
            self.assertIsNone(db.active_id('EQUITY:AAPL'))

    def test_invalid_instrument_rejected(self):
        for instrument in ({}, {'symbol': 'AAPL'}, {'assetType': 'EQUITY'}):
            with self.subTest(instrument=instrument), self.assertRaises(ValueError):
                PositionStore.key(instrument)

    def test_distinct_symbols_and_assets_get_distinct_ids(self):
        with PositionStore(self.path) as db:
            ids = db.reconcile([position('AAPL'), position('MSFT'), position('AAPL', asset='OPTION')])
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids.values())), 3)

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from schwab_cli import positions, monitor
from schwab_cli.store import PositionStore
from schwab_cli import store
from tests.helpers import position, snapshot


class PositionPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dbpath = Path(self.tmp.name) / 'positions.sqlite3'
        self.real_store = PositionStore

    def store_factory(self):
        return self.real_store(self.dbpath)

    def test_list_prints_stable_uuid_and_json(self):
        account_data = snapshot(position())
        with patch.object(positions, 'get_positions', return_value=account_data), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), \
             redirect_stdout(io.StringIO()) as output:
            positions.list_positions()
        self.assertIn('UUID', output.getvalue())
        with self.real_store(self.dbpath) as db:
            uid = db.active_id('EQUITY:AAPL')
        self.assertIn(uid, output.getvalue())
        self.assertEqual(account_data[0]['securitiesAccount']['positions'][0]['localPositionId'], uid)

    def test_monitor_once_prints_selected_uuid_without_sleeping(self):
        data = snapshot(position('AAPL'), position('MSFT'))
        with self.real_store(self.dbpath) as db:
            selected = db.reconcile(data[0]['securitiesAccount']['positions'])['EQUITY:AAPL']
        out = io.StringIO()
        with patch.object(monitor, 'get_positions', return_value=data), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), \
             patch.object(monitor.time, 'sleep') as sleep, redirect_stdout(out):
            monitor.monitor(position_id=selected, once=True)
        self.assertIn('AAPL', out.getvalue())
        self.assertNotIn('MSFT', out.getvalue())
        sleep.assert_not_called()

    def test_unknown_uuid_reports_not_found(self):
        out = io.StringIO()
        with patch.object(monitor, 'get_positions', return_value=snapshot(position())), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), redirect_stdout(out):
            monitor.monitor(position_id='missing-id', once=True)
        self.assertIn('no active position found', out.getvalue())

    def test_monitor_rejects_nonpositive_interval(self):
        with self.assertRaises(ValueError):
            monitor.monitor(interval=0, once=True)

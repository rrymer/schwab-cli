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

    def test_list_ids_are_opt_in_and_stable(self):
        data = snapshot(position())
        with patch.object(positions, 'get_positions', return_value=data), \
             patch.object(positions, 'prices_for_positions', return_value={'EQUITY:AAPL': (154.3, None)}), \
             patch.object(store, 'default_db_path', return_value=self.dbpath):
            with redirect_stdout(io.StringIO()) as output:
                positions.list_positions()
            self.assertNotIn('UUID', output.getvalue())
            self.assertIn('154.30', output.getvalue())
            with redirect_stdout(io.StringIO()) as output:
                positions.list_positions(include_ids=True)
            self.assertIn('UUID', output.getvalue())
        with self.real_store(self.dbpath) as db:
            uid = db.active_id('EQUITY:AAPL')
        self.assertIn(uid, output.getvalue())
        self.assertNotIn('localPositionId', data[0]['securitiesAccount']['positions'][0])

    def test_average_trade_price_in_list_and_monitor(self):
        data = snapshot(position())
        data[0]['securitiesAccount']['positions'][0]['averagePrice'] = 148.75
        with patch.object(positions, 'get_positions', return_value=data), \
             patch.object(positions, 'prices_for_positions', return_value={'EQUITY:AAPL': (154.30, None)}), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), \
             redirect_stdout(io.StringIO()) as output:
            positions.list_positions()
        self.assertIn('Avg trade', output.getvalue())
        self.assertIn('148.75', output.getvalue())
        self.assertIn('154.30', output.getvalue())

        with patch.object(monitor, 'get_positions', return_value=data), \
             patch.object(monitor, 'prices_for_positions', return_value={'EQUITY:AAPL': (154.30, None)}), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), \
             redirect_stdout(io.StringIO()) as output:
            monitor.monitor(once=True)
        self.assertIn('Avg trade', output.getvalue())
        self.assertIn('148.75', output.getvalue())

    def test_missing_average_price_displays_dash(self):
        data = snapshot(position())
        with patch.object(positions, 'get_positions', return_value=data), \
             patch.object(positions, 'prices_for_positions', return_value={'EQUITY:AAPL': (154.30, None)}), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), \
             redirect_stdout(io.StringIO()) as output:
            positions.list_positions()
        self.assertIn('Avg trade', output.getvalue())
        self.assertIn('-', output.getvalue())

    def test_list_json_omits_ids_by_default(self):
        import json
        data = snapshot(position())
        with patch.object(positions, 'get_positions', return_value=data), \
             patch.object(positions, 'prices_for_positions', return_value={'EQUITY:AAPL': (154.3, None)}), \
             patch.object(store, 'default_db_path', return_value=self.dbpath):
            with redirect_stdout(io.StringIO()) as output:
                positions.list_positions(as_json=True)
            payload = json.loads(output.getvalue())
            record = payload[0]['securitiesAccount']['positions'][0]
            self.assertEqual(record['currentPrice'], 154.3)
            self.assertNotIn('localPositionId', record)
            with redirect_stdout(io.StringIO()) as output:
                positions.list_positions(as_json=True, include_ids=True)
            record = json.loads(output.getvalue())[0]['securitiesAccount']['positions'][0]
            self.assertIn('localPositionId', record)

    def test_monitor_once_prints_selected_uuid_without_sleeping(self):
        data = snapshot(position('AAPL'), position('MSFT'))
        with self.real_store(self.dbpath) as db:
            selected = db.reconcile(data[0]['securitiesAccount']['positions'])['EQUITY:AAPL']
        out = io.StringIO()
        with patch.object(monitor, 'get_positions', return_value=data), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), \
             patch.object(monitor, 'prices_for_positions', return_value={'EQUITY:AAPL': (150, None)}) as price_load, \
             patch.object(monitor.time, 'sleep') as sleep, redirect_stdout(out):
            monitor.monitor(position_id=selected, once=True)
        self.assertIn('AAPL', out.getvalue())
        self.assertNotIn('MSFT', out.getvalue())
        sleep.assert_not_called()
        self.assertNotIn('UUID', out.getvalue())
        self.assertIn('150.00', out.getvalue())

    def test_unknown_uuid_reports_not_found(self):
        out = io.StringIO()
        with patch.object(monitor, 'get_positions', return_value=snapshot(position())), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), redirect_stdout(out):
            monitor.monitor(position_id='missing-id', once=True)
        self.assertIn('no active position found', out.getvalue())

    def test_monitor_clears_screen_only_on_tty_refresh(self):
        data = snapshot(position())
        fake = io.StringIO()
        fake.isatty = lambda: True
        calls = [None, KeyboardInterrupt()]
        def pause(_):
            outcome = calls.pop(0)
            if outcome:
                raise outcome
        with patch.object(monitor, 'get_positions', return_value=data), \
             patch.object(monitor, 'prices_for_positions', return_value={'EQUITY:AAPL': (150.0, None)}), \
             patch.object(store, 'default_db_path', return_value=self.dbpath), \
             patch.object(monitor.time, 'sleep', side_effect=pause), \
             patch.object(monitor.sys, 'stdout', fake):
            monitor.monitor(interval=1)
        value = fake.getvalue()
        self.assertEqual(value.count('\x1b[H\x1b[2J'), 2)
        self.assertEqual(value.count('AAPL'), 2)

    def test_monitor_rejects_nonpositive_interval(self):
        with self.assertRaises(ValueError):
            monitor.monitor(interval=0, once=True)

"""Optional technicals on positions: synthetic candles and instrument mapping."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from schwab_cli import dashboard_technicals as tech, monitor, positions, store
from schwab_cli.cli import build_parser
from tests.helpers import position, snapshot


def stamp(day, hour, minute=30):
    return int(datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


class DashboardTechnicalTests(unittest.TestCase):
    def test_rsi_volume_and_ten_completed_daily_sessions(self):
        # Nov 16, 2026 regular trading starts at 14:30 UTC.
        minutes = [
            {"datetime": stamp(16, 14 + (30 + i * 5) // 60, (30 + i * 5) % 60),
             "close": 100 + i, "volume": 100 + i}
            for i in range(20)
        ]
        daily = [
            {"datetime": i * 86400000, "volume": 1000 + i * 100}
            for i in range(10)
        ]
        # Old synthetic dates are still earlier than the active session.
        daily.append({"datetime": stamp(16, 21), "volume": 999999})
        requests = []
        def loader(symbol, **kwargs):
            requests.append((symbol, kwargs["frequency_type"], kwargs["frequency"]))
            return {"candles": minutes if kwargs["frequency_type"] == "minute" else daily}

        metrics = tech.latest_technicals("SPY", history_loader=loader)
        self.assertEqual(metrics["rsi_5m"], 100.0)
        self.assertEqual(metrics["session_volume"], sum(100 + i for i in range(20)))
        self.assertAlmostEqual(metrics["adv_10d"], sum(1000 + i * 100 for i in range(10)) / 10)
        self.assertEqual(requests, [("SPY", "minute", 5), ("SPY", "daily", 1)])

    def test_option_underlying_and_shared_symbol_are_fetched_once(self):
        positions_data = [position("SPY", "EQUITY"), position("OPT1", "OPTION"),
                          position("OPT2", "OPTION")]
        quotes = {
            "OPT1": {"reference": {"underlying": "SPY"}},
            "OPT2": {"reference": {"underlying": "SPY"}},
        }
        requested = []
        def history(symbol, **kwargs):
            requested.append(symbol)
            raise ValueError("No bars")
        metrics = tech.technicals_for_positions(
            positions_data, quote_loader=lambda _: quotes, history_loader=history
        )
        self.assertEqual(requested, ["SPY"])
        self.assertEqual(set(metrics), {"EQUITY:SPY", "OPTION:OPT1", "OPTION:OPT2"})
        self.assertTrue(all(v["rsi_5m"] is None for v in metrics.values()))

    def test_no_underlying_does_not_guess_from_option_symbol(self):
        metrics = tech.technicals_for_positions(
            [position("OPT1", "OPTION")],
            quote_loader=lambda _: {"OPT1": {}},
            history_loader=lambda *a, **k: self.fail("No underlying should mean no history request"),
        )
        self.assertIsNone(metrics["OPTION:OPT1"]["adv_10d"])

    def test_render_asset_codes_and_opt_in_columns(self):
        items = snapshot(position("SPY"), position("OPT1", "OPTION"),
                         position("ETF1", "COLLECTIVE_INVESTMENT"))
        ids = {f'{p["instrument"]["assetType"]}:{p["instrument"]["symbol"]}': "uuid"
               for p in items[0]["securitiesAccount"]["positions"]}
        output = positions.render_positions(items, ids, {})
        self.assertNotIn("RSI 5m", output)
        self.assertNotIn("Asset", output)
        self.assertIn("EQUITY", json.dumps(items))  # original API data is untouched
        self.assertIn("Symbol  A", output)
        result = positions.render_positions(
            items, ids, {}, technicals={"EQUITY:SPY": {
                "rsi_5m": 55.25, "session_volume": 100000, "adv_10d": 1200000,
            }}
        )
        self.assertIn("RSI 5m", result)
        self.assertIn("55.2", result)
        self.assertIn("100.0k", result)
        self.assertIn("1.2m", result)

    def test_list_and_monitor_flag_are_opt_in(self):
        parser = build_parser()
        self.assertTrue(parser.parse_args(["positions", "list", "--technicals"]).technicals)
        self.assertTrue(parser.parse_args(["positions", "monitor", "--technicals"]).technicals)
        self.assertFalse(parser.parse_args(["positions", "list"]).technicals)

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "ids.sqlite3"
            data = snapshot(position())
            values = {"EQUITY:AAPL": {"rsi_5m": 50, "session_volume": 1200, "adv_10d": 2000}}
            with patch.object(store, "default_db_path", return_value=db), \
                 patch.object(positions, "get_positions", return_value=data), \
                 patch.object(positions, "prices_for_positions", return_value={}), \
                 patch.object(positions, "technicals_for_positions", return_value=values) as load, \
                 redirect_stdout(io.StringIO()) as out:
                positions.list_positions(show_technicals=True)
            load.assert_called_once()
            self.assertIn("RSI 5m", out.getvalue())

            with patch.object(store, "default_db_path", return_value=db), \
                 patch.object(monitor, "get_positions", return_value=data), \
                 patch.object(monitor, "prices_for_positions", return_value={}), \
                 patch.object(monitor, "technicals_for_positions", return_value=values), \
                 redirect_stdout(io.StringIO()) as out:
                monitor.monitor(once=True, show_technicals=True)
            self.assertIn("ADV 10d", out.getvalue())


if __name__ == "__main__":
    unittest.main()

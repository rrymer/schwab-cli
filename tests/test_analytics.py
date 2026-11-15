"""Small deterministic tests for pure indicators and metric orchestration."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

from schwab_cli import analytics
from schwab_cli.analytics_service import calculate_market, resolve_metric
from schwab_cli.cli import build_parser, dispatch
from schwab_cli import analytics_service
from schwab_cli.store import PositionStore
from tests.helpers import position


def stamp(year, month, day, hour, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def candle(timestamp, close, high=None, low=None, volume=10):
    return {"datetime": timestamp, "close": close,
            "high": close + 1 if high is None else high,
            "low": close - 1 if low is None else low, "volume": volume}


def daily(closes):
    return [candle(i * 86400000, price) for i, price in enumerate(closes)]


class IndicatorTests(unittest.TestCase):
    def test_rsi_wilder_monotonic_and_flat(self):
        self.assertEqual(analytics.rsi(daily([100 + i for i in range(20)])), 100)
        self.assertEqual(analytics.rsi(daily([100 - i for i in range(20)])), 0)
        self.assertEqual(analytics.rsi(daily([100] * 20)), 50)

    def test_rsi_wilder_smoothed_reference(self):
        # RSI(2), first changes +2,-1 -> avg gain=1, avg loss=.5;
        # next change +2 -> avg gain=1.5, avg loss=.25; RSI=85.7142857.
        result = analytics.rsi(daily([10, 12, 11, 13]), period=2)
        self.assertAlmostEqual(result, 100.0 - 100.0 / 7.0)

    def test_atr_true_range_includes_previous_close(self):
        candles = [
            candle(0, 10, high=11, low=9),
            candle(86400000, 20, high=21, low=19),
            candle(172800000, 20, high=22, low=19),
        ]
        # First TR 2; second TR 11; seed 6.5; third TR 3; Wilder -> 4.75
        self.assertAlmostEqual(analytics.atr(candles, 2), 4.75)

    def test_not_enough_candles_and_bad_inputs(self):
        with self.assertRaises(ValueError):
            analytics.rsi(daily([10] * 14), 14)
        with self.assertRaises(ValueError):
            analytics.atr(daily([10]), 14)
        with self.assertRaises(ValueError):
            analytics.rsi(daily([10, 11]), 0)
        with self.assertRaises(ValueError):
            analytics.rsi(list(reversed(daily([10, 11, 12]))), 2)
        with self.assertRaises(ValueError):
            analytics.atr([candle(0, 10, high=9, low=11)], 1)

    def test_session_vwap_resets_excludes_pre_post_and_respects_dst(self):
        # Friday 2026-11-13 and Monday 2026-11-16 use UTC-5.
        bars = [
            candle(stamp(2026, 11, 13, 14, 30), 80, volume=100),
            candle(stamp(2026, 11, 16, 14, 0), 500, volume=900),  # 09:00 ET, excluded
            candle(stamp(2026, 11, 16, 14, 30), 100, volume=10),
            candle(stamp(2026, 11, 16, 15, 0), 110, volume=30),
            candle(stamp(2026, 11, 16, 21, 30), 900, volume=900),  # after-hours excluded
        ]
        self.assertAlmostEqual(analytics.session_vwap(bars), 107.5)
        self.assertEqual(analytics.session_last_timestamp(bars), bars[3]["datetime"])

    def test_session_vwap_daylight_saving_market_open(self):
        # July open is 13:30 UTC (09:30 EDT); 13:00 UTC is premarket.
        bars = [
            candle(stamp(2026, 7, 6, 13, 0), 900, volume=100),
            candle(stamp(2026, 7, 6, 13, 30), 100, volume=10),
            candle(stamp(2026, 7, 6, 14, 0), 110, volume=10),
        ]
        self.assertAlmostEqual(analytics.session_vwap(bars), 105)

    def test_session_vwap_zero_volume_rejected(self):
        with self.assertRaisesRegex(ValueError, "volume"):
            analytics.session_vwap([candle(stamp(2026, 11, 16, 14, 30), 100, volume=0)])


class ServiceTests(unittest.TestCase):
    def test_registry_rejects_unsupported_metric(self):
        self.assertEqual(resolve_metric("rsi_14"), ("daily", 14))
        self.assertEqual(resolve_metric("atr_20"), ("daily", 20))
        self.assertEqual(resolve_metric("vwap_session"), ("intraday", None))
        with self.assertRaises(ValueError):
            resolve_metric("probability_itm")

    def test_fetch_once_per_source_and_return_structured_results(self):
        market_data = daily([100 + i for i in range(40)])
        intraday = [
            candle(stamp(2026, 11, 16, 14, 30), 100, volume=10),
            candle(stamp(2026, 11, 16, 15, 30), 110, volume=30),
        ]
        requests = []
        def loader(symbol, **kwargs):
            requests.append((symbol, kwargs["frequency_type"]))
            return {"candles": market_data if kwargs["frequency_type"] == "daily" else intraday}
        output = calculate_market("spy", "rsi_14,atr_14,vwap_session", history_loader=loader)
        self.assertEqual(requests, [("SPY", "daily"), ("SPY", "minute")])
        self.assertEqual(output["symbol"], "SPY")
        self.assertEqual(list(output["metrics"]), ["rsi_14", "atr_14", "vwap_session"])
        self.assertEqual(output["metrics"]["rsi_14"], 100)
        self.assertAlmostEqual(output["metrics"]["atr_14"], 2)
        self.assertAlmostEqual(output["metrics"]["vwap_session"], 107.5)
        self.assertEqual(output["as_of"]["vwap_session"], intraday[1]["datetime"])
        self.assertEqual(output["sources"]["rsi_14"], "daily")

    def test_unknown_metric_fails_without_fetching(self):
        requests = []
        with self.assertRaisesRegex(ValueError, "Unknown analytics"):
            calculate_market("SPY", "rsi_14,not_a_metric", history_loader=lambda *args, **kwargs: requests.append(1))
        self.assertEqual(requests, [])

    def test_cli_json_is_programmatic_result(self):
        result = {"symbol": "SPY", "metrics": {"rsi_14": 45.0}, "as_of": {"rsi_14": 1000}, "sources": {"rsi_14": "daily"}}
        out = io.StringIO()
        with patch.object(analytics_service, "calculate_market", return_value=result), redirect_stdout(out):
            args = build_parser().parse_args(["analytics", "market", "SPY", "--fields", "rsi_14"])
            self.assertEqual(dispatch(args), 0)
        self.assertEqual(json.loads(out.getvalue()), result)

    def test_aggregated_instrument_retains_uuid_across_lots(self):
        with tempfile.TemporaryDirectory() as temporary:
            with PositionStore(temporary + "/positions.sqlite3") as store:
                initial = store.reconcile([position(long=1)])["EQUITY:AAPL"]
                # Both lots are represented by Schwab as a changed aggregate quantity.
                amended = store.reconcile([position(long=3)])["EQUITY:AAPL"]
                self.assertEqual(initial, amended)
                first_order = store.reserve_order("EQUITY:AAPL")
                second_order = store.reserve_order("EQUITY:AAPL")
                self.assertNotEqual(first_order, second_order)
                self.assertNotEqual(first_order, initial)
                self.assertEqual(store.reconcile([position(long=5)])["EQUITY:AAPL"], initial)


if __name__ == "__main__":
    unittest.main()

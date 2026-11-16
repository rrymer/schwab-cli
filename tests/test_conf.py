"""Configuration consistency across CLI and direct Python call paths."""
import unittest
from unittest.mock import patch

from schwab_cli import alerts, conf, monitor, orders, market_data
from schwab_cli.cli import build_parser


class ConfigurationTests(unittest.TestCase):
    def test_monitor_and_alert_defaults_are_shared(self):
        parser = build_parser()
        self.assertEqual(
            parser.parse_args(["positions", "monitor"]).interval,
            conf.POSITION_POLL_SECONDS,
        )
        self.assertEqual(monitor.monitor.__kwdefaults__["interval"],
                         conf.POSITION_POLL_SECONDS)
        self.assertEqual(
            parser.parse_args(["monitor", "alert", "--symbol", "SPY",
                               "--above", "10", "--stdout"]).interval,
            conf.ALERT_POLL_SECONDS,
        )
        self.assertEqual(alerts.alert.__kwdefaults__["interval"],
                         conf.ALERT_POLL_SECONDS)

    def test_history_defaults_and_limits(self):
        args = build_parser().parse_args(["analytics", "history", "SPY"])
        self.assertEqual((args.period_type, args.period, args.frequency_type,
                          args.frequency, args.limit),
                         (conf.HISTORY_PERIOD_TYPE, conf.HISTORY_PERIOD,
                          conf.HISTORY_FREQUENCY_TYPE, conf.HISTORY_FREQUENCY,
                          conf.HISTORY_DISPLAY_LIMIT))
        with patch.object(market_data, "_get", return_value={"candles": []}) as http:
            market_data.get_price_history("SPY")
        query = http.call_args.args[1]
        self.assertEqual(query["periodType"], conf.HISTORY_PERIOD_TYPE)
        self.assertEqual(query["period"], conf.HISTORY_PERIOD)
        self.assertEqual(query["frequencyType"], conf.HISTORY_FREQUENCY_TYPE)
        self.assertEqual(query["frequency"], conf.HISTORY_FREQUENCY)

    def test_order_defaults_are_shared(self):
        args = build_parser().parse_args(["positions", "add", "SPY", "BUY", "1"])
        self.assertEqual(args.order_type, conf.DEFAULT_ORDER_TYPE)
        self.assertEqual(args.session, conf.DEFAULT_SESSION)
        self.assertEqual(args.duration, conf.DEFAULT_DURATION)
        self.assertEqual(orders.SchwabOrder().session, conf.DEFAULT_SESSION)
        self.assertEqual(orders.build_single_order.__kwdefaults__["order_type"],
                         conf.DEFAULT_ORDER_TYPE)


if __name__ == "__main__":
    unittest.main()

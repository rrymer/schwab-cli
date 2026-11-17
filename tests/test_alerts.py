"""Read-only alert engine tests: no Schwab tokens or OS notifications."""
import io
import unittest
from unittest.mock import Mock, patch

from schwab_cli import alerts, conf
from schwab_cli.integrations import EmailDeliveryError, Message, Provider
from schwab_cli.cli import build_parser, dispatch
from schwab_cli.store import PositionStore
from tests.helpers import position, snapshot


class AlertTests(unittest.TestCase):
    def test_price_metric_uses_quote_for_market_symbol(self):
        with patch.object(alerts, "get_quotes", return_value={
            "SPY": {"quote": {"lastPrice": 501.25, "mark": 501.4}}
        }) as quotes:
            self.assertEqual(alerts.price_metric(alerts.AlertTarget(symbol="spy")), 501.25)
        quotes.assert_called_once_with(["SPY"])

    def test_price_metric_position_requires_uuid_and_uses_option_mark(self):
        instrument = position("SPY   261220P00500000", asset="OPTION")
        positions = snapshot(instrument)
        with patch.object(alerts, "get_positions", return_value=positions), \
             patch.object(alerts, "PositionStore") as store, \
             patch.object(alerts, "get_quotes", return_value={
                 "SPY   261220P00500000": {"quote": {"lastPrice": 1.25, "mark": 1.5}}
             }):
            store.key.side_effect = PositionStore.key
            store.return_value.__enter__.return_value.reconcile.return_value = {
                "OPTION:SPY   261220P00500000": "fake-uuid"
            }
            self.assertEqual(alerts.price_metric(alerts.AlertTarget(position_id="fake-uuid")), 1.5)

    def test_unknown_position_uuid_is_rejected(self):
        with patch.object(alerts, "get_positions", return_value=snapshot(position())), \
             patch.object(alerts, "PositionStore") as store:
            store.key.side_effect = PositionStore.key
            store.return_value.__enter__.return_value.reconcile.return_value = {"EQUITY:AAPL": "real-id"}
            with self.assertRaisesRegex(ValueError, "no active holding"):
                alerts.price_metric(alerts.AlertTarget(position_id="unknown"))

    def test_alert_target_requires_exactly_one_reference(self):
        for kwargs in ({}, {"symbol": "SPY", "position_id": "id"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                alerts.AlertTarget(**kwargs)

    def test_threshold_fires_once_and_rearms(self):
        rule = alerts.ThresholdRule("above", 100.0)
        self.assertFalse(rule.update(99))
        self.assertTrue(rule.update(100))
        self.assertFalse(rule.update(101))
        self.assertFalse(rule.update(None))
        self.assertFalse(rule.update(100))
        self.assertFalse(rule.update(99))
        self.assertTrue(rule.update(100))
        below = alerts.ThresholdRule("below", 100)
        self.assertTrue(below.update(100))
        self.assertFalse(below.update(90))
        self.assertFalse(below.update(101))
        self.assertTrue(below.update(99))

    def test_osc9_notification_filters_terminal_controls(self):
        output = io.StringIO()
        alerts.notify_terminal("SPY\x1b]9;evil\x07 message", stream=output)
        self.assertEqual(output.getvalue().count("\x1b]9;"), 1)
        self.assertEqual(output.getvalue().count("\x07"), 1)
        self.assertIn("SPY", output.getvalue())

    def test_alert_once_emits_only_when_met(self):
        delivered = []
        alerts.alert(symbol="SPY", metric="price", above=500, once=True, notify=True,
                     metric_evaluators={"price": lambda _: 501.5},
                     notifier=delivered.append)
        self.assertEqual(len(delivered), 1)
        self.assertIn("501.5", delivered[0])
        delivered.clear()
        alerts.alert(symbol="SPY", metric="price", above=500, once=True, notify=True,
                     metric_evaluators={"price": lambda _: 499},
                     notifier=delivered.append)
        self.assertEqual(delivered, [])

    def test_stdout_only_and_both_outputs(self):
        output = io.StringIO()
        notified = []
        with patch("sys.stdout", output):
            alerts.alert(symbol="SPY", above=500, once=True, stdout=True,
                         metric_evaluators={"price": lambda _: 501.5},
                         notifier=notified.append)
        self.assertIn("Schwab alert: SPY", output.getvalue())
        self.assertEqual(notified, [])

        output = io.StringIO()
        with patch("sys.stdout", output):
            alerts.alert(symbol="SPY", above=500, once=True, stdout=True, notify=True,
                         metric_evaluators={"price": lambda _: 501.5},
                         notifier=notified.append)
        self.assertIn("Schwab alert: SPY", output.getvalue())
        self.assertEqual(len(notified), 1)
        self.assertEqual(output.getvalue().strip(), notified[0])

    def test_missing_outputs_fail_before_polling(self):
        fetched = []
        with self.assertRaisesRegex(ValueError, "at least one alert output"):
            alerts.alert(symbol="SPY", above=500, once=True,
                         metric_evaluators={"price": lambda _: fetched.append(1)})
        self.assertEqual(fetched, [])

    def test_missing_quote_does_not_fire_or_reset(self):
        rule = alerts.ThresholdRule("above", 10)
        self.assertTrue(rule.update(11))
        self.assertFalse(rule.update(None))
        self.assertFalse(rule.update(11))

    def test_invalid_metric_or_interval_fails_before_fetching(self):
        with self.assertRaisesRegex(ValueError, "Unknown alert metric"):
            alerts.alert(symbol="SPY", metric="rsi_14", above=50, once=True, stdout=True)
        with self.assertRaisesRegex(ValueError, "interval"):
            alerts.alert(symbol="SPY", above=100, interval=0, once=True, stdout=True)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            alerts.alert(symbol="SPY", once=True, stdout=True)
        with self.assertRaises(ValueError):
            alerts.alert(symbol="SPY", above=float("nan"), once=True, stdout=True)

    def test_cli_parser_and_dispatch_to_alert(self):
        args = build_parser().parse_args([
            "monitor", "alert", "--symbol", "SPY", "--metric", "price",
            "--above", "500", "--once", "--stdout", "--notify",
        ])
        self.assertEqual(args.symbol, "SPY")
        with patch.object(alerts, "alert") as run:
            self.assertEqual(dispatch(args), 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["above"], 500.0)
        self.assertTrue(run.call_args.kwargs["stdout"])
        self.assertTrue(run.call_args.kwargs["notify"])

    def test_gmail_only_alert_resolves_provider_and_prefixes_message(self):
        provider = Mock(spec=Provider)
        with patch.object(alerts, "gmail_provider", return_value=provider) as factory:
            alerts.alert(symbol="SPY", above=500, once=True, gmail_notify=True,
                         metric_evaluators={"price": lambda _: 501.5})
        factory.assert_called_once_with()
        body = "Schwab alert: SPY price 501.5 above 500"
        provider.send.assert_called_once_with(Message(f"{conf.EMAIL_SUBJECT_PREFIX} {body}", body))

    def test_email_does_not_fire_when_condition_unmet(self):
        provider = Mock(spec=Provider)
        alerts.alert(symbol="SPY", above=500, once=True, email_provider=provider,
                     metric_evaluators={"price": lambda _: 499})
        provider.send.assert_not_called()

    def test_email_failure_does_not_abort_or_rearm_alert(self):
        provider = Mock(spec=Provider)
        provider.send.side_effect = [EmailDeliveryError("private detail"), None]
        evaluator = Mock(side_effect=[501, 502, None, 499, 501, KeyboardInterrupt])
        output, errors = io.StringIO(), io.StringIO()
        with (patch.object(alerts.time, "sleep"),
              patch("sys.stdout", output), patch("sys.stderr", errors)):
            alerts.alert(symbol="SPY", above=500, stdout=True, email_provider=provider,
                         metric_evaluators={"price": evaluator})
        self.assertEqual(provider.send.call_count, 2)
        self.assertEqual(output.getvalue().count("Schwab alert:"), 2)
        self.assertIn("alert email failed", errors.getvalue())
        self.assertNotIn("private detail", errors.getvalue())

    def test_gmail_flag_dispatch(self):
        args = build_parser().parse_args([
            "monitor", "alert", "--symbol", "SPY", "--above", "500", "--gmail-notify",
        ])
        with patch.object(alerts, "alert") as run:
            self.assertEqual(dispatch(args), 0)
        self.assertTrue(run.call_args.kwargs["gmail_notify"])
        self.assertFalse(run.call_args.kwargs["stdout"])
        self.assertFalse(run.call_args.kwargs["notify"])

    def test_gmail_setup_failure_precedes_polling(self):
        evaluator = Mock()
        with patch.object(alerts, "gmail_provider", side_effect=EmailDeliveryError("missing password")):
            with self.assertRaises(EmailDeliveryError):
                alerts.alert(symbol="SPY", above=500, gmail_notify=True,
                             metric_evaluators={"price": evaluator})
        evaluator.assert_not_called()

    def test_old_position_monitor_command_still_parses(self):
        args = build_parser().parse_args(["positions", "monitor", "some-uuid", "--once"])
        self.assertEqual(args.command, "positions")
        self.assertEqual(args.positions_command, "monitor")
        self.assertEqual(args.position_id, "some-uuid")


if __name__ == "__main__":
    unittest.main()

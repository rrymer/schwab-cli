import io
import json
import unittest
from unittest.mock import Mock, patch

from schwab_cli import api, conf, order_monitor
from schwab_cli.integrations import EmailDeliveryError, Provider


def order(filled, status):
    return {
        "orderId": 123, "status": status, "filledQuantity": filled,
        "remainingQuantity": 5 - filled, "accountNumber": 987654321,
        "orderLegCollection": [
            {"legId": 1, "instrument": {"symbol": "SPY"}, "instruction": "BUY", "quantity": 5}
        ],
        "orderActivityCollection": [
            {"activityType": "EXECUTION", "executionType": "FILL", "quantity": filled,
             "executionLegs": [{"legId": 1, "price": 600.25, "quantity": filled,
                                "time": "2026-11-17T16:00:00Z"}]}
        ] if filled else [],
    }


class OrderMonitorTests(unittest.TestCase):
    def test_get_order_uses_read_only_endpoint(self):
        with (patch.object(api, "account_numbers", return_value="HASH/1"),
              patch.object(api, "_get", return_value=order(5, "FILLED")) as get):
            self.assertEqual(api.get_order(123)["status"], "FILLED")
        get.assert_called_once_with("/accounts/HASH%2F1/orders/123")

    def test_get_order_rejects_mismatched_id(self):
        with (patch.object(api, "account_numbers", return_value="HASH"),
              patch.object(api, "_get", return_value=order(5, "FILLED"))):
            with self.assertRaises(api.SchwabAPIError):
                api.get_order(456)

    def test_partial_then_full_fill_and_no_duplicates(self):
        provider = Mock(spec=Provider)
        snapshots = [order(0, "WORKING"), order(2, "WORKING"), order(2, "WORKING"),
                     order(5, "FILLED"), AssertionError("extra poll")]
        with (patch.object(order_monitor, "get_order", side_effect=snapshots) as fetch,
              patch.object(order_monitor.time, "sleep") as sleep,
              patch("sys.stderr", io.StringIO())):
            order_monitor.monitor_order(123, email_provider=provider)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(provider.send.call_count, 2)
        partial, final = [call.args[0] for call in provider.send.call_args_list]
        self.assertTrue(partial.subject.startswith(f"{conf.EMAIL_SUBJECT_PREFIX} ORDER_PARTIAL_FILL"))
        self.assertTrue(final.subject.startswith(f"{conf.EMAIL_SUBJECT_PREFIX} ORDER_FILLED"))
        details = json.loads(final.body)
        self.assertEqual(details["newly_observed_filled_quantity"], 3)
        self.assertEqual(details["executions"][0]["executionLegs"][0]["price"], 600.25)
        self.assertNotIn("987654321", final.body)

    def test_terminal_without_execution_never_emails(self):
        for status in ("CANCELED", "REJECTED", "EXPIRED", "REPLACED"):
            with self.subTest(status=status):
                provider = Mock(spec=Provider)
                with (patch.object(order_monitor, "get_order", return_value=order(0, status)),
                      patch.object(order_monitor.time, "sleep") as sleep,
                      patch("sys.stderr", io.StringIO())):
                    order_monitor.monitor_order(123, email_provider=provider)
                sleep.assert_not_called()
                provider.send.assert_not_called()

    def test_already_filled_order_exits_even_if_email_fails(self):
        provider = Mock(spec=Provider)
        provider.send.side_effect = EmailDeliveryError("private")
        errors = io.StringIO()
        with (patch.object(order_monitor, "get_order", return_value=order(5, "FILLED")),
              patch.object(order_monitor.time, "sleep") as sleep,
              patch("sys.stderr", errors)):
            order_monitor.monitor_order(123, email_provider=provider)
        sleep.assert_not_called()
        provider.send.assert_called_once()
        self.assertIn("email failed", errors.getvalue())
        self.assertNotIn("private", errors.getvalue())

    def test_once_fetches_one_snapshot(self):
        provider = Mock(spec=Provider)
        with (patch.object(order_monitor, "get_order", return_value=order(0, "WORKING")) as fetch,
              patch.object(order_monitor.time, "sleep") as sleep):
            order_monitor.monitor_order(123, email_provider=provider, once=True)
        fetch.assert_called_once_with(123)
        sleep.assert_not_called()
        provider.send.assert_not_called()

import io
import json
import math
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock
from urllib.error import URLError
from schwab_cli import orders
from schwab_cli.cli import build_parser, dispatch
from schwab_cli.integrations import EmailDeliveryError
from tests.helpers import response


class OrderTests(unittest.TestCase):
    def test_limit_equity_payload(self):
        order = orders.build_single_order(symbol=' aapl ', asset_type='EQUITY', instruction='BUY', quantity=2, price=150.25)
        payload = order.to_create_payload()
        self.assertEqual(payload['orderType'], 'LIMIT')
        self.assertEqual(payload['price'], 150.25)
        self.assertEqual(payload['orderLegCollection'][0]['instrument']['symbol'], 'AAPL')
        self.assertEqual(payload['orderLegCollection'][0]['quantity'], 2)
        self.assertNotIn('orderId', payload)
        self.assertNotIn('legId', payload['orderLegCollection'][0])

    def test_option_instruction(self):
        order = orders.build_single_order(symbol='SPY   271217P00500000', asset_type='OPTION', instruction='SELL_TO_OPEN', quantity=1, price=2.5)
        self.assertEqual(order.orderLegCollection[0].instruction, 'SELL_TO_OPEN')
        self.assertEqual(order.orderLegCollection[0].instrument.assetType, 'OPTION')

    def test_market_order_omits_price(self):
        order = orders.build_single_order(symbol='AAPL', asset_type='EQUITY', instruction='SELL', quantity=1, order_type='MARKET')
        self.assertNotIn('price', order.to_create_payload())

    def test_invalid_orders_cannot_be_built(self):
        cases = [
            dict(symbol='', asset_type='EQUITY', instruction='BUY', quantity=1, price=1),
            dict(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=0, price=1),
            dict(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=float('nan'), price=1),
            dict(symbol='AAPL', asset_type='EQUITY', instruction='BUY_TO_OPEN', quantity=1, price=1),
            dict(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1),
            dict(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1, price=-1),
            dict(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1, price=float('inf')),
            dict(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1, price=1, order_type='MARKET'),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                orders.build_single_order(**case)

    def test_dry_run_never_uses_credentials_db_or_network(self):
        out = io.StringIO()
        with patch.object(orders, 'submit_order') as submit, \
             patch.object(orders, 'PositionStore') as store, \
             redirect_stdout(out):
            orders.add_position(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1, price=100)
        submit.assert_not_called()
        store.assert_not_called()
        self.assertIn('DRY RUN', out.getvalue())

    def test_submit_request_payload_and_location(self):
        order = orders.build_single_order(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1, price=100)
        with patch.object(orders, 'account_numbers', return_value='HASH'), \
             patch.object(orders, 'get_access_token', return_value='testtoken'), \
             patch.object(orders, 'urlopen', return_value=response(headers={'Location': '/orders/1'})) as send:
            self.assertEqual(orders.submit_order(order), '/orders/1')
        req = send.call_args.args[0]
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.get_header('Authorization'), 'Bearer testtoken')
        self.assertTrue(req.full_url.endswith('/accounts/HASH/orders'))
        self.assertEqual(json.loads(req.data)['orderLegCollection'][0]['instruction'], 'BUY')
        send.assert_called_once()

    def test_failed_post_is_not_retried(self):
        order = orders.build_single_order(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1, price=100)
        with patch.object(orders, 'account_numbers', return_value='HASH'), \
             patch.object(orders, 'get_access_token', return_value='fake'), \
             patch.object(orders, 'urlopen', side_effect=URLError('lost connection')) as send:
            with self.assertRaisesRegex(Exception, 'unknown'):
                orders.submit_order(order)
            send.assert_called_once()

    def test_submitted_order_prints_uuid_and_records_acceptance(self):
        store = MagicMock()
        store.__enter__.return_value = store
        store.active_id.return_value = None
        store.reserve_order.return_value = 'LOCAL-UUID'
        out = io.StringIO()
        with patch.object(orders, 'PositionStore', return_value=store), \
             patch.object(orders, 'submit_order', return_value='/orders/1') as submit, \
             redirect_stdout(out):
            orders.add_position(symbol='AAPL', asset_type='EQUITY', instruction='BUY', quantity=1, price=100, submit=True)
        submit.assert_called_once()
        store.record_order_location.assert_called_once_with('LOCAL-UUID', '/orders/1')
        self.assertIn('LOCAL-UUID', out.getvalue())


    def test_notification_dry_run_does_not_resolve_credentials(self):
        with (patch.object(orders, "gmail_provider") as provider,
              patch.object(orders, "submit_order") as submit,
              patch.object(orders, "monitor_order") as monitor,
              redirect_stdout(io.StringIO())):
            orders.add_position(symbol="AAPL", asset_type="EQUITY", instruction="BUY",
                                quantity=1, price=100, gmail_notify=True)
        provider.assert_not_called()
        submit.assert_not_called()
        monitor.assert_not_called()

    def test_cli_submission_monitors_returned_order_id(self):
        args = build_parser().parse_args([
            "positions", "add", "AAPL", "BUY", "1", "--price", "100",
            "--submit", "--gmail-notify",
        ])
        store = MagicMock()
        store.__enter__.return_value = store
        store.reserve_order.return_value = "LOCAL-UUID"
        with (patch.object(orders, "PositionStore", return_value=store),
              patch.object(orders, "gmail_provider") as provider,
              patch.object(orders, "submit_order", return_value="https://api.schwabapi.com/trader/v1/accounts/HASH/orders/123") as submit,
              patch.object(orders, "monitor_order") as monitor,
              redirect_stdout(io.StringIO())):
            def check_recorded(*args, **kwargs):
                store.record_order_location.assert_called_once()
            monitor.side_effect = check_recorded
            self.assertEqual(dispatch(args), 0)
        submit.assert_called_once()
        monitor.assert_called_once_with(123, email_provider=provider.return_value)

    def test_bad_credentials_fail_before_submission(self):
        with (patch.object(orders, "gmail_provider", side_effect=EmailDeliveryError("missing")),
              patch.object(orders, "submit_order") as submit,
              patch.object(orders, "PositionStore") as store,
              redirect_stdout(io.StringIO())):
            with self.assertRaises(EmailDeliveryError):
                orders.add_position(symbol="AAPL", asset_type="EQUITY", instruction="BUY",
                                    quantity=1, price=100, submit=True, gmail_notify=True)
        submit.assert_not_called()
        store.assert_not_called()

    def test_missing_location_does_not_resubmit(self):
        provider = MagicMock()
        with (patch.object(orders, "PositionStore"),
              patch.object(orders, "submit_order", return_value=None) as submit,
              patch.object(orders, "monitor_order") as monitor,
              patch("sys.stderr", io.StringIO()) as errors,
              redirect_stdout(io.StringIO())):
            orders.add_position(symbol="AAPL", asset_type="EQUITY", instruction="BUY",
                                quantity=1, price=100, submit=True, email_provider=provider)
        submit.assert_called_once()
        monitor.assert_not_called()
        self.assertIn("order accepted", errors.getvalue())

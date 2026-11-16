"""Account funds and position P&L tests without credentials or real account data."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from schwab_cli import funds, position_metrics, positions
from schwab_cli.cli import build_parser, dispatch
from schwab_cli.store import PositionStore
from schwab_cli import store
from tests.helpers import position, snapshot


class FundsTests(unittest.TestCase):
    def test_compact_funds_values_and_missing_fields(self):
        account = snapshot(position())[0]
        account["securitiesAccount"]["currentBalances"] = {
            "cashBalance": 12500,
            "cashAvailableForTrading": 8000,
            "availableFunds": 7000,
            "buyingPower": 24000,
            "optionBuyingPower": 3000,
            "dayTradingBuyingPower": 50000,
        }
        self.assertEqual(funds.render_funds(account),
                         "C:12.5k  T:8.0k  A:7.0k  B:24.0k  O:3.0k  D:50.0k")
        account["securitiesAccount"]["currentBalances"].pop("optionBuyingPower")
        self.assertIn("O:-", funds.render_funds(account))
        self.assertIn("C:-", funds.render_funds(snapshot()[0]))

    def test_funds_command_and_dashboard(self):
        data = snapshot(position())
        data[0]["securitiesAccount"]["currentBalances"] = {
            "cashBalance": -1150, "availableFunds": 20100,
        }
        with patch.object(funds, "get_positions", return_value=data), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(dispatch(build_parser().parse_args(["positions", "funds"])), 0)
        self.assertIn("C:-1.1k", out.getvalue())
        with patch.object(funds, "get_positions", return_value=data), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(dispatch(build_parser().parse_args(["positions", "funds", "--json"])), 0)
        self.assertEqual(json.loads(out.getvalue())["availableFunds"], 20100)
        displayed = positions.render_positions(data, {"EQUITY:AAPL": "uuid"}, {"EQUITY:AAPL": (10.0, None)})
        self.assertLess(displayed.index("A:20.1k"), displayed.index("Symbol"))

    def test_non_numeric_balances_are_not_fabricated(self):
        account = snapshot()[0]
        account["securitiesAccount"]["currentBalances"] = {"cashBalance": "1000", "buyingPower": float("nan")}
        values = funds.account_funds(account)
        self.assertIsNone(values["cashBalance"])
        self.assertIsNone(values["buyingPower"])


class PositionPnlTests(unittest.TestCase):
    def test_long_stock_unrealized(self):
        item = position()
        item.update(longQuantity=2, shortQuantity=0, averagePrice=100, marketValue=230)
        self.assertEqual(position_metrics.unrealized_pnl(item), 30)

    def test_short_stock_unrealized(self):
        item = position()
        item.update(longQuantity=0, shortQuantity=3, averagePrice=100, marketValue=-270)
        self.assertEqual(position_metrics.unrealized_pnl(item), 30)

    def test_long_and_short_options_use_multiplier(self):
        item = position(asset="OPTION")
        item.update(longQuantity=1, shortQuantity=0, averagePrice=2.50, marketValue=340)
        self.assertEqual(position_metrics.unrealized_pnl(item), 90)
        item.update(longQuantity=0, shortQuantity=2, averagePrice=3, marketValue=-450)
        self.assertEqual(position_metrics.unrealized_pnl(item), 150)

    def test_multiplier_is_respected_and_incomplete_values_rejected(self):
        item = position(asset="OPTION")
        item.update(longQuantity=1, shortQuantity=0, averagePrice=2.0, marketValue=350)
        item["instrument"]["multiplier"] = 50
        self.assertEqual(position_metrics.unrealized_pnl(item), 250)
        item.pop("averagePrice")
        self.assertIsNone(position_metrics.unrealized_pnl(item))

    def test_metric_command_resolves_uuid_and_isolated_account(self):
        data = snapshot(position())
        data[0]["securitiesAccount"]["positions"][0].update(
            averagePrice=100, marketValue=115, longQuantity=1, shortQuantity=0
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "positions.sqlite3"
            with patch.object(store, "default_db_path", return_value=path):
                with PositionStore() as db:
                    uid = db.reconcile(data[0]["securitiesAccount"]["positions"])["EQUITY:AAPL"]
                args = build_parser().parse_args([
                    "analytics", "calculate", "--position-id", uid, "--metric", "pnl"
                ])
                with patch.object(position_metrics, "get_positions", return_value=data), redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(dispatch(args), 0)
                response = json.loads(out.getvalue())
                self.assertEqual(response["value"], 15)
                self.assertEqual(response["basis"], "unrealized_average_cost")
                with patch.object(position_metrics, "get_positions", return_value=data):
                    with self.assertRaisesRegex(ValueError, "No active position"):
                        position_metrics.calculate_position("not-active")

    def test_invalid_position_metric_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown position metric"):
            position_metrics.calculate_position("id", "not-supported")


if __name__ == "__main__":
    unittest.main()

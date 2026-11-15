"""Tests for arbitrary API field selection and its CLI wiring."""
import csv
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from schwab_cli import market_data
from schwab_cli.cli import build_parser, dispatch
from schwab_cli.field_selection import parse_fields, print_fields


class FieldSelectionTests(unittest.TestCase):
    def test_parse_fields_preserves_order_and_case(self):
        self.assertEqual(parse_fields("volatility, delta , strikePrice"), ("volatility", "delta", "strikePrice"))

    def test_invalid_fields_are_rejected(self):
        for spec in ("", " ", "delta,", ",delta", "delta,,gamma", "delta,delta"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_fields(spec)

    def test_csv_missing_properties_and_escaping(self):
        rows = [
            {"volatility": 22.5, "description": 'a,b"c', "nested": {"foo": [1, 2]}},
            {"volatility": 0.0, "description": None},
        ]
        out = io.StringIO()
        with redirect_stdout(out):
            print_fields(rows, ("volatility", "description", "nested", "missing"))
        values = list(csv.reader(io.StringIO(out.getvalue())))
        self.assertEqual(values[0], ["volatility", "description", "nested", "missing"])
        self.assertEqual(values[1], ["22.5", 'a,b"c', '{"foo":[1,2]}', ""])
        self.assertEqual(values[2], ["0.0", "", "", ""])

    def test_chain_fields_select_contract_properties_and_context(self):
        chain = {
            "symbol": "$SPX",
            "callExpDateMap": {},
            "putExpDateMap": {
                "2026-12-18:33": {
                    "6000.0": [
                        {"strikePrice": 6000.0, "volatility": 19.6, "delta": -0.15},
                        {"strikePrice": 6000.0, "delta": -0.2},
                    ]
                }
            },
        }
        args = build_parser().parse_args([
            "options", "chain", "$SPX", "--type", "PUT",
            "--fields", "side,expirationKey,strikePrice,volatility,delta",
            "--limit", "2",
        ])
        out = io.StringIO()
        with patch.object(market_data, "get_option_chain", return_value=chain), redirect_stdout(out):
            self.assertEqual(dispatch(args), 0)
        result = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["side"], "PUT")
        self.assertEqual(result[0]["expirationKey"], "2026-12-18:33")
        self.assertEqual(result[0]["volatility"], "19.6")
        self.assertEqual(result[1]["volatility"], "")

    def test_chain_fields_respect_limit(self):
        chain = {
            "callExpDateMap": {"2026-12-18:33": {"100.0": [{"bidPrice": 1}, {"bidPrice": 2}]}},
            "putExpDateMap": {},
        }
        args = build_parser().parse_args(["options", "chain", "SPY", "--fields", "bidPrice", "--limit", "1"])
        out = io.StringIO()
        with patch.object(market_data, "get_option_chain", return_value=chain), redirect_stdout(out):
            dispatch(args)
        self.assertEqual(out.getvalue(), "bidPrice\n1\n")

    def test_history_fields_respect_last_n_limit(self):
        candles = [{"datetime": 100, "close": 1}, {"datetime": 200, "close": 2},
                   {"datetime": 300, "close": 3}]
        args = build_parser().parse_args(["market", "history", "SPY", "--fields", "datetime,close", "--limit", "2"])
        out = io.StringIO()
        with patch.object(market_data, "get_price_history", return_value={"symbol": "SPY", "candles": candles}), redirect_stdout(out):
            dispatch(args)
        self.assertEqual(out.getvalue(), "datetime,close\n200,2\n300,3\n")

    def test_expiration_fields_and_zero_dte_filter(self):
        expirations = [{"expirationDate": "2026-11-15", "daysToExpiration": 0},
                       {"expirationDate": "2026-11-20", "daysToExpiration": 5}]
        args = build_parser().parse_args([
            "options", "expirations", "SPX", "--fields", "expirationDate,daysToExpiration"
        ])
        out = io.StringIO()
        with patch.object(market_data, "get_option_expirations", return_value=expirations), redirect_stdout(out):
            dispatch(args)
        self.assertEqual(out.getvalue(), "expirationDate,daysToExpiration\n2026-11-20,5\n")

    def test_json_and_fields_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["options", "chain", "SPY", "--json", "--fields", "delta"])

    def test_existing_json_returns_unprojected_response(self):
        chain = {"symbol": "SPY", "callExpDateMap": {}, "putExpDateMap": {}, "status": "SUCCESS"}
        args = build_parser().parse_args(["options", "chain", "SPY", "--json"])
        out = io.StringIO()
        with patch.object(market_data, "get_option_chain", return_value=chain), redirect_stdout(out):
            dispatch(args)
        self.assertEqual(json.loads(out.getvalue()), chain)


if __name__ == "__main__":
    unittest.main()

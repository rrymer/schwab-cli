"""Contract and CLI tests for the read-only Schwab Market Data API."""
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from schwab_cli import market_data
from schwab_cli.api import SchwabAPIError
from schwab_cli.cli import build_parser, dispatch


class Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return io.BytesIO(json.dumps(self.payload).encode("utf-8"))
    def __exit__(self, exc_type, exc, tb):
        return False


class MarketDataTests(unittest.TestCase):
    def test_history_endpoint_authorization_and_dates(self):
        seen = {}
        def request(req, timeout):
            seen["request"], seen["timeout"] = req, timeout
            return Response({"symbol": "AAPL", "candles": [{"open": 10, "datetime": 1}]})
        with patch.object(market_data, "get_access_token", return_value="fake"), patch.object(market_data, "urlopen", side_effect=request):
            result = market_data.get_price_history(" aapl ", start=date(2026, 1, 1), end=date(2026, 1, 10))
        self.assertEqual(result["candles"][0]["open"], 10)
        self.assertEqual(seen["timeout"], 30)
        req = seen["request"]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.get_header("Authorization"), "Bearer fake")
        self.assertEqual(urlsplit(req.full_url).path, "/marketdata/v1/pricehistory")
        params = parse_qs(urlsplit(req.full_url).query)
        self.assertEqual(params["symbol"], ["AAPL"])
        self.assertEqual(params["startDate"], ["1767225600000"])
        self.assertEqual(params["endDate"], ["1768003200000"])
        self.assertEqual(params["needExtendedHoursData"], ["false"])

    def test_valid_frequency_constraints(self):
        with patch.object(market_data, "_get") as transport:
            invalid = [
                {"period_type": "day", "frequency_type": "daily"},
                {"period_type": "year", "frequency_type": "minute"},
                {"period": 4},
                {"frequency": 5},
                {"start": date(2026, 2, 2), "end": date(2026, 2, 1)},
            ]
            for kw in invalid:
                with self.subTest(kw=kw), self.assertRaises(ValueError):
                    market_data.get_price_history("SPY", **kw)
            transport.assert_not_called()

    def test_datetime_tz_required(self):
        self.assertEqual(market_data._epoch_ms(datetime(1970, 1, 1, tzinfo=timezone.utc)), 0)
        with self.assertRaises(ValueError):
            market_data._epoch_ms(datetime(2026, 11, 15))

    def test_expiration_selection_and_zero_dte(self):
        data = {"expirationList": [
            {"expirationDate": "2026-11-15", "daysToExpiration": 0},
            {"expirationDate": "2026-11-20", "daysToExpiration": 5},
            {"expirationDate": "2026-11-21", "daysToExpiration": 6},
        ]}
        with patch.object(market_data, "_get", return_value=data):
            self.assertEqual(market_data.nearest_expiration("SPX")["daysToExpiration"], 5)
            self.assertEqual(market_data.nearest_expiration("SPX", include_0dte=True)["daysToExpiration"], 0)
        with patch.object(market_data, "_get", return_value={"expirationList": []}):
            self.assertIsNone(market_data.nearest_expiration("SPX"))

    def test_schwab_spx_symbol_is_normalized(self):
        self.assertEqual(market_data._symbol("SPX"), "$SPX")
        self.assertEqual(market_data._symbol("$SPX.X"), "$SPX")
        self.assertEqual(market_data._symbol("$SPX"), "$SPX")
        with patch.object(market_data, "_get", return_value={"putExpDateMap": {}}) as request:
            market_data.get_option_chain("SPX", contract_type="PUT", strike_count=5)
        self.assertEqual(request.call_args.args[1]["symbol"], "$SPX")

    def test_chain_query_parameters(self):
        with patch.object(market_data, "_get", return_value={"callExpDateMap": {}, "putExpDateMap": {}}) as request:
            market_data.get_option_chain("spy", contract_type="PUT", from_date="2026-12-01",
                                         to_date=date(2026, 12, 18), strike_count=5)
        path, params = request.call_args.args
        self.assertEqual(path, "/chains")
        self.assertEqual(params["symbol"], "SPY")
        self.assertEqual(params["contractType"], "PUT")
        self.assertEqual(params["strategy"], "SINGLE")
        self.assertEqual(params["toDate"], "2026-12-18")
        self.assertEqual(params["strikeCount"], 5)

    def test_contract_iterator_handles_list_and_object(self):
        chain = {
            "callExpDateMap": {"2026-12-18:33": {"100.0": [{"bidPrice": 1}, {"bidPrice": 2}]}},
            "putExpDateMap": {"2026-12-18:33": {"90.0": {"bidPrice": 3}}},
        }
        items = list(market_data.iter_option_contracts(chain))
        self.assertEqual([x[0] for x in items], ["CALL", "CALL", "PUT"])
        self.assertEqual([x[3]["bidPrice"] for x in items], [1, 2, 3])

    def test_invalid_chain_rejected_without_http(self):
        with patch.object(market_data, "_get") as request:
            for kw in (
                {"contract_type": "INVALID"}, {"strike_count": 0},
                {"to_date": "yesterday"},
                {"from_date": "2026-12-20", "to_date": "2026-12-01"},
            ):
                with self.subTest(kw=kw), self.assertRaises(ValueError):
                    market_data.get_option_chain("AAPL", **kw)
            request.assert_not_called()

    def test_missing_required_response_structure(self):
        with patch.object(market_data, "_get", return_value={}):
            with self.assertRaises(SchwabAPIError):
                market_data.get_price_history("AAPL")
            with self.assertRaises(SchwabAPIError):
                market_data.get_option_expirations("AAPL")
            with self.assertRaises(SchwabAPIError):
                market_data.get_option_chain("AAPL")

    def test_api_error_does_not_dump_response(self):
        err = HTTPError("https://example.invalid", 403, "Unauthorized", {}, io.BytesIO(b"private-secret"))
        with patch.object(market_data, "get_access_token", return_value="fake"), patch.object(market_data, "urlopen", side_effect=err):
            with self.assertRaises(SchwabAPIError) as captured:
                market_data.get_option_expirations("SPY")
        self.assertIn("403", str(captured.exception))
        self.assertNotIn("private-secret", str(captured.exception))

    def test_cli_args(self):
        args = build_parser().parse_args(["market", "history", "AAPL", "--frequency-type", "weekly"])
        self.assertEqual(args.frequency_type, "weekly")
        args = build_parser().parse_args(["options", "chain", "SPX", "--type", "PUT"])
        self.assertEqual(args.contract_type, "PUT")

    def test_cli_expirations_filter(self):
        sample = [{"expirationDate": "2026-11-15", "daysToExpiration": 0},
                  {"expirationDate": "2026-11-20", "daysToExpiration": 5}]
        with patch.object(market_data, "get_option_expirations", return_value=sample), redirect_stdout(io.StringIO()) as output:
            code = dispatch(build_parser().parse_args(["options", "expirations", "SPX"]))
        self.assertEqual(code, 0)
        self.assertIn("2026-11-20", output.getvalue())
        self.assertNotIn("2026-11-15", output.getvalue())

    def test_cli_chain_summary(self):
        fake = {"symbol": "SPY", "callExpDateMap": {"2026-12-18:33": {"100.0": [{"bidPrice": 1.25, "askPrice": 1.45, "openInterest": 123}]}}, "putExpDateMap": {}}
        with patch.object(market_data, "get_option_chain", return_value=fake), redirect_stdout(io.StringIO()) as output:
            code = dispatch(build_parser().parse_args(["options", "chain", "SPY", "--limit", "1"]))
        self.assertEqual(code, 0)
        self.assertIn("123", output.getvalue())
        self.assertIn("CALL", output.getvalue())


if __name__ == "__main__":
    unittest.main()

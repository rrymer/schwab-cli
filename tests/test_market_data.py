"""Portable contract/behavior tests for the read-only market-data layer."""
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from schwab_cli import market_data
from schwab_cli.api import SchwabAPIError
from schwab_cli.cli import build_parser, dispatch


class _Response:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return io.BytesIO(json.dumps(self.data).encode())
    def __exit__(self, exc_type, exc, tb):
        return False


class MarketDataTests(unittest.TestCase):
    def test_price_history_request_and_epoch_ms(self):
        seen = {}
        def opener(req, timeout=30):
            seen["request"], seen["timeout"] = req, timeout
            return _Response({"symbol": "AAPL", "candles": [{"close": 1, "datetime": 42}]})
        with patch.object(market_data, "get_access_token", return_value="dummy"), patch.object(market_data, "urlopen", side_effect=opener):
            result = market_data.get_price_history(" aapl ", start=date(2026, 1, 1), end=date(2026, 1, 10))
        self.assertEqual(result["candles"][0]["close"], 1)
        self.assertEqual(seen["timeout"], 30)
        req = seen["request"]
        self.assertEqual(urlparse(req.full_url).path, "/marketdata/v1/pricehistory")
        params = parse_qs(urlparse(req.full_url).query)
        self.assertEqual(params["symbol"], ["AAPL"])
        self.assertEqual(params["frequencyType"], ["daily"])
        self.assertEqual(params["startDate"], ["1767225600000"])
        self.assertEqual(params["endDate"], ["1768003200000"])
        self.assertEqual(req.get_header("Authorization"), "Bearer dummy")

    def test_price_history_invalid_frequencies_fail_before_http(self):
        with patch.object(market_data, "_request") as request:
            for kwargs in ({"period_type": "day", "frequency_type": "daily"},
                           {"period_type": "year", "frequency_type": "minute"},
                           {"period_type": "year", "period": 4},
                           {"frequency": 5},
                           {"start": date(2026, 3, 4), "end": date(2026, 3, 3)}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    market_data.get_price_history("AAPL", **kwargs)
            request.assert_not_called()

    def test_aware_datetime_and_naive_datetime(self):
        self.assertEqual(market_data._epoch_ms(datetime(1970, 1, 1, tzinfo=timezone.utc)), 0)
        with self.assertRaises(ValueError):
            market_data._epoch_ms(datetime(2026, 1, 1))

    def test_expirations_and_nearest(self):
        sample = [{"expirationDate": "2026-11-15", "daysToExpiration": 0},
                  {"expirationDate": "2026-11-20", "daysToExpiration": 5},
                  {"expirationDate": "2026-11-21", "daysToExpiration": 6}]
        with patch.object(market_data, "_request", return_value={"expirationList": sample}) as req:
            self.assertEqual(market_data.nearest_expiration("SPX")["daysToExpiration"], 5)
            self.assertEqual(market_data.nearest_expiration("SPX", include_0dte=True)["daysToExpiration"], 0)
            self.assertEqual(req.call_args.args[0], "/expirationchain")
        with patch.object(market_data, "_request", return_value={"expirationList": []}):
            self.assertIsNone(market_data.nearest_expiration("SPX"))

    def test_chain_parameters(self):
        with patch.object(market_data, "_request", return_value={"callExpDateMap": {}, "putExpDateMap": {}}) as req:
            market_data.get_option_chain("spy", contract_type="PUT",
                                         from_date="2026-12-01", to_date=date(2026, 12, 18),
                                         strike_count=5)
            args, kwargs = req.call_args
            self.assertEqual(args[0], "/chains")
            self.assertEqual(args[1]["symbol"], "SPY")
            self.assertEqual(args[1]["contractType"], "PUT")
            self.assertEqual(args[1]["toDate"], "2026-12-18")
            self.assertEqual(args[1]["strikeCount"], 5)

    def test_bad_chain_inputs(self):
        with patch.object(market_data, "_request") as req:
            for kw in ({"contract_type": "BOGUS"}, {"strike_count": 0},
                       {"from_date": "2026-12-30", "to_date": "2026-12-01"},
                       {"to_date": "not-a-date"}):
                with self.subTest(kw=kw), self.assertRaises(ValueError):
                    market_data.get_option_chain("SPY", **kw)
            req.assert_not_called()

    def test_missing_payload_fields(self):
        with patch.object(market_data, "_request", return_value={}):
            with self.assertRaises(SchwabAPIError):
                market_data.get_price_history("AAPL")
            with self.assertRaises(SchwabAPIError):
                market_data.get_option_expirations("AAPL")
            with self.assertRaises(SchwabAPIError):
                market_data.get_option_chain("AAPL")

    def test_http_errors_do_not_expose_response_body(self):
        err = HTTPError("https://example.invalid", 401, "Unauthorized", {}, io.BytesIO(b"secret account number"))
        with patch.object(market_data, "get_access_token", return_value="dummy"), patch.object(market_data, "urlopen", side_effect=err):
            with self.assertRaises(SchwabAPIError) as raised:
                market_data.get_option_expirations("AAPL")
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("secret account", str(raised.exception))

    def test_cli_parsers(self):
        a = build_parser().parse_args(["market", "history", "AAPL", "--period-type", "year"])
        self.assertEqual(a.symbol, "AAPL")
        b = build_parser().parse_args(["options", "chain", "SPY", "--type", "PUT"])
        self.assertEqual(b.contract_type, "PUT")

    def test_cli_expirations(self):
        with patch("schwab_cli.market_data.get_option_expirations", return_value=[
            {"expirationDate": "2026-11-15", "daysToExpiration": 0},
            {"expirationDate": "2026-11-20", "daysToExpiration": 5},
        ]), redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(dispatch(build_parser().parse_args(["options", "expirations", "SPY"])), 0)
        self.assertIn("2026-11-20", buf.getvalue())
        self.assertNotIn("2026-11-15", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

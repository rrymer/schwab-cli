"""Schwab quote handling without live credentials."""
import unittest
from unittest.mock import patch
from schwab_cli import quotes
from schwab_cli.cli import build_parser


class QuoteTests(unittest.TestCase):
    def test_batch_quotes_uses_single_get_and_deduplicates(self):
        with patch.object(quotes, "_market_get", return_value={
            "AAPL": {"quote": {"lastPrice": 100}}, "SPY": {"quote": {"lastPrice": 500}}
        }) as get:
            result = quotes.get_quotes(["aapl", "SPY", "AAPL"])
        get.assert_called_once_with("/quotes", {"symbols": "AAPL,SPY"})
        self.assertEqual(len(result), 2)

    def test_option_mark_and_underlying_reference(self):
        option = {
            "assetMainType": "OPTION",
            "reference": {"underlying": "SPY"},
            "quote": {"mark": 2.45, "lastPrice": 0, "underlyingPrice": 517.2}
        }
        self.assertEqual(quotes.quote_price(option, option=True), 2.45)
        self.assertEqual(quotes.option_underlying(option), "SPY")
        self.assertEqual(quotes.option_underlying_price(option), 517.2)

    def test_underlying_quote_overrides_option_embedded_underlying(self):
        pos = [{"instrument": {"symbol": "SPY   261120P00500000", "assetType": "OPTION"}}]
        option = {"reference": {"underlying": "SPY"},
                  "quote": {"mark": 3.1, "underlyingPrice": 490}}
        batch = []
        def loader(symbols):
            batch.append(symbols)
            if len(batch) == 1:
                return {pos[0]["instrument"]["symbol"]: option}
            return {"SPY": {"quote": {"lastPrice": 501.23}}}
        result = quotes.prices_for_positions(pos, quote_loader=loader)
        self.assertEqual(result["OPTION:SPY   261120P00500000"], (3.1, 501.23))
        self.assertEqual(len(batch), 2)
        self.assertEqual(batch[1], ["SPY"])

    def test_missing_quote_does_not_invent_price(self):
        pos = [{"instrument": {"symbol": "SPY   261120P00500000", "assetType": "OPTION"}}]
        result = quotes.prices_for_positions(pos, quote_loader=lambda _: {})
        self.assertEqual(result["OPTION:SPY   261120P00500000"], (None, None))
        self.assertIsNone(quotes.quote_price({"quote": {"lastPrice": "not a number"}}))

    def test_equity_prefers_last_and_retains_zero(self):
        result = quotes.quote_price({"quote": {"lastPrice": 0, "mark": 10}})
        self.assertEqual(result, 0)

    def test_cli_include_ids_flags(self):
        parser = build_parser()
        self.assertFalse(parser.parse_args(["positions", "list"]).include_ids)
        self.assertTrue(parser.parse_args(["positions", "list", "--include-ids"]).include_ids)
        self.assertFalse(parser.parse_args(["positions", "monitor"]).include_ids)
        self.assertTrue(parser.parse_args(["positions", "monitor", "--include-ids"]).include_ids)


if __name__ == "__main__":
    unittest.main()

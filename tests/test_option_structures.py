"""Synthetic display-only option strategy classification tests."""
import unittest

from schwab_cli.option_structures import option_codes
from schwab_cli.positions import render_positions
from tests.helpers import position, snapshot


def opt(strike: int, side: str, *, long=0, short=0, expiry="261220", root="SPY"):
    symbol = f"{root:<6}{expiry}{side}{strike * 1000:08d}"
    return position(symbol, "OPTION", long=long, short=short)


def codes(*legs):
    return list(option_codes(list(legs)).values())


class OptionStructureTests(unittest.TestCase):
    def test_single_call_and_put(self):
        self.assertEqual(codes(opt(500, "C", long=1), opt(500, "P", short=1)), ["C", "P"])

    def test_bull_vertical_call_and_put(self):
        self.assertEqual(codes(opt(500, "C", short=2), opt(505, "C", long=2)), ["BV", "BV"])
        self.assertEqual(codes(opt(495, "P", long=1), opt(500, "P", short=1)), ["BV", "BV"])

    def test_bear_vertical_call_and_put(self):
        self.assertEqual(codes(opt(500, "C", long=1), opt(505, "C", short=1)), ["CV", "CV"])
        self.assertEqual(codes(opt(495, "P", short=2), opt(500, "P", long=2)), ["CV", "CV"])

    def test_short_iron_condor_and_iron_fly(self):
        for call_inner in (505, 500):
            with self.subTest(call_inner=call_inner):
                legs = [
                    opt(490, "P", long=1), opt(500, "P", short=1),
                    opt(call_inner, "C", short=1), opt(510, "C", long=1),
                ]
                self.assertEqual(codes(*legs), ["IC"] * 4)

    def test_long_iron_condor(self):
        self.assertEqual(codes(
            opt(490, "P", short=1), opt(500, "P", long=1),
            opt(505, "C", long=1), opt(510, "C", short=1)
        ), ["IC"] * 4)

    def test_unbalanced_different_expirations_and_ambiguous_structures(self):
        self.assertEqual(codes(opt(500, "C", short=2), opt(505, "C", long=1)), ["C", "C"])
        self.assertEqual(codes(opt(500, "C", short=1),
                               opt(505, "C", long=1, expiry="270120")), ["C", "C"])
        self.assertEqual(codes(opt(490, "P", long=1), opt(500, "P", short=1),
                               opt(505, "C", short=2), opt(510, "C", long=2)),
                         ["P", "P", "C", "C"])
        self.assertEqual(codes(opt(500, "P", long=1),
                               opt(500, "C", short=1)), ["P", "C"])

    def test_unrecognized_option_symbol_and_nonoption(self):
        unknown = position("FAKE", "OPTION", long=1)
        self.assertEqual(option_codes([unknown]), {"OPTION:FAKE": "O"})
        self.assertEqual(option_codes([position("SPY", "EQUITY")]), {})

    def test_dashboard_preserves_all_rows_and_ids(self):
        legs = [opt(490, "P", long=1), opt(500, "P", short=1),
                opt(500, "C", short=1), opt(510, "C", long=1)]
        account = snapshot(*legs)
        identities = {f'OPTION:{x["instrument"]["symbol"]}': f"id-{i}"
                      for i, x in enumerate(legs)}
        view = render_positions(account, identities, {}, include_ids=True)
        self.assertEqual(view.count("IC"), 4)
        for index in range(4):
            self.assertIn(f"id-{index}", view)


if __name__ == "__main__":
    unittest.main()

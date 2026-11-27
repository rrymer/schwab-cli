"""CLI route tests: no credentials, network or database."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from schwab_cli.cli import build_parser, dispatch
from schwab_cli.tradeplans.loader import load_trade_plan


class PlanCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "plan.yaml"
        self.path.write_text("version: 1\nstrategy:\n  name: example\n", encoding="utf-8")

    def test_plan_flag_and_preview_dispatch(self):
        args = build_parser().parse_args(["positions", "add", "--plan", str(self.path)])
        out = io.StringIO()
        with (patch("schwab_cli.orders.submit_order") as submit,
              patch("schwab_cli.orders.PositionStore") as store,
              redirect_stdout(out)):
            self.assertEqual(dispatch(args), 0)
        self.assertIn("not compiled or validated", out.getvalue())
        self.assertIn("DRY RUN", out.getvalue())
        submit.assert_not_called()
        store.assert_not_called()

    def test_live_plan_is_blocked(self):
        args = build_parser().parse_args([
            "positions", "add", "--plan", str(self.path), "--submit"
        ])
        with patch("schwab_cli.orders.submit_order") as submit:
            with self.assertRaisesRegex(ValueError, "disabled"):
                dispatch(args)
        submit.assert_not_called()

    def test_mixed_positional_or_single_order_settings_rejected(self):
        cases = [
            ["AAPL", "BUY", "1"],
            ["--price", "5"],
            ["--order-type", "MARKET"],
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                args = build_parser().parse_args([
                    "positions", "add", "--plan", str(self.path), *extra
                ])
                with self.assertRaisesRegex(Exception, "cannot be combined"):
                    dispatch(args)

    def test_incomplete_single_order_is_user_error(self):
        args = build_parser().parse_args(["positions", "add", "AAPL"])
        with self.assertRaisesRegex(Exception, "requires"):
            dispatch(args)

    def test_bad_yaml_and_non_mapping_rejected(self):
        for document in ("[1, 2]\n", "[]\n", "x: [broken\n", "!!python/object/apply:os.system ['exit 1']\n"):
            with self.subTest(document=document):
                self.path.write_text(document, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_trade_plan(self.path)

    def test_yaml_file_missing(self):
        with self.assertRaises(FileNotFoundError):
            load_trade_plan(self.path.parent / "missing.yaml")

    def test_legacy_single_leg_arguments_preserved(self):
        args = build_parser().parse_args([
            "positions", "add", "AAPL", "BUY", "2", "--price", "150"
        ])
        self.assertIsNone(args.plan)
        self.assertEqual(args.symbol, "AAPL")
        self.assertEqual(args.quantity, 2.0)


if __name__ == "__main__":
    unittest.main()

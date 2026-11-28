"""Plan CLI integration tests; the broker and local order store stay untouched."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from schwab_cli.cli import CommandError, build_parser, dispatch


VALID = """
strategy:
  name: test_strategy
  version: 1
  symbols:
    spy: {source: "BACKTEST:SPY", timeframe: "1h"}
plan:
  id: test_plan
  entry: entry
  blocks:
    entry:
      when: true
      then: {action: BUY, target: spy, sizing: 1, next: done}
    done:
      when: {op: "*"}
      then: {action: STOP}
"""


class PlanCliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "workflow.yaml"
        self.path.write_text(VALID, encoding="utf-8")

    def args(self, *extra):
        return build_parser().parse_args(["positions", "add", "--plan", str(self.path), *extra])

    def test_preview_compiles_validates_without_orders_or_credentials(self):
        output = io.StringIO()
        with (patch("schwab_cli.orders.submit_order") as submit,
              patch("schwab_cli.orders.PositionStore") as store,
              patch("schwab_cli.orders.get_access_token") as credentials,
              redirect_stdout(output)):
            self.assertEqual(dispatch(self.args()), 0)
        self.assertIn('"status": "compiled and graph-validated; not executable"', output.getvalue())
        self.assertIn('"steps": [', output.getvalue())
        self.assertIn("DRY RUN", output.getvalue())
        submit.assert_not_called()
        store.assert_not_called()
        credentials.assert_not_called()

    def test_live_submission_rejected_before_any_broker_call(self):
        with patch("schwab_cli.orders.submit_order") as submit:
            with self.assertRaisesRegex(ValueError, "disabled"):
                dispatch(self.args("--submit"))
        submit.assert_not_called()

    def test_mixed_order_arguments_rejected(self):
        for extra in (("AAPL", "BUY", "1"), ("--price", "5"), ("--order-type", "MARKET")):
            with self.subTest(extra=extra), self.assertRaises(CommandError):
                dispatch(self.args(*extra))

    def test_incomplete_single_leg_is_rejected(self):
        with self.assertRaises(CommandError):
            dispatch(build_parser().parse_args(["positions", "add", "AAPL"]))

    def test_invalid_plan_fails_validation(self):
        self.path.write_text(VALID.replace("next: done", "next: unknown"), encoding="utf-8")
        with patch("schwab_cli.orders.submit_order") as submit:
            with self.assertRaises(ValueError):
                dispatch(self.args())
        submit.assert_not_called()

    def test_old_single_leg_cli_still_parses(self):
        args = build_parser().parse_args(
            ["positions", "add", "AAPL", "BUY", "2", "--price", "100"]
        )
        self.assertEqual((args.symbol, args.instruction, args.quantity), ("AAPL", "BUY", 2.0))
        self.assertIsNone(args.plan)


if __name__ == "__main__":
    unittest.main()

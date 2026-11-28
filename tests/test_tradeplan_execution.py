"""Credential-free tests of guarded TradePlan order submission."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from schwab_cli.tradeplans.execution import (
    DuplicatePlanAction, PlanExecutionError, submit_plan_entry,
)
from schwab_cli.tradeplans.loader import YamlDocumentLoader


YAML = """
strategy:
  name: test
  version: 1
  symbols:
    spy: {source: "SCHWAB:SPY", timeframe: "quote"}
plan:
  id: live_test
  entry: enter
  blocks:
    enter:
      when: {op: "*"}
      then: {action: BUY, target: spy, sizing: 2, next: stop}
    stop:
      when: {op: "*"}
      then: {action: STOP}
"""


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name) / "orders.sqlite3"
        self.plan = YamlDocumentLoader().load(YAML)
        self.sent = []
        self.send = lambda order: self.sent.append(order.to_create_payload()) or (
            "https://api.schwabapi.com/trader/v1/accounts/test/orders/123"
        )
        self.options = dict(
            confirm_plan_id="live_test", limit_price=500,
            max_notional=1500, journal_path=self.journal,
            snapshot_loader=lambda _: {}, quote_loader=lambda _: 500,
            sender=self.send,
        )

    def test_submit_once_and_record_acceptance_without_claiming_fill(self):
        result = submit_plan_entry(self.plan, **self.options)
        self.assertEqual(result.status, "accepted_not_filled")
        self.assertEqual(len(self.sent), 1)
        order = self.sent[0]
        self.assertEqual(order["orderType"], "LIMIT")
        self.assertEqual(order["price"], 500)
        self.assertEqual(order["orderLegCollection"][0]["instruction"], "BUY")
        with sqlite3.connect(self.journal) as db:
            self.assertEqual(db.execute("SELECT state FROM plan_submissions").fetchone()[0], "accepted")
        with self.assertRaises(DuplicatePlanAction):
            submit_plan_entry(self.plan, **self.options)
        self.assertEqual(len(self.sent), 1)

    def test_unknown_submission_outcome_never_retries(self):
        def failing_sender(_):
            raise ConnectionError("Simulated unknown POST outcome")
        options = dict(self.options, sender=failing_sender)
        with self.assertRaises(ConnectionError):
            submit_plan_entry(self.plan, **options)
        with sqlite3.connect(self.journal) as db:
            self.assertEqual(db.execute("SELECT state FROM plan_submissions").fetchone()[0],
                             "outcome_unknown")
        with self.assertRaises(DuplicatePlanAction):
            submit_plan_entry(self.plan, **self.options)
        self.assertEqual(self.sent, [])

    def test_condition_false_never_claims(self):
        plan = YamlDocumentLoader().load(
            YAML.replace('when: {op: "*"}',
                         'when: {op: ">", args: [{ref: spy.close}, 600]}', 1)
        )
        options = dict(self.options, snapshot_loader=lambda _: {"spy.close": 500})
        self.assertEqual(submit_plan_entry(plan, **options).status, "condition_false")
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.sent, [])

    def test_confirmation_and_notional_are_required(self):
        with self.assertRaisesRegex(PlanExecutionError, "confirm-plan-id"):
            submit_plan_entry(self.plan, **dict(self.options, confirm_plan_id="wrong"))
        with self.assertRaisesRegex(PlanExecutionError, "notional"):
            submit_plan_entry(self.plan, **dict(self.options, max_notional=999))
        self.assertEqual(self.sent, [])

    def test_limit_price_guard_and_integer_quantity(self):
        with self.assertRaisesRegex(PlanExecutionError, "5%"):
            submit_plan_entry(self.plan, **dict(self.options, limit_price=530))
        plan = YamlDocumentLoader().load(YAML.replace("sizing: 2", "sizing: 2.5"))
        with self.assertRaisesRegex(PlanExecutionError, "integer"):
            submit_plan_entry(plan, **self.options)
        self.assertEqual(self.sent, [])

    def test_candle_timeframe_not_accepted_for_live_submission(self):
        plan = YamlDocumentLoader().load(YAML.replace('timeframe: "quote"',
                                                       'timeframe: "1h"'))
        with self.assertRaisesRegex(PlanExecutionError, "timeframe: quote"):
            submit_plan_entry(plan, **self.options)

    def test_nonlive_source_rejected(self):
        plan = YamlDocumentLoader().load(YAML.replace("SCHWAB:SPY", "BACKTEST:SPY"))
        with self.assertRaisesRegex(PlanExecutionError, "SCHWAB"):
            submit_plan_entry(plan, **self.options)

    @patch("schwab_cli.tradeplans.execution._available_shares", return_value=3.0)
    def test_close_sells_entire_long_equity_position(self, shares):
        plan = YamlDocumentLoader().load(YAML.replace(
            "action: BUY, target: spy, sizing: 2",
            "action: CLOSE, target: spy"))
        submit_plan_entry(plan, **self.options)
        leg = self.sent[0]["orderLegCollection"][0]
        self.assertEqual((leg["instruction"], leg["quantity"]), ("SELL", 3.0))
        shares.assert_called_once_with("SPY")

    @patch("schwab_cli.tradeplans.execution._available_shares", return_value=0.0)
    def test_close_without_long_position_is_rejected(self, shares):
        plan = YamlDocumentLoader().load(YAML.replace(
            "action: BUY, target: spy, sizing: 2",
            "action: CLOSE, target: spy"))
        with self.assertRaisesRegex(PlanExecutionError, "positive integral"):
            submit_plan_entry(plan, **self.options)
        self.assertEqual(self.sent, [])

    @patch("schwab_cli.tradeplans.execution._available_shares", return_value=1)
    def test_sell_cannot_open_short(self, shares):
        plan = YamlDocumentLoader().load(YAML.replace("action: BUY", "action: SELL"))
        with self.assertRaisesRegex(PlanExecutionError, "Insufficient"):
            submit_plan_entry(plan, **self.options)
        shares.assert_called_once_with("SPY")
        self.assertEqual(self.sent, [])

    def test_submission_default_snapshot_requires_fresh_quotes(self):
        options = dict(self.options)
        options.pop("snapshot_loader")
        with patch("schwab_cli.tradeplans.execution.schwab_snapshot",
                   return_value={}) as snapshot:
            submit_plan_entry(self.plan, **options)
        snapshot.assert_called_once_with(self.plan, require_fresh=True)

    def test_fresh_mark_accepted_and_old_quote_rejected(self):
        from schwab_cli.tradeplans.monitor import PlanConditionError, fresh_quote_price
        quote = {"quote": {"mark": 500, "quoteTimeInLong": 1_999}}
        with patch("schwab_cli.tradeplans.monitor.time.time", return_value=2):
            self.assertEqual(fresh_quote_price(quote), 500)
        with patch("schwab_cli.tradeplans.monitor.time.time", return_value=1_000):
            with self.assertRaises(PlanConditionError):
                fresh_quote_price(quote)

    def test_fresh_quote_fails_when_time_missing(self):
        from schwab_cli.tradeplans.execution import _fresh_quote
        with patch("schwab_cli.quotes.get_quotes", return_value={
            "SPY": {"quote": {"lastPrice": 500}}
        }):
            with self.assertRaisesRegex(PlanExecutionError, "fresh"):
                _fresh_quote("SPY")


if __name__ == "__main__":
    unittest.main()

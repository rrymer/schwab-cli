"""Network-free unit tests for TradePlan monitoring and condition evaluation."""
import unittest
from unittest.mock import patch

from schwab_cli.tradeplans.contracts import Literal, Operation, Reference
from schwab_cli.tradeplans.loader import YamlDocumentLoader
from schwab_cli.tradeplans.monitor import (
    PlanConditionError, SnapshotConditionEvaluator, observe_step, schwab_snapshot,
)


PLAN = """
strategy:
  name: demo
  version: 1
  symbols:
    spy: {source: "SCHWAB:SPY", timeframe: "1h"}
plan:
  id: demo_plan
  entry: enter
  blocks:
    enter:
      when: {op: ">", args: [{ref: spy.close}, 500]}
      then: {action: BUY, target: spy, sizing: 1, next: stop}
    stop:
      when: {op: "*"}
      then: {action: STOP}
"""


class ConditionTests(unittest.TestCase):
    def setUp(self):
        self.plan = YamlDocumentLoader().load(PLAN)
        self.evaluate = SnapshotConditionEvaluator().evaluate

    def test_triggers_only_on_true_condition(self):
        self.assertFalse(observe_step(self.plan, {"spy.close": 499}).triggered)
        decision = observe_step(self.plan, {"spy.close": 501})
        self.assertTrue(decision.triggered)
        self.assertEqual((decision.action, decision.next_step), ("BUY", "stop"))

    def test_terminal_wildcard_is_true(self):
        result = observe_step(self.plan, {}, step_name="stop")
        self.assertTrue(result.triggered)
        self.assertEqual((result.action, result.next_step), ("STOP", None))

    def test_operators(self):
        self.assertEqual(self.evaluate(Operation("+", (Literal(2), Literal(3))), {}), 5)
        self.assertEqual(self.evaluate(Operation("*", (Literal(4), Literal(5))), {}), 20)
        self.assertTrue(self.evaluate(Operation("AND", (Literal(True), Literal(True))), {}))
        self.assertTrue(self.evaluate(Operation("NOT", (Literal(False),)), {}))

    def test_missing_and_bad_inputs_fail_closed(self):
        values = (
            (Reference("missing"), {}),
            (Reference("x"), {"x": None}),
            (Operation(">", (Reference("x"), Literal(0))), {"x": float("nan")}),
            (Operation("/", (Literal(1), Literal(0))), {}),
            (Operation("+", (Literal(True), Literal(1))), {}),
            (Operation("AND", (Literal(1), Literal(True))), {}),
        )
        for expression, context in values:
            with self.subTest(expression=expression), self.assertRaises(PlanConditionError):
                self.evaluate(expression, context)

    @patch("schwab_cli.quotes.get_quotes")
    def test_live_quote_snapshot(self, quotes):
        quotes.return_value = {"SPY": {"quote": {"lastPrice": 510.25}}}
        self.assertEqual(schwab_snapshot(self.plan), {"spy.close": 510.25})
        quotes.assert_called_once_with(["SPY"])

    @patch("schwab_cli.quotes.get_quotes")
    def test_missing_quote_does_not_trigger(self, quotes):
        quotes.return_value = {}
        with self.assertRaises(PlanConditionError):
            schwab_snapshot(self.plan)

    @patch("schwab_cli.api.get_positions")
    def test_live_position_snapshot(self, positions):
        positions.return_value = [{"securitiesAccount": {"positions": [
            {"instrument": {"symbol": "SPY"}, "longQuantity": 7, "shortQuantity": 2}
        ]}}]
        plan = YamlDocumentLoader().load(PLAN.replace("{ref: spy.close}", "{ref: POSITION.spy}"))
        self.assertEqual(schwab_snapshot(plan), {"POSITION.spy": 5})

    def test_backtest_never_substitutes_live_quote(self):
        plan = YamlDocumentLoader().load(PLAN.replace("SCHWAB:SPY", "BACKTEST:SPY"))
        with self.assertRaisesRegex(PlanConditionError, "requires SCHWAB"):
            schwab_snapshot(plan)


if __name__ == "__main__":
    unittest.main()

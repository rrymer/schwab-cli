"""Typed qkt-inspired action semantics, entirely offline."""
import unittest
from dataclasses import FrozenInstanceError

from schwab_cli.tradeplans.contracts import Action, Literal, Operation, Reference
from schwab_cli.tradeplans.loader import YamlDocumentLoader
from schwab_cli.tradeplans.semantics import (
    ActionCompiler, BracketSpec, Buy, Cancel, CancelAll, Close, CloseAll,
    ExpressionCompiler, Flatten, Log, OcoEntry, OpenOptions, OrderSpec,
    Resize, Sell, SizingSpec, Stop, Trade, TradePlanSemanticError,
)


class ConcreteActionTests(unittest.TestCase):
    def setUp(self):
        self.compiler = ActionCompiler()
        self.aliases = {"spy", "qqq"}

    def compile(self, **values):
        return self.compiler.compile(values, self.aliases)

    def test_base_action_and_trade_are_abstract(self):
        with self.assertRaises(TypeError):
            Action()
        with self.assertRaises(TypeError):
            Trade(target="spy")
        self.assertIsInstance(self.compile(action="BUY", target="spy", sizing=1), Action)

    def test_buy_sell_and_close_have_typed_parameters(self):
        buy = self.compile(action="buy", target="spy", sizing=2)
        sell = self.compile(action="sell", target="spy", sizing={"unit": "USD", "value": 1500})
        close = self.compile(action="close", target="spy")
        self.assertIsInstance(buy, Buy)
        self.assertIsInstance(sell, Sell)
        self.assertIsInstance(close, Close)
        self.assertEqual([buy.verb, sell.verb, close.verb], ["BUY", "SELL", "CLOSE"])
        self.assertEqual(buy.arguments, {"sizing": 2})
        self.assertEqual(sell.sizing, SizingSpec(Literal(1500), "USD"))
        with self.assertRaises(FrozenInstanceError):
            buy.target = "qqq"

    def test_qkt_order_variants_and_tif(self):
        cases = (
            ({"type": "MARKET"}, "MARKET"),
            ({"type": "LIMIT", "limit_price": {"ref": "spy.ask"}, "tif": "DAY"}, "LIMIT"),
            ({"type": "STOP", "stop_price": 612}, "STOP"),
            ({"type": "STOP_LIMIT", "stop_price": 612, "limit_price": 613}, "STOP_LIMIT"),
            ({"type": "TRAILING_BY", "trail": 4}, "TRAILING_BY"),
            ({"type": "TRAILING_PCT", "trail": 2}, "TRAILING_PCT"),
            ({"type": "LIMIT", "limit_price": 500, "tif": "GTD",
              "until": {"ref": "NOW.timestamp"}}, "LIMIT"),
        )
        for order_data, kind in cases:
            with self.subTest(order_data=order_data):
                trade = self.compile(action="BUY", target="spy", sizing=1,
                                     order=order_data)
                self.assertIsInstance(trade.order, OrderSpec)
                self.assertEqual(trade.order.type, kind)
                if kind == "LIMIT":
                    self.assertIsInstance(trade.order.limit_price, (Reference, Literal))

    def test_invalid_order_fields_fail_during_compilation(self):
        invalid = (
            {"type": "LIMIT"},
            {"type": "MARKET", "limit_price": 500},
            {"type": "STOP_LIMIT", "stop_price": 500},
            {"type": "STOP", "stop_price": -4},
            {"type": "LIMIT", "limit_price": 500, "tif": "GTD"},
            {"type": "LIMIT", "limit_price": 500, "tif": "DAY", "until": 123},
            {"type": "MAGIC"},
            {"type": "MARKET", "unknown": 1},
            {"type": "TRAILING_PCT", "trail": 0},
        )
        for order in invalid:
            with self.subTest(order=order), self.assertRaises(TradePlanSemanticError):
                self.compile(action="BUY", target="spy", sizing=1, order=order)

    def test_bracket_requires_both_exits(self):
        buy = self.compile(action="BUY", target="spy", sizing=1,
                           bracket={"stop_loss": {"ref": "spy.low"},
                                    "take_profit": {"ref": "spy.high"}})
        self.assertIsInstance(buy.bracket, BracketSpec)
        with self.assertRaises(TradePlanSemanticError):
            self.compile(action="BUY", target="spy", sizing=1,
                         bracket={"stop_loss": 500})

    def test_resize_cancel_all_flatten_and_log(self):
        instances = (
            ({"action": "RESIZE", "target": "spy", "sizing": 5,
              "min_step": 2}, Resize),
            ({"action": "CANCEL", "target": "spy"}, Cancel),
            ({"action": "CANCEL_ALL"}, CancelAll),
            ({"action": "CLOSE_ALL"}, CloseAll),
            ({"action": "FLATTEN"}, Flatten),
            ({"action": "LOG", "message": "hello", "level": "WARN"}, Log),
            ({"action": "STOP"}, Stop),
        )
        for source, expected in instances:
            with self.subTest(source=source):
                self.assertIsInstance(self.compile(**source), expected)

    def test_oco_entry_requires_exactly_two_entries(self):
        action = self.compile(action="OCO_ENTRY", entries=[
            {"action": "BUY", "target": "spy", "sizing": 2,
             "order": {"type": "STOP", "stop_price": 650}},
            {"action": "SELL", "target": "spy", "sizing": 2,
             "order": {"type": "STOP", "stop_price": 550}},
        ])
        self.assertIsInstance(action, OcoEntry)
        self.assertEqual(tuple(x.verb for x in action.entries), ("BUY", "SELL"))
        for entries in ([], [{"action": "BUY", "target": "spy", "sizing": 1}]):
            with self.assertRaises(TradePlanSemanticError):
                self.compile(action="OCO_ENTRY", entries=entries)

    def test_options_structure_is_typed_but_not_a_broker_order(self):
        action = self.compile(action="OPEN", target="ps", venue="SCHWAB",
                              root="SPX", sizing={"unit": "PCT_RISK", "value": 1},
                              legs=[
                                  {"side": "SELL", "option_type": "PUT", "delta": .25,
                                   "dte_min": 30, "dte_max": 45},
                                  {"side": "BUY", "option_type": "PUT", "delta": .10,
                                   "same_expiry": True},
                              ])
        self.assertIsInstance(action, OpenOptions)
        self.assertEqual(action.legs[1].same_expiry, True)
        with self.assertRaises(TradePlanSemanticError):
            self.compile(action="OPEN", target="ps", venue="SCHWAB", root="SPX",
                         sizing=1, legs=[{"side": "BUY", "option_type": "PUT",
                                         "delta": 2, "same_expiry": True}])

    def test_invalid_actions_fail_closed(self):
        invalid = (
            {"action": "MAGIC"},
            {"action": "BUY", "target": "missing", "sizing": 1},
            {"action": "BUY", "target": "spy"},
            {"action": "BUY", "target": "spy", "sizing": -1},
            {"action": "CLOSE", "target": "spy", "sizing": 1},
            {"action": "STOP", "message": "bad"},
            {"action": "LOG", "message": "ok", "level": "TRACE"},
        )
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(TradePlanSemanticError):
                self.compile(**item)

    def test_full_plan_preserves_expression_typed_order(self):
        plan = YamlDocumentLoader().load("""
strategy:
  name: order_demo
  version: 1
  symbols:
    spy: {source: "BACKTEST:SPY", timeframe: "1h"}
plan:
  id: example
  entry: open
  blocks:
    open:
      when: {op: ">", args: [{ref: spy.close}, 600]}
      then:
        action: BUY
        target: spy
        sizing: {unit: USD, value: 1000}
        order:
          type: LIMIT
          limit_price: {ref: spy.close}
          tif: DAY
        next: done
    done:
      when: {op: "*"}
      then: {action: STOP}
""")
        trade = plan.steps[0].actions[0]
        self.assertIsInstance(trade, Buy)
        self.assertEqual(trade.order.limit_price, Reference("spy.close"))
        self.assertEqual(plan.strategy.rules[0].actions[0], trade)
        self.assertIsInstance(plan.strategy.rules[0].condition, Operation)


if __name__ == "__main__":
    unittest.main()

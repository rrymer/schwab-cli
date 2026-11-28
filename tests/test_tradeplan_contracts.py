"""Contract-only tests: no network, accounts, broker or DSL parser needed."""
import unittest
from abc import ABC
from dataclasses import FrozenInstanceError

from schwab_cli.tradeplans.semantics import Buy, SizingSpec

from schwab_cli.tradeplans.contracts import (
    Action, ActionExecutor, ConditionEvaluator, DocumentLoader,
    Expression, GraphValidator, Literal, Operation, PlanCompiler, PlanStep,
    Reference, Rule, SafeguardEvaluator, SemanticInterpreter, Strategy,
    Stream, TradePlan, Transition,
)


def sample():
    stream = Stream("asset", "BACKTEST:SPY", "1h")
    condition = Operation("AND", (
        Operation(">", (Reference("asset.close"), Literal(100))),
        Operation("=", (Reference("POSITION.asset"), Literal(0))),
    ))
    action = Buy(target="asset", sizing=SizingSpec(Literal(1)))
    strategy = Strategy("test", 1, (stream,), (Rule(condition, (action,)),))
    plan = TradePlan("plan-1", strategy, "enter", (
        PlanStep("enter", (action,), (Transition("END"),)),
        PlanStep("END", terminal=True),
    ))
    return strategy, plan


class TradePlanContractTests(unittest.TestCase):
    def test_abcs_are_not_instantiable(self):
        for cls in (Expression, Action, DocumentLoader, SemanticInterpreter,
                    PlanCompiler, GraphValidator, ConditionEvaluator,
                    SafeguardEvaluator, ActionExecutor):
            with self.subTest(cls=cls.__name__), self.assertRaises(TypeError):
                cls()

    def test_expression_tree_collects_references_without_evaluation(self):
        strategy, _ = sample()
        refs = strategy.rules[0].condition.references()
        self.assertEqual(refs, frozenset({"asset.close", "POSITION.asset"}))
        self.assertEqual(Literal(None).references(), frozenset())
        with self.assertRaises(TypeError):
            Operation("AND", (Literal(True), "not an expression"))

    def test_strategy_rule_and_action_validation(self):
        strategy, plan = sample()
        self.assertEqual(strategy.rules[0].actions[0].verb, "BUY")
        self.assertEqual(plan.steps[0].transitions[0].destination, "END")
        with self.assertRaises(ValueError):
            Rule(Literal(True), ())
        with self.assertRaises(ValueError):
            Strategy("bad", 0, strategy.streams)
        with self.assertRaises(ValueError):
            Strategy("bad", 1, ())
        with self.assertRaises(ValueError):
            Reference(" ")
        with self.assertRaises(ValueError):
            Buy(target=" ", sizing=SizingSpec(Literal(1)))

    def test_models_are_frozen_at_top_level(self):
        strategy, plan = sample()
        with self.assertRaises(FrozenInstanceError):
            strategy.name = "mutated"
        with self.assertRaises(FrozenInstanceError):
            plan.entry = "changed"

    def test_interpreter_and_validator_can_be_implemented_separately(self):
        class StubLoader(DocumentLoader):
            def load(self, source):
                return {"source": source}

        class StubInterpreter(SemanticInterpreter):
            def interpret(self, document):
                self.last_document = document
                return sample()[0]

        class StubCompiler(PlanCompiler):
            def compile(self, strategy, document):
                return sample()[1]

        class StubValidator(GraphValidator):
            def validate(self, plan):
                if plan.entry not in {step.identifier for step in plan.steps}:
                    raise ValueError("Unknown entry")

        loader = StubLoader()
        interpreter = StubInterpreter()
        source = loader.load("document")
        strategy = interpreter.interpret(source)
        plan = StubCompiler().compile(strategy, source)
        self.assertEqual(interpreter.last_document, {"source": "document"})
        self.assertIsNone(StubValidator().validate(plan))


if __name__ == "__main__":
    unittest.main()

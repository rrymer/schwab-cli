"""Pure TradePlan predicate evaluation and read-only market snapshots.

No order submission, persistence or credentials are accessed by the evaluator.
A separate adapter fetches the explicitly supported Schwab data references.
"""
from __future__ import annotations

import math
import operator
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ConditionEvaluator, Expression, Literal, Operation, Reference, TradePlan


class PlanConditionError(ValueError):
    """A condition or its input snapshot cannot be evaluated safely."""


_ARITHMETIC = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}
_COMPARE = {"=": operator.eq, "==": operator.eq, "!=": operator.ne,
            "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


class SnapshotConditionEvaluator(ConditionEvaluator):
    """Evaluate the supported, side-effect-free typed expression subset.

    Missing values and unsupported operator/type combinations fail closed.
    """

    def evaluate(self, expression: Expression, context: Mapping[str, Any]) -> Any:
        if isinstance(expression, Literal):
            return expression.value
        if isinstance(expression, Reference):
            if expression.name not in context:
                raise PlanConditionError(f"Missing condition reference: {expression.name}")
            value = context[expression.name]
            if value is None or (type(value) is float and not math.isfinite(value)):
                raise PlanConditionError(f"Invalid condition reference: {expression.name}")
            if type(value) not in (str, bool, int, float):
                raise PlanConditionError(f"Unsupported reference type: {expression.name}")
            return value
        if not isinstance(expression, Operation):
            raise PlanConditionError("Unsupported expression type")
        op = expression.name.upper()
        if op == "*" and len(expression.operands) == 0:
            return True
        values = [self.evaluate(item, context) for item in expression.operands]
        if op == "NOT":
            if len(values) != 1 or type(values[0]) is not bool:
                raise PlanConditionError("NOT requires one boolean operand")
            return not values[0]
        if op in ("AND", "OR"):
            if len(values) != 2 or any(type(v) is not bool for v in values):
                raise PlanConditionError(f"{op} requires two boolean operands")
            return all(values) if op == "AND" else any(values)
        if len(values) != 2:
            raise PlanConditionError(f"{op} requires two operands")
        if op in _COMPARE:
            if any(type(v) is bool for v in values):
                if op not in ("=", "==", "!=") or any(type(v) is not bool for v in values):
                    raise PlanConditionError("Boolean comparison requires two booleans")
            elif not (all(type(v) in (int, float) for v in values) or
                      all(type(v) is str for v in values)):
                raise PlanConditionError("Incompatible comparison types")
            result = _COMPARE[op](*values)
            return bool(result)
        if op in _ARITHMETIC:
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise PlanConditionError("Arithmetic requires finite numeric operands")
            try:
                result = _ARITHMETIC[op](*values)
            except (ZeroDivisionError, OverflowError) as exc:
                raise PlanConditionError("Arithmetic failed") from exc
            if not math.isfinite(result):
                raise PlanConditionError("Non-finite arithmetic result")
            return result
        raise PlanConditionError(f"Unsupported operator: {op}")


@dataclass(frozen=True)
class PlanObservation:
    plan_id: str
    step: str
    triggered: bool
    action: str
    next_step: str | None


def observe_step(plan: TradePlan, context: Mapping[str, Any], *,
                 step_name: str | None = None,
                 evaluator: ConditionEvaluator | None = None) -> PlanObservation:
    """Evaluate exactly one plan step; never advance it or execute the action."""
    name = step_name or plan.entry
    steps = {step.identifier: step for step in plan.steps}
    if name not in steps:
        raise PlanConditionError(f"Unknown plan step: {name}")
    index = next(i for i, step in enumerate(plan.steps) if step.identifier == name)
    if index >= len(plan.strategy.rules):
        raise PlanConditionError("Plan is missing a condition for its step")
    step = steps[name]
    if len(step.actions) != 1:
        raise PlanConditionError("Expected exactly one action per step")
    evaluator = evaluator or SnapshotConditionEvaluator()
    outcome = evaluator.evaluate(plan.strategy.rules[index].condition, context)
    if type(outcome) is not bool:
        raise PlanConditionError(f"WHEN in {name} did not evaluate to a boolean")
    return PlanObservation(plan.identifier, name, outcome, step.actions[0].verb,
                           step.transitions[0].destination if step.transitions else None)


def schwab_snapshot(plan: TradePlan) -> dict[str, float]:
    """Fetch only explicitly supported live references, never infer unknown streams.

    SCHWAB:<symbol> provides a quote price under <alias>.close.
    POSITION.<alias> is net shares from a successful single-account snapshot.
    BACKTEST sources cannot be silently replaced by live market data.
    """
    from ..api import get_positions
    from ..quotes import get_quotes, quote_price

    references = set().union(*(rule.condition.references() for rule in plan.strategy.rules))
    streams = {stream.alias: stream for stream in plan.strategy.streams}
    result: dict[str, float] = {}
    quote_symbols: dict[str, str] = {}
    needs_positions = False
    for ref in references:
        if ref.startswith("POSITION."):
            alias = ref.removeprefix("POSITION.")
            if alias not in streams:
                raise PlanConditionError(f"Unknown position stream: {alias}")
            needs_positions = True
        elif ref.endswith(".close"):
            alias = ref[:-len(".close")]
            if alias not in streams:
                raise PlanConditionError(f"Unknown quote stream: {alias}")
            source = streams[alias].source
            if not source.startswith("SCHWAB:") or not source.removeprefix("SCHWAB:"):
                raise PlanConditionError(f"Live quote requires SCHWAB:<symbol> source: {alias}")
            quote_symbols[alias] = source.removeprefix("SCHWAB:").upper()
        else:
            raise PlanConditionError(f"Unsupported live reference: {ref}")
    if needs_positions:
        accounts = get_positions()
        if len(accounts) != 1 or not isinstance(accounts[0].get("securitiesAccount"), dict):
            raise PlanConditionError("Expected exactly one complete Schwab account snapshot")
        holdings = accounts[0]["securitiesAccount"].get("positions") or []
        by_symbol: dict[str, float] = {}
        for holding in holdings:
            instrument = holding.get("instrument") or {}
            symbol = instrument.get("symbol")
            if not isinstance(symbol, str):
                raise PlanConditionError("Position snapshot lacks instrument symbol")
            long_qty, short_qty = holding.get("longQuantity", 0), holding.get("shortQuantity", 0)
            if any(type(q) not in (int, float) or not math.isfinite(q) for q in (long_qty, short_qty)):
                raise PlanConditionError("Position snapshot has invalid quantities")
            by_symbol[symbol.upper()] = by_symbol.get(symbol.upper(), 0) + long_qty - short_qty
        for ref in references:
            if ref.startswith("POSITION."):
                alias = ref.removeprefix("POSITION.")
                stream = streams[alias]
                if not stream.source.startswith("SCHWAB:"):
                    raise PlanConditionError(f"Live position requires SCHWAB:<symbol> source: {alias}")
                symbol = stream.source.removeprefix("SCHWAB:").upper()
                result[ref] = by_symbol.get(symbol, 0.0)
    if quote_symbols:
        quotes = get_quotes(list(dict.fromkeys(quote_symbols.values())))
        for alias, symbol in quote_symbols.items():
            price = quote_price(quotes.get(symbol))
            if price is None or not math.isfinite(price) or price <= 0:
                raise PlanConditionError(f"No valid live quote for {symbol}")
            result[f"{alias}.close"] = price
    return result

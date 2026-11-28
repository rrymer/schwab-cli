"""Typed qkt-inspired YAML semantic models and finite TradePlan compiler.

Action and Expression are abstract contracts in contracts.py; concrete language
semantics live here. These are not the upstream Kotlin qkt compiler or AST.
No class in this module places orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, ClassVar, Mapping

from .contracts import (
    Action, Expression, GraphValidator, Literal, Operation, PlanCompiler,
    PlanStep, Reference, Rule, SemanticInterpreter, Strategy, Stream,
    TradePlan, Transition,
)


class TradePlanSemanticError(ValueError):
    """Unsupported or inconsistent TradePlan semantic content."""


@dataclass(frozen=True)
class SizingSpec:
    """qkt SIZING: an expression plus its unit/basis, resolved at execution."""
    value: Expression
    unit: str = "UNITS"

    def __post_init__(self) -> None:
        if self.unit not in {"UNITS", "USD", "PCT_EQUITY", "PCT_BALANCE", "PCT_RISK", "RISK_USD", "POSITION"}:
            raise TradePlanSemanticError(f"Unsupported sizing unit: {self.unit}")
        if not isinstance(self.value, Expression):
            raise TradePlanSemanticError("Sizing value must be an Expression")
        if isinstance(self.value, Literal) and type(self.value.value) in (int, float):
            if not isfinite(self.value.value) or self.value.value <= 0:
                raise TradePlanSemanticError("Sizing must be finite and positive")


@dataclass(frozen=True)
class OrderSpec:
    """qkt order clauses; price expressions are evaluated when the rule fires."""
    type: str = "MARKET"
    limit_price: Expression | None = None
    stop_price: Expression | None = None
    trail: Expression | None = None
    tif: str = "GTC"
    until: Expression | None = None

    def __post_init__(self) -> None:
        kinds = {"MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_BY", "TRAILING_PCT"}
        if self.type not in kinds or self.tif not in {"DAY", "GTC", "IOC", "FOK", "GTD"}:
            raise TradePlanSemanticError("Unsupported order type or TIF")
        required = {
            "MARKET": (False, False, False),
            "LIMIT": (True, False, False),
            "STOP": (False, True, False),
            "STOP_LIMIT": (True, True, False),
            "TRAILING_BY": (False, False, True),
            "TRAILING_PCT": (False, False, True),
        }[self.type]
        present = tuple(x is not None for x in (self.limit_price, self.stop_price, self.trail))
        if present != required:
            raise TradePlanSemanticError(f"{self.type}: invalid combination of price fields")
        if (self.tif == "GTD") != (self.until is not None):
            raise TradePlanSemanticError("GTD requires 'until'; other TIF types prohibit it")
        if any(x is not None and not isinstance(x, Expression)
               for x in (self.limit_price, self.stop_price, self.trail, self.until)):
            raise TradePlanSemanticError("Order prices and deadlines must be Expressions")
        for value in (self.limit_price, self.stop_price, self.trail):
            if isinstance(value, Literal) and (
                type(value.value) not in (int, float)
                or not isfinite(value.value) or value.value <= 0
            ):
                raise TradePlanSemanticError("Order prices and trailing distances must be positive")


@dataclass(frozen=True)
class BracketSpec:
    """Both stop-loss and take-profit are compulsory, matching qkt BRACKET."""
    stop_loss: Expression
    take_profit: Expression


@dataclass(frozen=True, kw_only=True)
class Trade(Action):
    """Base action that targets an instrument/stream. No side effects."""
    target: str
    order: OrderSpec | None = None

    def __post_init__(self) -> None:
        if type(self.target) is not str or not self.target.strip():
            raise TradePlanSemanticError("Trade requires a target stream")

    @property
    def arguments(self) -> Mapping[str, Any]:
        return {"order": self.order} if self.order is not None else {}


@dataclass(frozen=True, kw_only=True)
class Buy(Trade):
    sizing: SizingSpec
    bracket: BracketSpec | None = None
    verb: ClassVar[str] = "BUY"

    @property
    def arguments(self) -> Mapping[str, Any]:
        size = self.sizing.value
        value = size.value if isinstance(size, Literal) and self.sizing.unit == "UNITS" else self.sizing
        data: dict[str, Any] = {"sizing": value}
        if self.order is not None:
            data["order"] = self.order
        if self.bracket is not None:
            data["bracket"] = self.bracket
        return data


@dataclass(frozen=True, kw_only=True)
class Sell(Buy):
    verb: ClassVar[str] = "SELL"


@dataclass(frozen=True, kw_only=True)
class Close(Trade):
    verb: ClassVar[str] = "CLOSE"


@dataclass(frozen=True, kw_only=True)
class Resize(Trade):
    sizing: SizingSpec
    min_step: Expression | None = None
    verb: ClassVar[str] = "RESIZE"


@dataclass(frozen=True)
class CloseAll(Action):
    verb: ClassVar[str] = "CLOSE_ALL"


@dataclass(frozen=True)
class Flatten(CloseAll):
    verb: ClassVar[str] = "FLATTEN"


@dataclass(frozen=True)
class Cancel(Action):
    target: str
    verb: ClassVar[str] = "CANCEL"


@dataclass(frozen=True)
class CancelAll(Action):
    verb: ClassVar[str] = "CANCEL_ALL"


@dataclass(frozen=True)
class Log(Action):
    message: str
    level: str = "INFO"
    verb: ClassVar[str] = "LOG"

    @property
    def arguments(self) -> Mapping[str, Any]:
        return {"message": self.message}


@dataclass(frozen=True)
class Stop(Action):
    """TradePlans terminal extension, not upstream qkt STOP order syntax."""
    verb: ClassVar[str] = "STOP"


@dataclass(frozen=True)
class OcoEntry(Action):
    """Two qkt entry actions; broker capability must be verified separately."""
    entries: tuple[Buy | Sell, Buy | Sell]
    verb: ClassVar[str] = "OCO_ENTRY"

    def __post_init__(self) -> None:
        if len(self.entries) != 2 or not all(isinstance(x, (Buy, Sell)) for x in self.entries):
            raise TradePlanSemanticError("OCO_ENTRY requires exactly two BUY/SELL entries")


@dataclass(frozen=True)
class OptionLeg:
    side: str
    option_type: str
    delta: Expression
    dte_min: int | None = None
    dte_max: int | None = None
    same_expiry: bool = False


@dataclass(frozen=True)
class OpenOptions(Action):
    """qkt OPEN ... OPTIONS intent, not a resolved Schwab multi-leg order."""
    target: str
    venue: str
    root: str
    legs: tuple[OptionLeg, ...]
    sizing: SizingSpec
    verb: ClassVar[str] = "OPEN"


class ExpressionCompiler:
    """Compile YAML values into the typed expression tree."""

    OPERATORS = frozenset({
        "AND", "OR", "NOT", "=", "==", "!=", "<", "<=", ">", ">=",
        "+", "-", "*", "/",
    })

    @staticmethod
    def mapping(value: Any, label: str) -> dict:
        if type(value) is not dict:
            raise TradePlanSemanticError(f"{label} must be a mapping")
        return value

    @staticmethod
    def keys(value: dict, allowed: set[str], label: str) -> None:
        unexpected = set(value) - allowed
        if unexpected:
            raise TradePlanSemanticError(f"{label}: unsupported fields {sorted(unexpected)}")

    @staticmethod
    def identifier(value: Any, label: str) -> str:
        if type(value) is not str or not value.strip():
            raise TradePlanSemanticError(f"{label} must be a non-empty string")
        return value

    def compile(self, raw: Any) -> Expression:
        if type(raw) in (str, int, float, bool) or raw is None:
            return Literal(raw)
        obj = self.mapping(raw, "expression")
        if "ref" in obj:
            self.keys(obj, {"ref"}, "reference")
            return Reference(self.identifier(obj["ref"], "reference"))
        self.keys(obj, {"op", "args"}, "expression")
        op = self.identifier(obj.get("op"), "operator").upper()
        if op not in self.OPERATORS:
            raise TradePlanSemanticError(f"Unsupported operator {op!r}")
        args = obj.get("args")
        if type(args) is not list:
            raise TradePlanSemanticError("Expression args must be a list")
        if (op == "NOT" and len(args) != 1) or (op != "NOT" and len(args) != 2):
            raise TradePlanSemanticError(f"{op}: wrong operand count")
        return Operation(op, tuple(self.compile(arg) for arg in args))

    def condition(self, raw: Any) -> Expression:
        if type(raw) is dict and raw.get("op") == "*":
            self.keys(raw, {"op", "args"}, "unconditional condition")
            args = raw.get("args", [])
            if type(args) is list and not args:
                return Operation("*", ())
        return self.compile(raw)


class ActionCompiler:
    """Compile verbs to concrete Actions; no untyped action argument bucket."""

    def __init__(self, expressions: ExpressionCompiler | None = None):
        self.expressions = expressions or ExpressionCompiler()

    def sizing(self, value: Any) -> SizingSpec:
        if type(value) is dict and "unit" in value:
            self.expressions.keys(value, {"unit", "value"}, "sizing")
            unit = self.expressions.identifier(value["unit"], "sizing unit").upper()
            return SizingSpec(self.expressions.compile(value.get("value")), unit)
        return SizingSpec(self.expressions.compile(value))

    def order(self, value: Any) -> OrderSpec:
        data = self.expressions.mapping(value, "order")
        self.expressions.keys(data, {"type", "limit_price", "stop_price", "trail", "tif", "until"}, "order")
        kind = self.expressions.identifier(data.get("type", "MARKET"), "order type").upper()
        tif = self.expressions.identifier(data.get("tif", "GTC"), "time in force").upper()
        return OrderSpec(
            type=kind, tif=tif,
            limit_price=self.expressions.compile(data["limit_price"]) if "limit_price" in data else None,
            stop_price=self.expressions.compile(data["stop_price"]) if "stop_price" in data else None,
            trail=self.expressions.compile(data["trail"]) if "trail" in data else None,
            until=self.expressions.compile(data["until"]) if "until" in data else None,
        )

    def bracket(self, value: Any) -> BracketSpec:
        data = self.expressions.mapping(value, "bracket")
        self.expressions.keys(data, {"stop_loss", "take_profit"}, "bracket")
        if set(data) != {"stop_loss", "take_profit"}:
            raise TradePlanSemanticError("BRACKET requires stop_loss and take_profit")
        return BracketSpec(
            self.expressions.compile(data["stop_loss"]),
            self.expressions.compile(data["take_profit"]),
        )

    def options(self, data: dict) -> OpenOptions:
        self.expressions.keys(data, {"action", "target", "venue", "root", "legs", "sizing"}, "OPEN")
        legs_raw = data.get("legs")
        if type(legs_raw) is not list or not legs_raw:
            raise TradePlanSemanticError("OPEN OPTIONS requires nonempty legs")
        legs = []
        for raw in legs_raw:
            leg = self.expressions.mapping(raw, "option leg")
            self.expressions.keys(leg, {"side", "option_type", "delta", "dte_min", "dte_max", "same_expiry"}, "option leg")
            side = self.expressions.identifier(leg.get("side"), "option side").upper()
            kind = self.expressions.identifier(leg.get("option_type"), "option type").upper()
            dmin, dmax = leg.get("dte_min"), leg.get("dte_max")
            same = leg.get("same_expiry", False)
            if side not in {"BUY", "SELL"} or kind not in {"CALL", "PUT"}:
                raise TradePlanSemanticError("Option legs require BUY/SELL CALL/PUT")
            if type(same) is not bool or (same and (dmin is not None or dmax is not None)):
                raise TradePlanSemanticError("Invalid option expiry selection")
            if not same and (type(dmin) is not int or type(dmax) is not int or not 0 <= dmin <= dmax):
                raise TradePlanSemanticError("Option leg requires DTE range or same_expiry")
            delta = self.expressions.compile(leg.get("delta"))
            if isinstance(delta, Literal) and (
                type(delta.value) not in (int, float) or not 0 < delta.value <= 1
            ):
                raise TradePlanSemanticError("Option delta must be between zero and one")
            legs.append(OptionLeg(side, kind, delta, dmin, dmax, same))
        return OpenOptions(
            target=self.expressions.identifier(data.get("target"), "structure alias"),
            venue=self.expressions.identifier(data.get("venue"), "options venue"),
            root=self.expressions.identifier(data.get("root"), "options root"),
            legs=tuple(legs), sizing=self.sizing(data.get("sizing")),
        )

    def compile(self, raw: Any, aliases: set[str]) -> Action:
        data = self.expressions.mapping(raw, "action")
        verb = self.expressions.identifier(data.get("action"), "action").upper()
        if verb == "OPEN":
            return self.options(data)
        if verb == "OCO_ENTRY":
            self.expressions.keys(data, {"action", "entries"}, "OCO_ENTRY")
            raw_entries = data.get("entries")
            if type(raw_entries) is not list or len(raw_entries) != 2:
                raise TradePlanSemanticError("OCO_ENTRY requires exactly two entries")
            return OcoEntry(tuple(self._entry(item, aliases) for item in raw_entries))
        if verb in {"BUY", "SELL"}:
            return self._entry(data, aliases)
        if verb in {"CLOSE", "RESIZE"}:
            allowed = {"action", "target"} | ({"sizing", "min_step"} if verb == "RESIZE" else set())
            self.expressions.keys(data, allowed, verb)
            target = self._target(data, aliases)
            if verb == "CLOSE":
                return Close(target=target)
            if "sizing" not in data:
                raise TradePlanSemanticError("RESIZE requires sizing")
            return Resize(target=target, sizing=self.sizing(data["sizing"]),
                          min_step=self.expressions.compile(data["min_step"])
                          if "min_step" in data else None)
        if verb == "CANCEL":
            self.expressions.keys(data, {"action", "target"}, "CANCEL")
            return Cancel(self._target(data, aliases))
        if verb in {"CANCEL_ALL", "CLOSE_ALL", "FLATTEN", "STOP"}:
            self.expressions.keys(data, {"action"}, verb)
            return {"CANCEL_ALL": CancelAll, "CLOSE_ALL": CloseAll,
                    "FLATTEN": Flatten, "STOP": Stop}[verb]()
        if verb == "LOG":
            self.expressions.keys(data, {"action", "message", "level"}, "LOG")
            message = self.expressions.identifier(data.get("message"), "LOG message")
            level = self.expressions.identifier(data.get("level", "INFO"), "LOG level").upper()
            if level not in {"INFO", "WARN", "ERROR", "DEBUG"}:
                raise TradePlanSemanticError("Unsupported LOG level")
            return Log(message, level)
        raise TradePlanSemanticError(f"Unsupported action {verb!r}")

    def _target(self, data: dict, aliases: set[str]) -> str:
        target = data.get("target")
        if target not in aliases:
            raise TradePlanSemanticError(f"Unknown action target {target!r}")
        return target

    def _entry(self, raw: Any, aliases: set[str]) -> Buy | Sell:
        data = self.expressions.mapping(raw, "entry")
        self.expressions.keys(data, {"action", "target", "sizing", "order", "bracket"}, "entry")
        verb = self.expressions.identifier(data.get("action"), "entry action").upper()
        if verb not in {"BUY", "SELL"}:
            raise TradePlanSemanticError("Entry must be BUY or SELL")
        if "sizing" not in data:
            raise TradePlanSemanticError(f"{verb} requires sizing")
        return (Buy if verb == "BUY" else Sell)(
            target=self._target(data, aliases),
            sizing=self.sizing(data["sizing"]),
            order=self.order(data["order"]) if "order" in data else None,
            bracket=self.bracket(data["bracket"]) if "bracket" in data else None,
        )


class QktYamlSemanticInterpreter(SemanticInterpreter):
    """Interpret the documented YAML subset into qkt-inspired typed semantics."""

    def interpret(self, document: Mapping[str, Any]) -> Strategy:
        helper = ExpressionCompiler
        root = helper.mapping(document, "document")
        helper.keys(root, {"strategy", "plan"}, "document")
        if "plan" not in root:
            raise TradePlanSemanticError("Missing plan")
        data = helper.mapping(root.get("strategy"), "strategy")
        helper.keys(data, {"name", "version", "symbols", "parameters"}, "strategy")
        name = helper.identifier(data.get("name"), "strategy.name")
        version = data.get("version")
        if type(version) is not int or version < 1:
            raise TradePlanSemanticError("strategy.version must be a positive integer")
        symbols = helper.mapping(data.get("symbols"), "strategy.symbols")
        if not symbols:
            raise TradePlanSemanticError("strategy.symbols cannot be empty")
        streams = []
        for alias, raw in symbols.items():
            helper.identifier(alias, "stream alias")
            spec = helper.mapping(raw, f"stream {alias}")
            helper.keys(spec, {"source", "timeframe", "warmup_bars"}, f"stream {alias}")
            warmup = spec.get("warmup_bars")
            if warmup is not None and (type(warmup) is not int or warmup < 0):
                raise TradePlanSemanticError("warmup_bars must be a nonnegative integer")
            streams.append(Stream(
                alias, helper.identifier(spec.get("source"), "stream source"),
                helper.identifier(spec.get("timeframe"), "stream timeframe"), warmup,
            ))
        parameters = helper.mapping(data.get("parameters", {}), "parameters")
        return Strategy(name, version, tuple(streams), parameters=parameters)


class QktYamlPlanCompiler(PlanCompiler):
    """Compile named one-shot TradePlan steps with typed qkt-style actions."""

    def __init__(self, expressions: ExpressionCompiler | None = None,
                 actions: ActionCompiler | None = None):
        self.expressions = expressions or ExpressionCompiler()
        self.actions = actions or ActionCompiler(self.expressions)

    def compile(self, strategy: Strategy, document: Mapping[str, Any]) -> TradePlan:
        helper = self.expressions
        raw = helper.mapping(document.get("plan"), "plan")
        helper.keys(raw, {"id", "entry", "blocks"}, "plan")
        identifier = helper.identifier(raw.get("id"), "plan.id")
        entry = helper.identifier(raw.get("entry"), "plan.entry")
        blocks = helper.mapping(raw.get("blocks"), "plan.blocks")
        if not blocks:
            raise TradePlanSemanticError("plan.blocks cannot be empty")
        aliases = {stream.alias for stream in strategy.streams}
        steps, rules = [], []
        for name, value in blocks.items():
            helper.identifier(name, "block name")
            block = helper.mapping(value, f"block {name}")
            helper.keys(block, {"when", "then"}, f"block {name}")
            then = helper.mapping(block.get("then"), f"{name}.then")
            if "when" not in block:
                raise TradePlanSemanticError(f"{name}: missing when condition")
            condition = helper.condition(block["when"])
            action = self.actions.compile(
                {key: value for key, value in then.items() if key != "next"}, aliases,
            )
            if isinstance(action, Stop):
                if "next" in then:
                    raise TradePlanSemanticError(f"{name}: STOP cannot have next")
                if condition != Operation("*", ()):
                    raise TradePlanSemanticError(f"{name}: STOP requires unconditional when: {{op: '*'}}")
                steps.append(PlanStep(name, (action,), terminal=True))
            else:
                successor = helper.identifier(then.get("next"), f"{name}.then.next")
                steps.append(PlanStep(name, (action,), (Transition(successor),)))
            rules.append(Rule(condition, (action,)))
        resolved = Strategy(strategy.name, strategy.version, strategy.streams,
                            tuple(rules), strategy.parameters)
        return TradePlan(identifier, resolved, entry, tuple(steps))


class AcyclicPlanValidator(GraphValidator):
    """Check a finite graph; no trading side effects."""

    def validate(self, plan: TradePlan) -> None:
        names = [step.identifier for step in plan.steps]
        if len(names) != len(set(names)):
            raise TradePlanSemanticError("Duplicate plan block identifiers")
        by_name = {step.identifier: step for step in plan.steps}
        if plan.entry not in by_name:
            raise TradePlanSemanticError("Entry block not found")
        if by_name[plan.entry].terminal:
            raise TradePlanSemanticError("Entry cannot be terminal")
        if not any(step.terminal for step in plan.steps):
            raise TradePlanSemanticError("No terminal block")
        for step in plan.steps:
            if step.terminal:
                if (step.transitions or len(step.actions) != 1
                    or not isinstance(step.actions[0], Stop)):
                    raise TradePlanSemanticError(f"{step.identifier}: terminal must contain only STOP")
            elif not step.transitions or not step.actions:
                raise TradePlanSemanticError(f"{step.identifier}: dangling action block")
            for edge in step.transitions:
                if edge.destination not in by_name:
                    raise TradePlanSemanticError(
                        f"{step.identifier}: missing successor {edge.destination}"
                    )
        states: dict[str, int] = {}

        def visit(name: str) -> None:
            state = states.get(name, 0)
            if state == 1:
                raise TradePlanSemanticError(f"Cycle detected at {name}")
            if state == 2:
                return
            states[name] = 1
            for edge in by_name[name].transitions:
                visit(edge.destination)
            states[name] = 2

        visit(plan.entry)
        if len(states) != len(by_name):
            raise TradePlanSemanticError(
                f"Unreachable blocks: {sorted(set(by_name) - set(states))}"
            )

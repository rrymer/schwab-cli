"""Minimal, explicitly scoped YAML-to-TradePlan semantic implementation.

This is a qkt-*inspired* YAML object model, not qkt's Kotlin parser or a
general qkt grammar implementation. Unknown shapes fail closed.
"""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import (
    ActionCall, Expression, GraphValidator, Literal, Operation, PlanCompiler,
    PlanStep, Reference, Rule, SemanticInterpreter, Strategy, Stream,
    TradePlan, Transition,
)


class TradePlanSemanticError(ValueError):
    """Unsupported or inconsistent TradePlan semantic content."""


_OPERATORS = frozenset({
    "AND", "OR", "NOT", "=", "==", "!=", "<", "<=", ">", ">=",
    "+", "-", "*", "/",
})
_ACTIONS = frozenset({"BUY", "SELL", "CLOSE", "LOG", "END"})
_TERMINAL_NODE_TYPES = frozenset({"terminal", "end", "stop"})


def _mapping(value: Any, label: str) -> dict:
    if type(value) is not dict:
        raise TradePlanSemanticError(f"{label} must be a mapping")
    return value


def _keys(value: dict, allowed: set[str], label: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise TradePlanSemanticError(f"{label}: unsupported fields {sorted(unexpected)}")


def _identifier(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise TradePlanSemanticError(f"{label} must be a non-empty string")
    return value


def _expression(raw: Any) -> Expression:
    if type(raw) in (str, int, float, bool) or raw is None:
        return Literal(raw)
    obj = _mapping(raw, "expression")
    if "ref" in obj:
        _keys(obj, {"ref"}, "reference")
        return Reference(_identifier(obj["ref"], "reference"))
    _keys(obj, {"op", "args"}, "expression")
    op = _identifier(obj.get("op"), "operator").upper()
    if op not in _OPERATORS:
        raise TradePlanSemanticError(f"Unsupported operator {op!r}")
    args = obj.get("args")
    if type(args) is not list:
        raise TradePlanSemanticError("Expression args must be a list")
    if (op == "NOT" and len(args) != 1) or (op != "NOT" and len(args) != 2):
        raise TradePlanSemanticError(f"{op}: wrong operand count")
    return Operation(op, tuple(_expression(arg) for arg in args))


def _action(raw: Any, aliases: set[str]) -> ActionCall:
    data = _mapping(raw, "action")
    _keys(data, {"action", "target", "sizing", "message"}, "action")
    verb = _identifier(data.get("action"), "action").upper()
    if verb not in _ACTIONS:
        raise TradePlanSemanticError(f"Unsupported action {verb!r}")
    target = data.get("target")
    if verb in {"BUY", "SELL", "CLOSE"}:
        if target not in aliases:
            raise TradePlanSemanticError(f"Unknown action target {target!r}")
    elif target is not None:
        raise TradePlanSemanticError("LOG cannot specify a target")
    if verb in {"BUY", "SELL"}:
        sizing = data.get("sizing")
        if type(sizing) not in (int, float) or sizing <= 0:
            raise TradePlanSemanticError(f"{verb} requires positive numeric sizing")
    elif "sizing" in data:
        raise TradePlanSemanticError(f"{verb} does not accept sizing")
    if verb == "LOG":
        _identifier(data.get("message"), "LOG message")
    elif "message" in data:
        raise TradePlanSemanticError(f"{verb} does not accept message")
    return ActionCall(verb, target=target, arguments={
        key: value for key, value in data.items() if key not in ("action", "target")
    })


def _strategy(document: dict) -> Strategy:
    data = _mapping(document.get("strategy"), "strategy")
    _keys(data, {"name", "version", "symbols", "parameters"}, "strategy")
    name = _identifier(data.get("name"), "strategy.name")
    version = data.get("version")
    if type(version) is not int or version < 1:
        raise TradePlanSemanticError("strategy.version must be a positive integer")
    symbols = _mapping(data.get("symbols"), "strategy.symbols")
    if not symbols:
        raise TradePlanSemanticError("strategy.symbols cannot be empty")
    streams = []
    for alias, raw in symbols.items():
        _identifier(alias, "stream alias")
        spec = _mapping(raw, f"stream {alias}")
        _keys(spec, {"source", "timeframe", "warmup_bars"}, f"stream {alias}")
        warmup = spec.get("warmup_bars")
        if warmup is not None and (type(warmup) is not int or warmup < 0):
            raise TradePlanSemanticError("warmup_bars must be a nonnegative integer")
        streams.append(Stream(alias, _identifier(spec.get("source"), "stream source"),
                              _identifier(spec.get("timeframe"), "stream timeframe"),
                              warmup))
    parameters = _mapping(data.get("parameters", {}), "parameters")
    return Strategy(name, version, tuple(streams), parameters=parameters)


class QktYamlSemanticInterpreter(SemanticInterpreter):
    """Interpret the documented qkt-inspired subset of a YAML plan."""

    def interpret(self, document: Mapping[str, Any]) -> Strategy:
        root = _mapping(document, "document")
        _keys(root, {"strategy", "plan"}, "document")
        if "plan" not in root:
            raise TradePlanSemanticError("Missing plan")
        return _strategy(root)


class QktYamlPlanCompiler(PlanCompiler):
    """Convert explicit named YAML blocks into a finite TradePlan model."""

    def normalize_node_type(self, value: str) -> str:
        """Canonicalize terminal node type aliases; preserve other node types."""
        if not isinstance(value, str):
            raise ValueError("TradePlan node type must be a string")
        if value.strip().lower() in _TERMINAL_NODE_TYPES:
            return "terminal"
        else:
            return value

    def compile(self, strategy: Strategy, document: Mapping[str, Any]) -> TradePlan:
        raw = _mapping(document.get("plan"), "plan")
        _keys(raw, {"id", "entry", "blocks"}, "plan")
        identifier = _identifier(raw.get("id"), "plan.id")
        entry = _identifier(raw.get("entry"), "plan.entry")
        blocks = _mapping(raw.get("blocks"), "plan.blocks")
        if not blocks:
            raise TradePlanSemanticError("plan.blocks cannot be empty")
        aliases = {stream.alias for stream in strategy.streams}
        steps = []
        rules = []
        for name, value in blocks.items():
            _identifier(name, "block name")
            block = _mapping(value, f"block {name}")
            _keys(block, {"type", "when", "then"}, f"block {name}")
            print(block.get("type"))
            block["type"] = self.normalize_node_type(block.get("type"))
            if block.get("type") == "terminal":
                if "then" in block or "when" in block:
                    raise TradePlanSemanticError(f"{name}: terminal cannot have actions")
                steps.append(PlanStep(name, terminal=True))
                continue
            if "type" in block:
                raise TradePlanSemanticError(f"{name}: unknown block type")
            then = _mapping(block.get("then"), f"{name}.then")
            _keys(then, {"action", "target", "sizing", "message", "next"}, f"{name}.then")
            successor = _identifier(then.get("next"), f"{name}.then.next")
            call = _action({key: value for key, value in then.items() if key != "next"}, aliases)
            if "when" not in block:
                raise TradePlanSemanticError(f"{name}: missing when condition")
            condition = _expression(block["when"])
            steps.append(PlanStep(name, (call,), (Transition(successor),)))
            rules.append(Rule(condition, (call,)))
        # Preserve rule conditions in the Strategy semantic model.
        resolved_strategy = Strategy(strategy.name, strategy.version, strategy.streams,
                                     tuple(rules), strategy.parameters)
        return TradePlan(identifier, resolved_strategy, entry, tuple(steps))


class AcyclicPlanValidator(GraphValidator):
    """Pure-Python finite DAG validator, without order submission or side effects."""

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
                if step.transitions or step.actions:
                    raise TradePlanSemanticError(f"{step.identifier}: terminal has actions/edges")
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

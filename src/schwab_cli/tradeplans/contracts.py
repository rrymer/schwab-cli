"""TradePlans semantic contracts for a qkt-inspired DSL.

These are *Python* interfaces, not qkt's Kotlin AST or a supported qkt parser.
Loading, interpreting, validating, evaluating and submitting are separate
capabilities. Importing this module performs no I/O.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


# The semantic tree contains data, not arbitrary callables or executable code.
# Python's native dict/list/scalar types are the interchange format.


class Expression(ABC):
    """Pure syntax for a value or condition; evaluation belongs to a runtime."""

    @abstractmethod
    def references(self) -> frozenset[str]:
        """Return names referenced by this expression without evaluating them."""


@dataclass(frozen=True)
class Literal(Expression):
    value: str | int | float | bool | None

    def references(self) -> frozenset[str]:
        return frozenset()


@dataclass(frozen=True)
class Reference(Expression):
    """Examples: asset.close, ACCOUNT.equity, POSITION.asset.qty."""

    name: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Reference name cannot be empty")

    def references(self) -> frozenset[str]:
        return frozenset((self.name,))


@dataclass(frozen=True)
class Operation(Expression):
    """An operator or function call; no operator is executed during construction."""

    name: str
    operands: tuple[Expression, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Operation name cannot be empty")
        if not all(isinstance(item, Expression) for item in self.operands):
            raise TypeError("Operation operands must be Expressions")

    def references(self) -> frozenset[str]:
        return frozenset().union(*(item.references() for item in self.operands))


class Action(ABC):
    """Data-only trading action; it cannot submit an order by itself."""

    @property
    @abstractmethod
    def verb(self) -> str:
        """Action vocabulary, e.g. BUY, CLOSE, OPEN, LOG."""


@dataclass(frozen=True)
class Stream:
    alias: str
    source: str
    timeframe: str
    warmup_bars: int | None = None


@dataclass(frozen=True)
class Rule:
    condition: Expression
    actions: tuple[Action, ...]

    def __post_init__(self) -> None:
        if not self.actions:
            raise ValueError("Rule requires at least one action")
        if not isinstance(self.condition, Expression):
            raise TypeError("Rule condition must be an Expression")
        if not all(isinstance(action, Action) for action in self.actions):
            raise TypeError("Rule actions must be Action instances")


@dataclass(frozen=True)
class Strategy:
    """qkt-inspired strategy semantics, without invented successor keywords."""

    name: str
    version: int
    streams: tuple[Stream, ...]
    rules: tuple[Rule, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Strategy name cannot be empty")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("Strategy version must be a positive integer")
        if not self.streams:
            raise ValueError("Strategy must declare at least one stream")


@dataclass(frozen=True)
class Transition:
    """TradePlans extension, NOT a qkt keyword: named successor edge."""

    destination: str
    condition: Expression | None = None


@dataclass(frozen=True)
class PlanStep:
    """One bounded TradePlans workflow node and its explicit outgoing edges."""

    identifier: str
    actions: tuple[Action, ...] = ()
    transitions: tuple[Transition, ...] = ()
    terminal: bool = False


@dataclass(frozen=True)
class TradePlan:
    """Versioned finite workflow built from semantics, not executable code."""

    identifier: str
    strategy: Strategy
    entry: str
    steps: tuple[PlanStep, ...]


class DocumentLoader(ABC):
    """Load a complete, validated TradePlan from a source document."""

    @abstractmethod
    def load(self, source: str) -> TradePlan:
        """Parse, interpret, compile and validate a TradePlan without side effects."""


class SemanticInterpreter(ABC):
    """Map native Python data onto the supported typed semantic subset."""

    @abstractmethod
    def interpret(self, document: Mapping[str, Any]) -> Strategy:
        """Reject unsupported constructs; never call the broker or network."""


class PlanCompiler(ABC):
    """Compile semantic models into a finite plan, without side effects."""

    @abstractmethod
    def compile(self, strategy: Strategy, document: Mapping[str, Any]) -> TradePlan:
        """Produce a TradePlan with explicit actions, steps and transitions."""


class GraphValidator(ABC):
    """Check successor DAGs, reachability, terminal paths and action budgets."""

    @abstractmethod
    def validate(self, plan: TradePlan) -> None:
        """Raise on invalid plans; a later implementation may use NetworkX."""


class ConditionEvaluator(ABC):
    """Evaluate typed expressions against a supplied, immutable market snapshot."""

    @abstractmethod
    def evaluate(self, expression: Expression, context: Mapping[str, Any]) -> Any:
        """Return a value, or a declared unknown state for missing inputs."""


class SafeguardEvaluator(ABC):
    """Assess direction, buying power and hard risk constraints without submitting."""

    @abstractmethod
    def check(self, plan: TradePlan, context: Mapping[str, Any]) -> Any:
        """Return structured safeguards evidence; no order side effects."""


class ActionExecutor(ABC):
    """Broker-facing, stateful boundary; implementing it requires safety gates."""

    @abstractmethod
    def execute(self, action: Action, context: Mapping[str, Any]) -> Any:
        """Execute only after graph validation, safeguards and idempotency checks."""

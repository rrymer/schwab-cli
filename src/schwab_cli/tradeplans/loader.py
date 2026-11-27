"""Safe YAML document loader for TradePlans.

The public loader orchestrates safe YAML parsing, semantic interpretation,
plan compilation and graph validation. Brokerage permissions are never involved.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .contracts import DocumentLoader, GraphValidator, PlanCompiler, SemanticInterpreter, TradePlan
from .semantics import AcyclicPlanValidator, QktYamlPlanCompiler, QktYamlSemanticInterpreter


class DocumentLoadError(ValueError):
    """A source document is malformed, ambiguous or not a supported data tree."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """PyYAML SafeLoader with rejection of ambiguous duplicate mapping keys."""


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)  # Preserve SafeLoader's standard YAML merge support.
    result: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if type(key) is not str:
            raise DocumentLoadError("YAML mapping keys must be strings")
        if key in result:
            raise DocumentLoadError(f"Duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _primitive_tree(value: Any, *, max_depth: int, max_nodes: int) -> Any:
    """Copy to pure Python JSON-compatible types; detect alias cycles/bombs."""
    active: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise DocumentLoadError("Document exceeds maximum expanded node count")
        if depth > max_depth:
            raise DocumentLoadError("Document nesting exceeds maximum depth")
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise DocumentLoadError("Non-finite numbers are not supported")
            return item
        if type(item) not in (list, dict):
            raise DocumentLoadError(f"Unsupported YAML value type: {type(item).__name__}")
        identity = id(item)
        if identity in active:
            raise DocumentLoadError("Recursive YAML aliases are not supported")
        active.add(identity)
        try:
            if type(item) is list:
                return [visit(child, depth + 1) for child in item]
            out: dict[str, Any] = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise DocumentLoadError("YAML mapping keys must be strings")
                out[key] = visit(child, depth + 1)
            return out
        finally:
            active.remove(identity)

    return visit(value, 0)


class YamlDocumentLoader(DocumentLoader):
    """Produce a validated TradePlan from YAML *text*, never a path.

    Parsing, semantic interpretation, compilation and graph validation remain
    independently testable strategies. No orders or network calls occur here.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 1_048_576,
        max_depth: int = 64,
        max_nodes: int = 100_000,
        interpreter: SemanticInterpreter | None = None,
        compiler: PlanCompiler | None = None,
        validator: GraphValidator | None = None,
    ) -> None:
        if any(type(x) is not int or x <= 0 for x in (max_bytes, max_depth, max_nodes)):
            raise ValueError("Loader limits must be positive integers")
        self.max_bytes = max_bytes
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.interpreter = interpreter if interpreter is not None else QktYamlSemanticInterpreter()
        self.compiler = compiler if compiler is not None else QktYamlPlanCompiler()
        self.validator = validator if validator is not None else AcyclicPlanValidator()

    def _parse_mapping(self, source: str) -> Mapping[str, Any]:
        if type(source) is not str:
            raise TypeError("YAML source must be text, not a file path or stream")
        if len(source.encode("utf-8")) > self.max_bytes:
            raise DocumentLoadError("YAML document exceeds maximum byte size")
        try:
            document = yaml.load(source, Loader=_UniqueKeySafeLoader)
            if type(document) is not dict:
                raise DocumentLoadError("YAML document must have a mapping at its root")
            return _primitive_tree(document, max_depth=self.max_depth, max_nodes=self.max_nodes)
        except DocumentLoadError:
            raise
        except (yaml.YAMLError, RecursionError, OverflowError) as exc:
            raise DocumentLoadError(f"Invalid YAML document: {exc}") from exc

    def load(self, source: str) -> TradePlan:
        """Load and validate a typed TradePlan from a supported YAML document."""
        document = self._parse_mapping(source)
        strategy = self.interpreter.interpret(document)
        plan = self.compiler.compile(strategy, document)
        self.validator.validate(plan)
        return plan


@dataclass(frozen=True)
class FileDocumentLoader:
    """Bind a source file to a document parser/TradePlan compiler.

    The path is not opened until load() is invoked. The injected DocumentLoader
    still accepts source *text*, making both interfaces independently useful.
    """

    path: Path
    document_loader: DocumentLoader

    def load(self) -> TradePlan:
        """Read a bounded UTF-8 file and return a validated TradePlan."""
        limit = getattr(self.document_loader, "max_bytes", 1_048_576)
        if type(limit) is not int or limit <= 0:
            raise ValueError("Document loader max_bytes must be a positive integer")
        with self.path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise DocumentLoadError(f"TradePlan file exceeds {limit} bytes: {self.path}")
        try:
            source = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentLoadError(f"TradePlan file is not UTF-8: {self.path}") from exc
        return self.document_loader.load(source)


def get_loader(
    path: str | os.PathLike[str],
    loader: DocumentLoader | type[DocumentLoader] | str | None = None,
) -> FileDocumentLoader:
    """Select and bind a TradePlan loader by file extension or explicit override.

    Examples:
        plan = get_loader("myplan.yaml").load()
        plan = get_loader(Path("myplan.yml")).load()
        plan = get_loader("custom.plan", loader="yaml").load()
        plan = get_loader("custom.plan", loader=YamlDocumentLoader).load()
        plan = get_loader("custom.plan", loader=YamlDocumentLoader()).load()

    The function never interprets the path string as YAML source text.
    """
    if not isinstance(path, (str, os.PathLike)):
        raise TypeError("TradePlan path must be a string or PathLike")
    raw_path = os.fspath(path)
    if not isinstance(raw_path, str):
        raise TypeError("TradePlan paths must be text, not bytes")
    if not raw_path:
        raise ValueError("TradePlan path cannot be empty")
    file_path = Path(raw_path)

    if loader is None:
        selected = file_path.suffix.lower().removeprefix(".")
    elif isinstance(loader, str):
        selected = loader.lower().removeprefix(".")
    elif isinstance(loader, DocumentLoader):
        return FileDocumentLoader(file_path, loader)
    elif isinstance(loader, type) and issubclass(loader, DocumentLoader):
        return FileDocumentLoader(file_path, loader())
    else:
        raise TypeError("loader must be a format name, DocumentLoader class or instance")

    if selected in ("yaml", "yml"):
        return FileDocumentLoader(file_path, YamlDocumentLoader())
    raise ValueError(
        f"Unsupported TradePlan format {selected!r}; "
        "specify loader='yaml' or provide a DocumentLoader"
    )

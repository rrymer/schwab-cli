"""Safe YAML document loader for TradePlans.

Only syntax and primitive document shape are handled here. qkt vocabulary,
TradePlans graph semantics and brokerage permissions belong to later stages.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import yaml

from .contracts import DocumentLoader


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
    """Deserialize one YAML mapping from *source text*, never from a path.

    Uses PyYAML's safe constructor, rejects duplicate keys, arbitrary tags,
    non-primitive values, alias cycles and unbounded expanded documents.
    Nothing here validates qkt action names or TradePlans instructions.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 1_048_576,
        max_depth: int = 64,
        max_nodes: int = 100_000,
    ) -> None:
        if any(type(x) is not int or x <= 0 for x in (max_bytes, max_depth, max_nodes)):
            raise ValueError("Loader limits must be positive integers")
        self.max_bytes = max_bytes
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def load(self, source: str) -> Mapping[str, Any]:
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

"""Utilities for working with TradePlans documents."""
import os
from pathlib import Path

from .contracts import DocumentLoader
from .loader import YamlDocumentLoader, FileDocumentLoader


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

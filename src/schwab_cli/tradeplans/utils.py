"""Utilities for working with TradePlans documents."""

from os import PathLike
from pathlib import Path

from .contracts import DocumentLoader
from .loader import DocumentLoadError, YamlDocumentLoader


def get_loader(
    file_path: str | PathLike[str], file_type: str | None = None
) -> DocumentLoader:
    """Instantiate a loader by explicit file type or by path suffix."""
    if file_type is None:
        file_type = Path(file_path).suffix

    if file_type.removeprefix(".").lower() in ("yaml", "yml"):
        return YamlDocumentLoader()

    if not file_type:
        raise DocumentLoadError(f"Missing TradePlans file type for {file_path!s}")
    raise DocumentLoadError(f"Unsupported TradePlans file type: {file_type!r}")

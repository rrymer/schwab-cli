"""Utilities for working with TradePlans documents."""
import json
import os
from pathlib import Path
from typing import Any

import yaml

from .contracts import DocumentLoader
from .loader import YamlDocumentLoader, FileDocumentLoader


def load_trade_plan(path: str | Path) -> dict[str, Any]:
    """Safely deserialize a local YAML mapping; never resolve credentials or call Schwab."""
    source = Path(path).expanduser()
    try:
        with source.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid TradePlan YAML at {source}: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ValueError("TradePlan YAML must be a nonempty mapping")
    if not all(isinstance(key, str) for key in data):
        raise ValueError("TradePlan YAML top-level keys must be strings")
    return data


def preview_trade_plan(path: str | Path, *, submit: bool = False,
                       gmail_notify: bool = False) -> None:
    """Show the loaded document's shape only. Live plan orders are not implemented."""
    if submit:
        raise ValueError(
            "--submit with --plan is disabled: TradePlans compilation, graph "
            "validation, safeguards and idempotent execution are not implemented"
        )
    if gmail_notify:
        raise ValueError("--gmail-notify requires a submitted order; YAML plans are preview-only")
    data = load_trade_plan(path)
    print(json.dumps({
        "source": str(Path(path).expanduser()),
        "top_level_keys": list(data),
        "status": "loaded, not compiled or validated",
    }, indent=2))
    print("DRY RUN: no order submitted. YAML TradePlan execution is not implemented.")

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

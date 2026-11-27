"""Read a YAML TradePlan without enabling broker execution.

The TradePlans compiler, graph validator and safeguards are interfaces only.
This module deliberately does not infer an execution format from arbitrary YAML.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


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

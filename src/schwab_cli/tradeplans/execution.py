"""One-shot, explicitly authorized TradePlan order submission.

This adapter deliberately does not advance plan DAG nodes. Acceptance is not a
fill; succeeding steps require independent reconciliation and authorization.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..database import default_db_path
from ..orders import SchwabOrder, build_single_order, submit_order
from .contracts import ActionCall, TradePlan
from .monitor import observe_step, schwab_snapshot


class PlanExecutionError(ValueError):
    """A plan action is not eligible for order submission."""


class DuplicatePlanAction(PlanExecutionError):
    """A step was already claimed; do not retry automatically."""


@dataclass(frozen=True)
class SubmissionResult:
    plan_id: str
    step: str
    status: str
    location: str | None = None


class PlanOrderJournal:
    """Durable claim-before-POST record, separate from aggregate positions.

    Claimed entries are never implicitly released, including after network
    failures and process crashes. Manual broker reconciliation is required.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = (Path(path).expanduser() if path is not None
                     else default_db_path().with_name("tradeplans.sqlite3"))
        self.db: sqlite3.Connection | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create privately even if the process has a permissive umask.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS plan_submissions (
                plan_id TEXT NOT NULL,
                step TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                location TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (plan_id, step)
            )
        """)
        return self

    def __exit__(self, *_):
        if self.db is not None:
            self.db.close()

    def claim(self, plan_id: str, step: str, payload: dict) -> None:
        """Atomically reserve an action before its first possible network POST."""
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, allow_nan=False
        ).encode("utf-8")).hexdigest()
        assert self.db is not None
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO plan_submissions(plan_id,step,payload_sha256,state) "
                "VALUES (?,?,?,'outcome_unknown')",
                (plan_id, step, digest),
            )
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self.db.execute("ROLLBACK")
            raise DuplicatePlanAction(
                f"Plan {plan_id!r}, step {step!r} was previously claimed. "
                "Check broker order history and the local submission journal; "
                "never blindly retry."
            ) from exc
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def accepted(self, plan_id: str, step: str, location: str | None) -> None:
        assert self.db is not None
        with self.db:
            self.db.execute(
                "UPDATE plan_submissions SET state='accepted',location=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE plan_id=? AND step=?",
                (location, plan_id, step),
            )


def _live_stream(plan: TradePlan, alias: str) -> str:
    streams = {stream.alias: stream for stream in plan.strategy.streams}
    if alias not in streams:
        raise PlanExecutionError(f"Unknown action stream: {alias}")
    source = streams[alias].source
    if not source.startswith("SCHWAB:") or not source[7:]:
        raise PlanExecutionError("Order submission requires an explicit SCHWAB:<symbol> stream")
    symbol = source[7:].strip().upper()
    if not symbol.isascii() or not symbol.replace(".", "").isalnum():
        raise PlanExecutionError("Only explicit equity ticker symbols are supported")
    return symbol


def _fresh_quote(symbol: str, *, max_age_seconds: float = 60) -> float:
    """Require a recent broker timestamp; do not equate stale marks with live prices."""
    from ..quotes import get_quotes, quote_price

    quote = get_quotes([symbol]).get(symbol)
    price = quote_price(quote)
    details = quote.get("quote") if isinstance(quote, dict) else None
    timestamp = details.get("quoteTimeInLong") if isinstance(details, dict) else None
    now_ms = time.time() * 1000
    if (price is None or price <= 0 or not isinstance(timestamp, (int, float))
        or type(timestamp) is bool or not math.isfinite(timestamp)
        or not 0 <= now_ms - timestamp <= max_age_seconds * 1000):
        raise PlanExecutionError(f"No fresh, timestamped Schwab quote for {symbol}")
    return price


def _available_shares(symbol: str) -> float:
    from ..api import get_positions
    accounts = get_positions()
    if len(accounts) != 1 or not isinstance(accounts[0].get("securitiesAccount"), dict):
        raise PlanExecutionError("Expected one complete account position snapshot")
    total = 0.0
    for holding in accounts[0]["securitiesAccount"].get("positions") or []:
        instrument = holding.get("instrument") or {}
        if str(instrument.get("symbol", "")).upper() != symbol:
            continue
        if instrument.get("assetType") not in ("EQUITY", "COLLECTIVE_INVESTMENT"):
            raise PlanExecutionError("Cannot sell unsupported instrument type")
        long_qty = holding.get("longQuantity", 0)
        short_qty = holding.get("shortQuantity", 0)
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in (long_qty, short_qty)):
            raise PlanExecutionError("Invalid broker position quantities")
        total += long_qty - short_qty
    return total


def _build_guarded_order(plan: TradePlan, *, step: str,
                         limit_price: float, max_notional: float,
                         quote_loader: Callable[[str], float]) -> SchwabOrder:
    if type(limit_price) not in (int, float) or not math.isfinite(limit_price) or limit_price <= 0:
        raise PlanExecutionError("A finite positive --plan-limit-price is required")
    if type(max_notional) not in (int, float) or not math.isfinite(max_notional) or max_notional <= 0:
        raise PlanExecutionError("A finite positive --plan-max-notional is required")
    selected = next((item for item in plan.steps if item.identifier == step), None)
    if selected is None or selected.terminal or len(selected.actions) != 1:
        raise PlanExecutionError("Expected a nonterminal, single-action plan step")
    action = selected.actions[0]
    if not isinstance(action, ActionCall) or action.verb not in {"BUY", "SELL", "CLOSE"}:
        raise PlanExecutionError("Only equity BUY, SELL and CLOSE are supported for live plans")
    if action.target is None:
        raise PlanExecutionError("Trade action lacks target")
    symbol = _live_stream(plan, action.target)
    if action.verb == "CLOSE":
        held = _available_shares(symbol)
        if not held.is_integer() or held <= 0:
            raise PlanExecutionError("CLOSE requires a positive integral long equity position")
        quantity = int(held)
    else:
        quantity = action.arguments.get("sizing")
        if type(quantity) is not int or quantity < 1:
            raise PlanExecutionError("Live sizing must be a positive integer number of shares")
    if limit_price * quantity > max_notional:
        raise PlanExecutionError("Order exceeds explicit maximum notional")
    reference = quote_loader(symbol)
    # Avoid accidental extreme limit prices regardless of order side.
    if action.verb == "BUY" and limit_price > reference * 1.05:
        raise PlanExecutionError("BUY limit price exceeds fresh quote by more than 5%")
    if action.verb in {"SELL", "CLOSE"} and limit_price < reference * 0.95:
        raise PlanExecutionError("SELL limit price is more than 5% below fresh quote")
    if action.verb == "SELL" and _available_shares(symbol) < quantity:
        raise PlanExecutionError("Insufficient long shares to SELL without shorting")
    return build_single_order(
        symbol=symbol, asset_type="EQUITY",
        instruction="SELL" if action.verb == "CLOSE" else action.verb,
        quantity=quantity, order_type="LIMIT", price=limit_price,
    )


def submit_plan_entry(
    plan: TradePlan, *, confirm_plan_id: str, limit_price: float,
    max_notional: float, journal_path: str | Path | None = None,
    snapshot_loader: Callable[[TradePlan], dict] = schwab_snapshot,
    quote_loader: Callable[[str], float] = _fresh_quote,
    sender: Callable[[SchwabOrder], str | None] = submit_order,
) -> SubmissionResult:
    """Submit the *entry* action once after explicit risk and condition gates.

    This is deliberately NOT a workflow runner: no successor execution, no
    polling-until-triggered, no retry, and no implied execution confirmation.
    """
    if confirm_plan_id != plan.identifier:
        raise PlanExecutionError("--confirm-plan-id must exactly match the loaded plan identifier")
    observation = observe_step(plan, snapshot_loader(plan))
    if not observation.triggered:
        return SubmissionResult(plan.identifier, plan.entry, "condition_false")
    order = _build_guarded_order(
        plan, step=plan.entry, limit_price=limit_price,
        max_notional=max_notional, quote_loader=quote_loader,
    )
    payload = order.to_create_payload()
    with PlanOrderJournal(journal_path) as journal:
        journal.claim(plan.identifier, plan.entry, payload)
        # The claim is committed before POST. Any exception leaves the outcome
        # unknown and prevents duplicates, including after process restarts.
        location = sender(order)
        journal.accepted(plan.identifier, plan.entry, location)
    return SubmissionResult(plan.identifier, plan.entry, "accepted_not_filled", location)

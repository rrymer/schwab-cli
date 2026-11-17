"""Read-only order execution monitoring and audit notifications."""
from __future__ import annotations

import json
import math
import sys
import time

from . import conf
from .api import get_order, SchwabAPIError
from .alerts import notify_terminal
from .integrations import Provider, EmailDeliveryError, gmail_provider, send_email

TERMINAL_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"})


def _filled_quantity(order: dict) -> float:
    value = order.get("filledQuantity")
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise SchwabAPIError("Order response lacks a valid filledQuantity")
    return value


def _execution_details(order: dict) -> dict:
    """Whitelist audit fields; exclude account identifiers and raw responses."""
    legs = [
        {"legId": leg.get("legId"), "symbol": (leg.get("instrument") or {}).get("symbol"),
         "instruction": leg.get("instruction"), "quantity": leg.get("quantity")}
        for leg in order.get("orderLegCollection") or []
    ]
    executions = [
        {"quantity": activity.get("quantity"),
         "executionLegs": [
             {key: leg.get(key) for key in ("legId", "price", "quantity", "time")}
             for leg in activity.get("executionLegs") or []
         ]}
        for activity in order.get("orderActivityCollection") or []
        if activity.get("activityType") == "EXECUTION" and activity.get("executionType") == "FILL"
    ]
    return {"orderId": order["orderId"], "status": order["status"],
            "filledQuantity": order["filledQuantity"],
            "remainingQuantity": order.get("remainingQuantity"),
            "orderLegs": legs, "executions": executions}


def monitor_order(
    order_id: int, *, interval: float = conf.ALERT_POLL_SECONDS,
    once: bool = False, stdout: bool = False, notify: bool = False,
    gmail_notify: bool = False, email_provider: Provider | None = None,
) -> None:
    """Report observed cumulative fill increases until the order is terminal.

    Initial fills are reported, including an already filled order. No order
    mutation, replacement following, persistent deduplication, or retry.
    """
    if type(order_id) is not int or not 0 < order_id < 2**63:
        raise ValueError("Order ID must be a positive Schwab int64 order ID")
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Order monitor interval must be positive and finite")
    if not (stdout or notify or gmail_notify or email_provider is not None):
        raise ValueError("Select at least one output: --stdout, --notify or --gmail-notify")
    if gmail_notify and email_provider is None:
        email_provider = gmail_provider()
    reported = 0.0
    try:
        while True:
            order = get_order(order_id)
            filled = _filled_quantity(order)
            status = order["status"]
            if filled > reported:
                event = "ORDER_FILLED" if status == "FILLED" else "ORDER_PARTIAL_FILL"
                subject = f"{event}: order {order_id} ({filled:g} filled)"
                details = _execution_details(order)
                details.update(event_type=event, previous_filled_quantity=reported,
                               newly_observed_filled_quantity=filled - reported)
                body = json.dumps(details, indent=2)
                reported = filled
                if stdout:
                    print(subject, body, sep="\n", flush=True)
                if notify:
                    notify_terminal(subject)
                if email_provider is not None:
                    try:
                        send_email(subject, body, provider=email_provider)
                    except EmailDeliveryError:
                        print("schwab: order email failed or submission outcome unknown",
                              file=sys.stderr, flush=True)
            if status in TERMINAL_STATUSES:
                print(f"Order {order_id}: {status}; filled quantity {filled:g}", file=sys.stderr)
                return
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        return

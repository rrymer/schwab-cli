"""Schwab order models and explicit order submission.

Order dataclasses and payload conversion adapted from Schwab-HW.py.
No credentials or account identifiers are stored here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from . import conf
from .api import BASE_URL, SchwabAPIError, account_numbers
from .auth import get_access_token
from .store import PositionStore
from .integrations import Provider, gmail_provider
from .order_monitor import monitor_order


def remove_none(obj: Any) -> Any:
    """Recursively remove None, retaining 0 and False."""
    if isinstance(obj, dict):
        return {k: remove_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [remove_none(v) for v in obj]
    return obj


@dataclass
class Instrument:
    symbol: str
    assetType: str | None = None
    cusip: str | None = None
    description: str | None = None
    instrumentId: int | None = None
    netChange: float | None = None
    type: str | None = None


@dataclass
class OrderLeg:
    instrument: Instrument
    instruction: str
    quantity: float
    orderLegType: str | None = None
    legId: int | None = None
    positionEffect: str | None = None
    quantityType: str | None = None
    divCapGains: str | None = None
    toSymbol: str | None = None


@dataclass
class ExecutionLeg:
    legId: int | None = None
    price: float | None = None
    quantity: float | None = None
    mismarkedQuantity: float | None = None
    instrumentId: int | None = None
    time: str | None = None


@dataclass
class OrderActivity:
    activityType: str | None = None
    executionType: str | None = None
    quantity: float | None = None
    orderRemainingQuantity: float | None = None
    executionLegs: list[ExecutionLeg] = field(default_factory=list)


@dataclass
class SchwabOrder:
    session: str = conf.DEFAULT_SESSION
    duration: str = conf.DEFAULT_DURATION
    orderType: str = "MARKET"
    orderStrategyType: str = "SINGLE"
    complexOrderStrategyType: str = "NONE"
    orderLegCollection: list[OrderLeg] = field(default_factory=list)
    quantity: float | None = None
    stopPrice: float | None = None
    stopPriceLinkBasis: str | None = None
    stopPriceLinkType: str | None = None
    stopPriceOffset: float | None = None
    stopType: str | None = None
    priceLinkBasis: str | None = None
    priceLinkType: str | None = None
    price: float | None = None
    activationPrice: float | None = None
    taxLotMethod: str | None = None
    specialInstruction: str | None = None
    destinationLinkName: str | None = None
    cancelTime: str | None = None
    releaseTime: str | None = None
    orderId: int | None = None
    accountNumber: int | str | None = None
    filledQuantity: float | None = None
    remainingQuantity: float | None = None
    cancelable: bool | None = None
    editable: bool | None = None
    status: str | None = None
    statusDescription: str | None = None
    enteredTime: str | None = None
    closeTime: str | None = None
    orderActivityCollection: list[OrderActivity] = field(default_factory=list)
    replacingOrderCollection: list[str] = field(default_factory=list)
    childOrderStrategies: list[SchwabOrder] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return remove_none(asdict(self))

    def to_create_payload(self) -> dict[str, Any]:
        data = self.to_dict()
        server_fields = {
            "orderId", "accountNumber", "filledQuantity", "remainingQuantity",
            "cancelable", "editable", "status", "statusDescription",
            "enteredTime", "closeTime", "orderActivityCollection",
            "replacingOrderCollection",
        }
        for key in server_fields:
            data.pop(key, None)
        for leg in data.get("orderLegCollection", []):
            for key in ("legId", "orderLegType", "positionEffect"):
                leg.pop(key, None)
            for key in ("instrumentId", "netChange"):
                leg["instrument"].pop(key, None)
        return data


def _positive_quantity(value: float) -> float:
    try:
        n = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Quantity must be a positive number") from exc
    if not n.is_finite() or n <= 0:
        raise ValueError("Quantity must be a finite positive number")
    return float(n)


def build_single_order(*, symbol: str, asset_type: str, instruction: str,
                       quantity: float, order_type: str = conf.DEFAULT_ORDER_TYPE,
                       price: float | None = None, session: str = conf.DEFAULT_SESSION,
                       duration: str = conf.DEFAULT_DURATION) -> SchwabOrder:
    """Build a single-leg order; no network request is performed."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Symbol is required")
    asset_type = asset_type.upper()
    instruction = instruction.upper()
    order_type = order_type.upper()
    if asset_type not in {"EQUITY", "OPTION"}:
        raise ValueError("Supported asset types: EQUITY, OPTION")
    permitted = ({"BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER"}
                 if asset_type == "EQUITY" else
                 {"BUY_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_CLOSE"})
    if instruction not in permitted:
        raise ValueError(f"Invalid {asset_type} instruction: {instruction}")
    if order_type not in {"LIMIT", "MARKET"}:
        raise ValueError("Supported order types: LIMIT, MARKET")
    if order_type == "LIMIT":
        if price is None:
            raise ValueError("LIMIT orders require --price")
        try:
            p = Decimal(str(price))
        except InvalidOperation as exc:
            raise ValueError("Price must be a nonnegative finite number") from exc
        if not p.is_finite() or p < 0:
            raise ValueError("Price must be a nonnegative finite number")
        price = float(p)
    elif price is not None:
        raise ValueError("MARKET orders must not specify --price")
    return SchwabOrder(
        session=session.upper(), duration=duration.upper(),
        orderType=order_type, price=price, orderStrategyType="SINGLE",
        complexOrderStrategyType="NONE",
        orderLegCollection=[OrderLeg(
            instrument=Instrument(symbol=symbol, assetType=asset_type),
            instruction=instruction, quantity=_positive_quantity(quantity),
        )],
    )


def submit_order(order: SchwabOrder) -> str | None:
    """Submit an order to the unique account. THIS CAN PLACE A LIVE TRADE.

    Returns the Schwab Location header (order URL) when supplied.
    No automatic retry: repeating a POST could create a duplicate order.
    """
    payload = order.to_create_payload()
    account_hash = account_numbers()
    url = f"{BASE_URL}/accounts/{quote(account_hash, safe='')}/orders"
    request = Request(
        url,
        data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {get_access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=conf.HTTP_TIMEOUT_SECONDS) as response:
            return response.headers.get("Location")
    except HTTPError as exc:
        # Deliberately avoid dumping Schwab response bodies, which may contain account data.
        raise SchwabAPIError(f"Schwab rejected order: HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise SchwabAPIError(
            "Order submission outcome is unknown (network failure). "
            "Check Schwab orders before trying again."
        ) from exc


def add_position(*, symbol: str, asset_type: str, instruction: str,
                 quantity: float, order_type: str = conf.DEFAULT_ORDER_TYPE, price: float | None = None,
                 session: str = conf.DEFAULT_SESSION, duration: str = conf.DEFAULT_DURATION,
                 submit: bool = False, gmail_notify: bool = False,
                 email_provider: Provider | None = None) -> None:
    order = build_single_order(
        symbol=symbol, asset_type=asset_type, instruction=instruction,
        quantity=quantity, order_type=order_type, price=price,
        session=session, duration=duration,
    )
    print(json.dumps(order.to_create_payload(), indent=2))
    if not submit:
        print("DRY RUN: no order submitted. Add --submit to transmit a LIVE order.")
        return
    if gmail_notify and email_provider is None:
        email_provider = gmail_provider()
    instrument_key = PositionStore.key({'symbol': symbol, 'assetType': asset_type})
    with PositionStore() as store:
        existing_id = store.active_id(instrument_key)
        tracking_id = store.reserve_order(instrument_key)
    print(f"Local order tracking UUID: {tracking_id}")
    if existing_id:
        print(f"Existing position UUID: {existing_id} (order may change this position)")
    try:
        location = submit_order(order)
    except Exception:
        print(f"Submission not confirmed; tracking UUID retained: {tracking_id}")
        print("Check Schwab order history before retrying; never assume the POST failed.")
        raise
    with PositionStore() as store:
        store.record_order_location(tracking_id, location)
    print("Schwab accepted the order request; execution is NOT confirmed.")
    if location:
        print(f"Order location: {location}")

    if email_provider is not None:
        # Never follow the Location URL directly or use it to select an account.
        # Poll the numeric ID via our existing authenticated read-only endpoint.
        try:
            parts = urlsplit(location or "").path.rstrip("/").split("/")
            if len(parts) < 2 or parts[-2] != "orders" or not parts[-1].isascii() or not parts[-1].isdigit():
                raise ValueError("Missing broker order ID")
            order_id = int(parts[-1])
            if not 0 < order_id < 2**63:
                raise ValueError("Invalid broker order ID")
            monitor_order(order_id, email_provider=email_provider)
        except (ValueError, SchwabAPIError, OSError) as exc:
            print("schwab: order accepted, but execution monitoring failed; "
                  "check Schwab order history before retrying. "
                  f"({type(exc).__name__})", file=sys.stderr, flush=True)

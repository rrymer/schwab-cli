"""Command-line entry point for the local Schwab client."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

class CommandError(Exception):
    """A user-facing command failure."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schwab",
        description="Schwab account and market-data CLI",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    auth = commands.add_parser("auth", help="Manage Schwab authorization")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_commands.add_parser("login", help="Authorize interactively")
    auth_commands.add_parser("status", help="Show local authorization status")
    auth_commands.add_parser(
        "refresh",
        help="Force an access-token refresh using the stored refresh token",
    )

    positions = commands.add_parser("positions", help="Work with account positions")
    positions_commands = positions.add_subparsers(dest="positions_command", required=True)
    positions_list_parser = positions_commands.add_parser("list", help="List positions")
    positions_list_parser.add_argument("--json", action="store_true")

    positions_add = positions_commands.add_parser(
        "add",
        help="Preview or submit a single-leg order",
    )

    positions_add.add_argument("symbol")
    positions_add.add_argument("instruction")
    positions_add.add_argument("quantity", type=float)

    positions_add.add_argument(
        "--asset-type",
        choices=["EQUITY", "OPTION"],
        default="EQUITY",
    )
    positions_add.add_argument(
        "--order-type",
        choices=["LIMIT", "MARKET"],
        default="LIMIT",
    )
    positions_add.add_argument("--price", type=float)
    positions_add.add_argument("--session", default="NORMAL")
    positions_add.add_argument("--duration", default="DAY")
    positions_add.add_argument(
        "--submit",
        action="store_true",
        help="Transmit a live order to Schwab",
    )
    positions_monitor = positions_commands.add_parser(
        "monitor",
        help="Monitor Schwab positions",
    )

    positions_monitor.add_argument(
        "position_id",
        nargs="?",
        default=None,
        help="UUID of the position to monitor (omit for all positions)",
    )

    positions_monitor.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="Polling interval in seconds (default: 15)",
    )

    positions_monitor.add_argument(
        "--once",
        action="store_true",
        help="Fetch one snapshot instead of continuous monitoring",
    )
    analytics = commands.add_parser("analytics", help="Calculate market indicators")
    analytics_commands = analytics.add_subparsers(dest="analytics_command", required=True)
    analytics_market = analytics_commands.add_parser(
        "market", help="Compute indicators for a market symbol (not an account position)"
    )
    analytics_market.add_argument("symbol")
    analytics_market.add_argument(
        "--fields", required=True,
        help="Comma-separated indicators, e.g. rsi_14,atr_14,vwap_session",
    )

    market = commands.add_parser("market", help="Read Schwab market data")
    market_commands = market.add_subparsers(dest="market_command", required=True)
    history = market_commands.add_parser("history", help="Historical OHLCV prices")
    history.add_argument("symbol")
    history.add_argument("--period-type", choices=("day", "month", "year", "ytd"), default="year")
    history.add_argument("--period", type=int, default=1)
    history.add_argument("--frequency-type", choices=("minute", "daily", "weekly", "monthly"), default="daily")
    history.add_argument("--frequency", type=int, default=1)
    history.add_argument("--start", help="UTC start date YYYY-MM-DD")
    history.add_argument("--end", help="UTC end date YYYY-MM-DD")
    history.add_argument("--extended-hours", action="store_true")
    history.add_argument("--limit", type=int, default=10, help="Display last N candles")
    history_output = history.add_mutually_exclusive_group()
    history_output.add_argument("--json", action="store_true", help="Print the complete API response")
    history_output.add_argument("--fields", help="Comma-separated candle fields to print as CSV")

    options = commands.add_parser("options", help="Read option chain data")
    option_commands = options.add_subparsers(dest="options_command", required=True)
    expirations = option_commands.add_parser("expirations", help="List contract expirations")
    expirations.add_argument("symbol")
    expirations.add_argument("--include-0dte", action="store_true")
    expiration_output = expirations.add_mutually_exclusive_group()
    expiration_output.add_argument("--json", action="store_true")
    expiration_output.add_argument("--fields", help="Comma-separated expiration fields to print as CSV")
    chain = option_commands.add_parser("chain", help="Retrieve an option chain")
    chain.add_argument("symbol")
    chain.add_argument("--type", choices=("CALL", "PUT", "ALL"), default="ALL", dest="contract_type")
    chain.add_argument("--from-date", help="First expiration YYYY-MM-DD")
    chain.add_argument("--to-date", help="Last expiration YYYY-MM-DD")
    chain.add_argument("--strike-count", type=int, help="Number of strikes around ATM")
    chain.add_argument("--limit", type=int, default=20, help="Display up to N contracts")
    chain_output = chain.add_mutually_exclusive_group()
    chain_output.add_argument("--json", action="store_true", help="Print the complete API response")
    chain_output.add_argument("--fields", help="Comma-separated contract fields to print as CSV")

    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "auth":
        if args.auth_command == "login":
            from .auth import login
            login()
            print("Schwab authorization successful.")
            return 0

        if args.auth_command == "status":
            from .auth import status
            for key, value in status().items():
                print(f"{key}: {value}")
            return 0

        if args.auth_command == "refresh":
            from .auth import refresh_access_token, status

            refresh_access_token()
            remaining = status()["access_token_seconds_remaining"]

            print("Schwab access token refreshed successfully.")
            print(f"Expires in: {remaining} seconds")
            return 0

    if args.command == "positions":
        if args.positions_command == "list":
            from .positions import list_positions

            list_positions(as_json=args.json)
            return 0

        if args.positions_command == "add":
            from .orders import add_position

            add_position(
                symbol=args.symbol,
                asset_type=args.asset_type,
                instruction=args.instruction,
                quantity=args.quantity,
                order_type=args.order_type,
                price=args.price,
                session=args.session,
                duration=args.duration,
                submit=args.submit,
            )
            return 0

        if args.positions_command == "monitor":
            from .monitor import monitor

            monitor(
                position_id=args.position_id,
                interval=args.interval,
                once=args.once,
            )
            return 0

    if args.command == "analytics" and args.analytics_command == "market":
        import json
        from .analytics_service import calculate_market

        result = calculate_market(args.symbol, args.fields)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "market" and args.market_command == "history":
        import json
        from datetime import date
        from .market_data import get_price_history
        from .field_selection import parse_fields, print_fields

        if args.limit < 0:
            raise CommandError("--limit must be nonnegative")
        fields = parse_fields(args.fields) if args.fields is not None else None
        result = get_price_history(
            args.symbol, period_type=args.period_type, period=args.period,
            frequency_type=args.frequency_type, frequency=args.frequency,
            start=date.fromisoformat(args.start) if args.start else None,
            end=date.fromisoformat(args.end) if args.end else None,
            extended_hours=args.extended_hours,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        elif fields is not None:
            candles = result["candles"]
            print_fields(candles[-args.limit:] if args.limit else [], fields)
        else:
            candles = result["candles"]
            print(f"{result.get('symbol', args.symbol)}: {len(candles)} candles")
            for candle in (candles[-args.limit:] if args.limit else []):
                print(f"{candle.get('datetime')}  "
                      f"O:{candle.get('open')} H:{candle.get('high')} "
                      f"L:{candle.get('low')} C:{candle.get('close')} "
                      f"V:{candle.get('volume')}")
        return 0

    if args.command == "options" and args.options_command == "expirations":
        import json
        from .market_data import get_option_expirations
        from .field_selection import parse_fields, print_fields

        fields = parse_fields(args.fields) if args.fields is not None else None
        expirations = get_option_expirations(args.symbol)
        if not args.include_0dte:
            expirations = [x for x in expirations
                           if isinstance(x, dict) and x.get("daysToExpiration", -1) > 0]
        if args.json:
            print(json.dumps(expirations, indent=2))
        elif fields is not None:
            print_fields(expirations, fields)
        else:
            for x in expirations:
                print(f"{x.get('expirationDate')}  "
                      f"DTE:{x.get('daysToExpiration')}  "
                      f"Type:{x.get('expirationType', '-')}")
        return 0

    if args.command == "options" and args.options_command == "chain":
        import json
        from itertools import islice
        from .market_data import get_option_chain, iter_option_contracts
        from .field_selection import parse_fields, print_fields

        if args.limit < 0:
            raise CommandError("--limit must be nonnegative")
        fields = parse_fields(args.fields) if args.fields is not None else None
        chain = get_option_chain(
            args.symbol, contract_type=args.contract_type,
            from_date=args.from_date, to_date=args.to_date,
            strike_count=args.strike_count,
        )
        if args.json:
            print(json.dumps(chain, indent=2))
        elif fields is not None:
            contracts = islice(iter_option_contracts(chain), args.limit)
            rows = (
                {**contract, "side": side, "expirationKey": expiration,
                 "strikeKey": strike}
                for side, expiration, strike, contract in contracts
            )
            print_fields(rows, fields)
        else:
            print(f"{chain.get('symbol', args.symbol)}  "
                  f"Underlying:{chain.get('underlyingPrice', '-')}  "
                  f"Delayed:{chain.get('isDelayed', '-')}")
            for side, expiration, strike, contract in islice(iter_option_contracts(chain), args.limit):
                print(f"{side:4} {expiration:17} {strike:>8}  "
                      f"Bid:{contract.get('bidPrice', '-')}  "
                      f"Ask:{contract.get('askPrice', '-')}  "
                      f"OI:{contract.get('openInterest', '-')}")
        return 0

    raise CommandError("Unknown command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from .api import SchwabAPIError
    from .auth import AuthError
    try:
        return dispatch(args)
    except (CommandError, ValueError, SchwabAPIError, AuthError) as exc:
        print(f"schwab: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

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
    market = commands.add_parser("market", help="Read Schwab market data")
    market_commands = market.add_subparsers(dest="market_command", required=True)
    history = market_commands.add_parser("history", help="Fetch historical OHLCV candles")
    history.add_argument("symbol")
    history.add_argument("--period-type", choices=["day", "month", "year", "ytd"], default="year")
    history.add_argument("--period", type=int, default=1)
    history.add_argument("--frequency-type", choices=["minute", "daily", "weekly", "monthly"], default="daily")
    history.add_argument("--frequency", type=int, default=1)
    history.add_argument("--start", help="Start date YYYY-MM-DD (UTC)")
    history.add_argument("--end", help="End date YYYY-MM-DD (UTC)")
    history.add_argument("--extended-hours", action="store_true")
    history.add_argument("--limit", type=int, default=10, help="Display last N candles (default 10)")
    history.add_argument("--json", action="store_true", help="Print entire raw API response")

    options = commands.add_parser("options", help="Read option expiration and chain data")
    options_commands = options.add_subparsers(dest="options_command", required=True)
    expiries = options_commands.add_parser("expirations", help="List option expirations")
    expiries.add_argument("symbol")
    expiries.add_argument("--include-0dte", action="store_true")
    expiries.add_argument("--json", action="store_true", help="Print raw expiration entries")
    chain = options_commands.add_parser("chain", help="Fetch option chain")
    chain.add_argument("symbol")
    chain.add_argument("--type", dest="contract_type", choices=["CALL", "PUT", "ALL"], default="ALL")
    chain.add_argument("--from-date", help="Earliest expiration YYYY-MM-DD")
    chain.add_argument("--to-date", help="Latest expiration YYYY-MM-DD")
    chain.add_argument("--strike-count", type=int)
    chain.add_argument("--json", action="store_true", help="Print entire raw API response")
    chain.add_argument("--limit", type=int, default=20, help="Display first N contracts (default 20)")

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

    if args.command == "market" and args.market_command == "history":
        from datetime import date
        import json
        from .market_data import get_price_history

        if args.limit < 0:
            raise CommandError("--limit must be nonnegative")
        data = get_price_history(
            args.symbol, period_type=args.period_type, period=args.period,
            frequency_type=args.frequency_type, frequency=args.frequency,
            start=date.fromisoformat(args.start) if args.start else None,
            end=date.fromisoformat(args.end) if args.end else None,
            extended_hours=args.extended_hours,
        )
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            candles = data["candles"]
            print(f"{data.get('symbol', args.symbol)}: {len(candles)} candle(s)")
            for candle in candles[-args.limit:] if args.limit else []:
                print(f"{candle.get('datetime')}  O:{candle.get('open')} "
                      f"H:{candle.get('high')} L:{candle.get('low')} "
                      f"C:{candle.get('close')} V:{candle.get('volume')}")
        return 0

    if args.command == "options" and args.options_command == "expirations":
        import json
        from .market_data import get_option_expirations

        expirations = get_option_expirations(args.symbol)
        if not args.include_0dte:
            expirations = [e for e in expirations if e.get("daysToExpiration", -1) > 0]
        if args.json:
            print(json.dumps(expirations, indent=2))
        else:
            for expiry in expirations:
                print(f"{expiry.get('expirationDate')}  "
                      f"DTE:{expiry.get('daysToExpiration')}  "
                      f"Type:{expiry.get('expirationType', '-')}")
        return 0

    if args.command == "options" and args.options_command == "chain":
        import json
        from .market_data import get_option_chain

        if args.limit < 0:
            raise CommandError("--limit must be nonnegative")
        data = get_option_chain(
            args.symbol, contract_type=args.contract_type,
            from_date=args.from_date, to_date=args.to_date,
            strike_count=args.strike_count,
        )
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"{data.get('symbol', args.symbol)} "
                  f"underlying:{data.get('underlyingPrice', '-')} "
                  f"delayed:{data.get('isDelayed', '-')}")
            shown = 0
            for side, key in (("CALL", "callExpDateMap"), ("PUT", "putExpDateMap")):
                for expiration, strikes in (data.get(key) or {}).items():
                    for strike, contracts in strikes.items():
                        if isinstance(contracts, dict):
                            contracts = [contracts]
                        for contract in contracts:
                            if shown >= args.limit:
                                return 0
                            print(f"{side:4} {expiration:16} {strike:>8} "
                                  f"bid:{contract.get('bidPrice', '-')} "
                                  f"ask:{contract.get('askPrice', '-')} "
                                  f"OI:{contract.get('openInterest', '-')}")
                            shown += 1
        return 0

    raise CommandError("Unknown command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except CommandError as exc:
        print(f"schwab: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

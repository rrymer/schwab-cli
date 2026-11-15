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

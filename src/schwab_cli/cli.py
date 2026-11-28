"""Command-line entry point for the local Schwab client."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import conf

class CommandError(Exception):
    """A user-facing command failure."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schwab",
        description="Schwab account and market-data CLI",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create ~/.schwab/config.yaml and positions.sqlite3 if missing")

    db = commands.add_parser("db", help="Manage local SQLite storage")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    db_commands.add_parser("init", help="Initialize a new database")
    db_commands.add_parser("status", help="Show path and schema version")
    db_commands.add_parser("migrate", help="Apply schema migrations")
    db_commands.add_parser("schema", help="Print current SQL schema")
    db_backup = db_commands.add_parser("backup", help="Create a consistent database snapshot")
    db_backup.add_argument("destination", nargs="?", help="Destination file or directory")
    db_backup.add_argument("--source", help="Source database path; defaults to DB_PATH")

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
    positions_list_parser.add_argument("--include-ids", action="store_true", help="Show locally assigned position UUIDs")
    positions_list_parser.add_argument("--technicals", action="store_true", help="Include 5m RSI, session volume and 10-day ADV")

    positions_funds = positions_commands.add_parser("funds", help="Display account cash and buying power")
    positions_funds.add_argument("--json", action="store_true", help="Return labeled USD balances")

    positions_add = positions_commands.add_parser(
        "add",
        help="Preview a YAML TradePlan or preview/submit a single-leg order",
    )

    positions_add.add_argument("symbol", nargs="?")
    positions_add.add_argument("instruction", nargs="?")
    positions_add.add_argument("quantity", nargs="?", type=float)
    positions_add.add_argument("--plan", metavar="PATH", help="Path to a YAML TradePlan")
    positions_add.add_argument("--plan-limit-price", type=float,
                               help="Required explicit LIMIT price for live plan entry")
    positions_add.add_argument("--plan-max-notional", type=float,
                               help="Required dollar cap for live plan entry")
    positions_add.add_argument("--confirm-plan-id",
                               help="Required exact plan ID acknowledgement for live submission")

    positions_add.add_argument(
        "--asset-type",
        choices=["EQUITY", "OPTION"],
        default=conf.DEFAULT_ASSET_TYPE,
    )
    positions_add.add_argument(
        "--order-type",
        choices=["LIMIT", "MARKET"],
        default=conf.DEFAULT_ORDER_TYPE,
    )
    positions_add.add_argument("--price", type=float)
    positions_add.add_argument("--session", default=conf.DEFAULT_SESSION)
    positions_add.add_argument("--duration", default=conf.DEFAULT_DURATION)
    positions_add.add_argument(
        "--submit",
        action="store_true",
        help="Transmit a live order to Schwab",
    )
    positions_add.add_argument("--gmail-notify", action="store_true", help="Wait for execution and email confirmed fills (with --submit)")
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
        default=conf.POSITION_POLL_SECONDS,
        help=f"Polling interval in seconds (default: {conf.POSITION_POLL_SECONDS:g})",
    )

    positions_monitor.add_argument("--include-ids", action="store_true", help="Show position UUIDs")
    positions_monitor.add_argument("--technicals", action="store_true", help="Include 5m RSI, session volume and 10-day ADV")
    positions_monitor.add_argument("--in-place", action="store_true", help="Clear and redraw the same terminal area on each refresh")

    positions_monitor.add_argument(
        "--once",
        action="store_true",
        help="Fetch one snapshot instead of continuous monitoring",
    )
    monitor_group = commands.add_parser("monitor", help="Monitoring services and alerts")
    monitor_commands = monitor_group.add_subparsers(dest="monitor_command", required=True)
    monitor_plan = monitor_commands.add_parser(
        "plan", help="Evaluate TradePlan WHEN conditions (read-only)"
    )
    monitor_plan.add_argument("--plan", required=True, metavar="PATH")
    monitor_plan.add_argument("--step", help="Named step to observe (default: entry)")
    monitor_plan.add_argument("--interval", type=float, default=conf.ALERT_POLL_SECONDS)
    monitor_plan.add_argument("--once", action="store_true", help="Evaluate a single snapshot")
    monitor_plan.add_argument("--submit", action="store_true",
                              help="Submit the entry order once when WHEN becomes true")
    monitor_plan.add_argument("--plan-limit-price", type=float)
    monitor_plan.add_argument("--plan-max-notional", type=float)
    monitor_plan.add_argument("--confirm-plan-id")

    monitor_alert = monitor_commands.add_parser(
        "alert", help="Notify when a monitored metric reaches a threshold"
    )
    monitor_target = monitor_alert.add_mutually_exclusive_group(required=True)
    monitor_target.add_argument("--symbol", help="Market ticker to monitor")
    monitor_target.add_argument("--position-id", help="Active local position UUID")
    monitor_alert.add_argument("--metric", default=conf.DEFAULT_ALERT_METRIC, help="Registered metric (currently: price)")
    monitor_level = monitor_alert.add_mutually_exclusive_group(required=True)
    monitor_level.add_argument("--above", type=float, help="Notify at or above this level")
    monitor_level.add_argument("--below", type=float, help="Notify at or below this level")
    monitor_alert.add_argument("--interval", type=float, default=conf.ALERT_POLL_SECONDS, help="Poll interval in seconds")
    monitor_alert.add_argument("--stdout", action="store_true", help="Print triggered alerts to stdout")
    monitor_alert.add_argument("--notify", action="store_true", help="Request a terminal system notification")
    monitor_alert.add_argument("--gmail-notify", action="store_true", help="Email triggered alerts via Gmail")
    monitor_alert.add_argument("--once", action="store_true", help="Evaluate only one sample instead of waiting for a trigger")

    analytics = commands.add_parser("analytics", help="Price history and market indicators")
    analytics_commands = analytics.add_subparsers(dest="analytics_command", required=True)
    calculate = analytics_commands.add_parser("calculate", help="Calculate live position metrics")
    calculate.add_argument("--position-id", required=True, help="Active local position UUID")
    calculate.add_argument("--metric", choices=("pnl",), required=True, help="Position metric (pnl)")

    history = analytics_commands.add_parser("history", help="Price candles or a technical indicator")
    history.add_argument("symbol")
    history.add_argument("--metric", default=conf.DEFAULT_HISTORY_METRIC, help="price (default), rsi_N, atr_N, or vwap_session")
    history.add_argument("--period-type", choices=("day", "month", "year", "ytd"), default=conf.HISTORY_PERIOD_TYPE)
    history.add_argument("--period", type=int, default=conf.HISTORY_PERIOD)
    history.add_argument("--frequency-type", choices=("minute", "daily", "weekly", "monthly"), default=conf.HISTORY_FREQUENCY_TYPE)
    history.add_argument("--frequency", type=int, default=conf.HISTORY_FREQUENCY)
    history.add_argument("--start", help="UTC start date YYYY-MM-DD")
    history.add_argument("--end", help="UTC end date YYYY-MM-DD")
    history.add_argument("--extended-hours", action="store_true")
    history.add_argument("--limit", type=int, default=conf.HISTORY_DISPLAY_LIMIT, help="Display last N candles")
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
    chain.add_argument("--type", choices=("CALL", "PUT", "ALL"), default=conf.DEFAULT_CONTRACT_TYPE, dest="contract_type")
    chain.add_argument("--from-date", help="First expiration YYYY-MM-DD")
    chain.add_argument("--to-date", help="Last expiration YYYY-MM-DD")
    chain.add_argument("--strike-count", type=int, help="Number of strikes around ATM")
    chain.add_argument("--limit", type=int, default=conf.CHAIN_DISPLAY_LIMIT, help="Display up to N contracts")
    chain_output = chain.add_mutually_exclusive_group()
    chain_output.add_argument("--json", action="store_true", help="Print the complete API response")
    chain_output.add_argument("--fields", help="Comma-separated contract fields to print as CSV")

    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "init":
        created = conf.ensure_config_file()
        action = "Created" if created else "Already exists (unchanged)"
        print(f"{action}: {conf.CONFIG_PATH}")
        from . import database

        version = database.initialize()
        print(f"Database initialized: {database.default_db_path()} (schema {version})")
        return 0

    if args.command == "db":
        import json
        from . import database

        if args.db_command == "init":
            version = database.initialize()
            print(f"Database initialized: {database.default_db_path()} (schema {version})")
        elif args.db_command == "status":
            print(json.dumps(database.status(), indent=2))
        elif args.db_command == "migrate":
            version = database.migrate()
            print(f"Database schema version: {version}")
        elif args.db_command == "schema":
            print(database.schema_sql(), end="")
        elif args.db_command == "backup":
            print(f"Backup created: {database.backup(args.destination, source=args.source)}")
        return 0

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

            list_positions(as_json=args.json, include_ids=args.include_ids,
                           show_technicals=args.technicals)
            return 0

        if args.positions_command == "funds":
            from .funds import show_funds

            show_funds(as_json=args.json)
            return 0

        if args.positions_command == "add":
            if args.plan is not None:
                if any(value is not None for value in (args.symbol, args.instruction, args.quantity)):
                    raise CommandError("--plan cannot be combined with symbol, instruction or quantity")
                if (args.asset_type != conf.DEFAULT_ASSET_TYPE
                    or args.order_type != conf.DEFAULT_ORDER_TYPE
                    or args.price is not None
                    or args.session != conf.DEFAULT_SESSION
                    or args.duration != conf.DEFAULT_DURATION):
                    raise CommandError("--plan cannot be combined with single-leg order options")
                if args.gmail_notify:
                    raise CommandError("--gmail-notify is not supported for TradePlan submissions")
                from .tradeplans import get_loader
                plan = get_loader(args.plan).load()
                if args.submit:
                    if any(x is None for x in (
                        args.plan_limit_price, args.plan_max_notional, args.confirm_plan_id
                    )):
                        raise CommandError(
                            "Live plan submission requires --plan-limit-price, "
                            "--plan-max-notional and --confirm-plan-id"
                        )
                    from .tradeplans.execution import submit_plan_entry
                    result = submit_plan_entry(
                        plan, confirm_plan_id=args.confirm_plan_id,
                        limit_price=args.plan_limit_price,
                        max_notional=args.plan_max_notional,
                    )
                    import json
                    print(json.dumps(result.__dict__, indent=2))
                else:
                    if any(x is not None for x in (
                        args.plan_limit_price, args.plan_max_notional, args.confirm_plan_id
                    )):
                        raise CommandError("Live plan order options require --submit")
                    from .tradeplans.utils import preview_trade_plan
                    preview_trade_plan(args.plan)
                return 0
            if any(x is not None for x in (
                args.plan_limit_price, args.plan_max_notional, args.confirm_plan_id
            )):
                raise CommandError("--plan-limit-price/--plan-max-notional/--confirm-plan-id require --plan")
            if args.symbol is None or args.instruction is None or args.quantity is None:
                raise CommandError("positions add requires SYMBOL INSTRUCTION QUANTITY or --plan PATH")
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
                submit=args.submit, gmail_notify=args.gmail_notify,
            )
            return 0

        if args.positions_command == "monitor":
            from .monitor import monitor

            monitor(
                position_id=args.position_id,
                interval=args.interval,
                once=args.once,
                include_ids=args.include_ids,
                in_place=args.in_place,
                show_technicals=args.technicals,
            )
            return 0

    if args.command == "monitor" and args.monitor_command == "plan":
        import json
        import time
        from .tradeplans import get_loader
        from .tradeplans.monitor import observe_step, schwab_snapshot

        if not 0.1 <= args.interval <= 3600:
            raise CommandError("--interval must be between 0.1 and 3600 seconds")
        if args.submit:
            if args.step is not None:
                raise CommandError("Live plan monitor can only submit the entry step")
            if any(x is None for x in (
                args.plan_limit_price, args.plan_max_notional, args.confirm_plan_id
            )):
                raise CommandError(
                    "Live plan monitor requires --plan-limit-price, "
                    "--plan-max-notional and --confirm-plan-id"
                )
        elif any(x is not None for x in (
            args.plan_limit_price, args.plan_max_notional, args.confirm_plan_id
        )):
            raise CommandError("Live plan order options require --submit")
        plan = get_loader(args.plan).load()
        if args.submit and args.confirm_plan_id != plan.identifier:
            raise CommandError("--confirm-plan-id does not match the loaded plan")
        while True:
            context = schwab_snapshot(plan)
            observation = observe_step(plan, context, step_name=args.step)
            print(json.dumps({
                "plan": observation.plan_id, "step": observation.step,
                "triggered": observation.triggered, "action": observation.action,
                "next_step": observation.next_step,
            }), flush=True)
            if args.submit and observation.triggered:
                from .tradeplans.execution import submit_plan_entry
                result = submit_plan_entry(
                    plan, confirm_plan_id=args.confirm_plan_id,
                    limit_price=args.plan_limit_price,
                    max_notional=args.plan_max_notional,
                )
                print(json.dumps(result.__dict__, indent=2), flush=True)
                if result.status != "condition_false":
                    return 0
            if args.once:
                return 0
            time.sleep(args.interval)

    if args.command == "monitor" and args.monitor_command == "alert":
        from .alerts import alert

        alert(
            symbol=args.symbol, position_id=args.position_id,
            metric=args.metric, above=args.above, below=args.below,
            interval=args.interval, once=args.once, exit_on_trigger=True,
            stdout=args.stdout, notify=args.notify, gmail_notify=args.gmail_notify,
        )
        return 0

    if args.command == "analytics" and args.analytics_command == "calculate":
        from .position_metrics import show_position_metric

        show_position_metric(args.position_id, args.metric)
        return 0

    if args.command == "analytics" and args.analytics_command == "history":
        import json
        from datetime import date
        from .market_data import get_price_history
        from .field_selection import parse_fields, print_fields

        if args.limit < 0:
            raise CommandError("--limit must be nonnegative")
        if args.metric != "price":
            from .analytics_service import calculate_market, resolve_metric

            # Reject incompatible candle-only settings rather than silently ignore them.
            resolve_metric(args.metric)
            if args.fields is not None or args.json:
                raise CommandError("--fields and --json only apply to --metric price")
            if (args.period_type, args.period, args.frequency_type, args.frequency,
                args.start, args.end, args.extended_hours, args.limit) != (
                conf.HISTORY_PERIOD_TYPE, conf.HISTORY_PERIOD,
                conf.HISTORY_FREQUENCY_TYPE, conf.HISTORY_FREQUENCY,
                None, None, False, conf.HISTORY_DISPLAY_LIMIT
            ):
                raise CommandError("History range/frequency/limit options only apply to --metric price")
            print(json.dumps(calculate_market(args.symbol, (args.metric,)), indent=2))
            return 0
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
    from .integrations import EmailDeliveryError
    from .database import DatabaseError
    from sqlite3 import Error as SQLiteError
    try:
        if args.command != "init":
            conf.ensure_config_file()
        return dispatch(args)
    except (CommandError, ValueError, OSError, SchwabAPIError, AuthError, EmailDeliveryError, DatabaseError, SQLiteError) as exc:
        print(f"schwab: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Validated flat YAML configuration with non-secret application defaults.

Only documented scalar settings from ~/.schwab/config.yaml are supported.
Credentials remain in macOS Keychain, not in YAML.
"""

from pathlib import Path
import json
import math
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Monitoring and requests (seconds).
POSITION_POLL_SECONDS = 15.0
ALERT_POLL_SECONDS = 15.0
HTTP_TIMEOUT_SECONDS = 30
LOGIN_TIMEOUT_SECONDS = 180
CALLBACK_POLL_SECONDS = 0.5
TOKEN_REFRESH_BUFFER_SECONDS = 60
ACCESS_TOKEN_FALLBACK_SECONDS = 1800

# CLI / broker order defaults.
DEFAULT_ASSET_TYPE = "EQUITY"
DEFAULT_ORDER_TYPE = "LIMIT"
DEFAULT_SESSION = "NORMAL"
DEFAULT_DURATION = "DAY"
DEFAULT_ALERT_METRIC = "price"
DEFAULT_HISTORY_METRIC = "price"
DEFAULT_CONTRACT_TYPE = "ALL"

# Historical price queries and output.
HISTORY_PERIOD_TYPE = "year"
HISTORY_PERIOD = 1
HISTORY_FREQUENCY_TYPE = "daily"
HISTORY_FREQUENCY = 1
HISTORY_DISPLAY_LIMIT = 10
CHAIN_DISPLAY_LIMIT = 20
ANALYTICS_DAILY_PERIOD_TYPE = "year"
ANALYTICS_DAILY_PERIOD = 1
ANALYTICS_INTRADAY_PERIOD_TYPE = "day"
ANALYTICS_INTRADAY_PERIOD = 1

# Regular US equity session and dashboard technicals.
MARKET_TIMEZONE = "America/New_York"
REGULAR_SESSION_START_MINUTE = 9 * 60 + 30
REGULAR_SESSION_END_MINUTE = 16 * 60
DASHBOARD_RSI_PERIOD = 14
DASHBOARD_BAR_MINUTES = 5
DASHBOARD_INTRADAY_DAYS = 5
DASHBOARD_ADV_SESSIONS = 10
DASHBOARD_DAILY_PERIOD_TYPE = "month"
DASHBOARD_DAILY_PERIOD = 1

# Standard contract fallback; nonstandard multipliers must be verified.
STANDARD_OPTION_MULTIPLIER = 100.0


# OAuth callback and Keychain identifiers. These are names, never secrets.
REDIRECT_URI = "https://127.0.0.1/callback"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765
KEYCHAIN_ACCOUNT = "schwabbot"
KEYCHAIN_SERVICE_CLIENT_ID = "client_id"
KEYCHAIN_SERVICE_CLIENT_SECRET = "client_secret"
KEYCHAIN_SERVICE_ACCESS_TOKEN = "access_token"
KEYCHAIN_SERVICE_REFRESH_TOKEN = "refresh_token"
KEYCHAIN_SERVICE_ACCESS_EXPIRES_AT = "access_expires_at"

# A flat YAML mapping is intentionally supported without a third-party parser.
# Configuration is loaded once at process import; restart a long-running
# monitor after modifying ~/.schwab/config.yaml.
CONFIG_PATH = Path.home() / ".schwab" / "config.yaml"
_DEFAULTS = {
    name: value for name, value in tuple(globals().items())
    if name.isupper() and not name.startswith("_")
    and isinstance(value, (str, int, float)) and not isinstance(value, bool)
}


def _uncomment(value: str) -> str:
    """Strip inline # comments outside quoted strings."""
    quote = None
    escaped = False
    for i, char in enumerate(value):
        if quote == '"' and char == '\\' and not escaped:
            escaped = True
            continue
        if char in ("'", '"') and not escaped:
            if quote is None:
                quote = char
            elif quote == char:
                # Double single quotes are valid YAML escaping, handled later.
                if quote == "'" and i + 1 < len(value) and value[i + 1] == "'":
                    continue
                quote = None
        if char == '#' and quote is None and (i == 0 or value[i - 1].isspace()):
            return value[:i].strip()
        escaped = False
    if quote is not None:
        raise ValueError("Unterminated YAML quote")
    return value.strip()


def _scalar(value: str):
    if not value:
        raise ValueError("Missing YAML scalar value")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid double-quoted YAML string") from exc
        if not isinstance(parsed, str):
            raise ValueError("Expected string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("Unterminated single-quoted YAML string")
        return value[1:-1].replace("''", "'")
    if value.lower() in ("true", "false", "null", "~", "yes", "no", "on", "off"):
        raise ValueError("Boolean and null values are not supported for settings")
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", value):
        return float(value)
    if value[0] in ("[", "{", "-", "|", ">"):
        raise ValueError("Use a flat key: scalar YAML mapping")
    return value


def _validate(name: str, value):
    default = _DEFAULTS[name]
    if isinstance(default, str):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
        if name.startswith("KEYCHAIN_") and any(c in value for c in "\r\n\x00"):
            raise ValueError(f"{name} contains invalid characters")
        if name == "MARKET_TIMEZONE":
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("MARKET_TIMEZONE must be a valid IANA zone") from exc
    elif isinstance(default, int):
        if type(value) is not int:
            raise ValueError(f"{name} must be an integer")
        if value <= 0 and name not in {"REGULAR_SESSION_START_MINUTE", "REGULAR_SESSION_END_MINUTE"}:
            raise ValueError(f"{name} must be positive")
    elif isinstance(default, float):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number")
    if name in {"REGULAR_SESSION_START_MINUTE", "REGULAR_SESSION_END_MINUTE"} and not (0 <= value <= 1440):
        raise ValueError(f"{name} must be between 0 and 1440")
    if name == "HISTORY_PERIOD_TYPE" and value not in {"day", "month", "year", "ytd"}:
        raise ValueError("Invalid HISTORY_PERIOD_TYPE")
    if name == "HISTORY_FREQUENCY_TYPE" and value not in {"minute", "daily", "weekly", "monthly"}:
        raise ValueError("Invalid HISTORY_FREQUENCY_TYPE")
    if name == "DEFAULT_ASSET_TYPE" and value not in {"EQUITY", "OPTION"}:
        raise ValueError("Invalid DEFAULT_ASSET_TYPE")
    if name == "DEFAULT_SESSION" and value not in {"NORMAL", "AM", "PM", "SEAMLESS"}:
        raise ValueError("Invalid DEFAULT_SESSION")
    if name == "DEFAULT_DURATION" and value not in {"DAY", "GOOD_TILL_CANCEL", "FILL_OR_KILL", "IMMEDIATE_OR_CANCEL"}:
        raise ValueError("Invalid DEFAULT_DURATION")
    if name == "DEFAULT_ORDER_TYPE" and value not in {"LIMIT", "MARKET"}:
        raise ValueError("Invalid DEFAULT_ORDER_TYPE")
    if name == "DEFAULT_CONTRACT_TYPE" and value not in {"CALL", "PUT", "ALL"}:
        raise ValueError("Invalid DEFAULT_CONTRACT_TYPE")
    return value


def load_overrides(path: Path | str | None = None) -> dict[str, object]:
    """Parse a flat YAML mapping at ~/.schwab/config.yaml; never read secrets.

    Missing file means all defaults. Unknown keys, malformed input and invalid
    types are errors. Nested YAML and sequences are intentionally unsupported.
    """
    config_path = Path(path) if path is not None else CONFIG_PATH
    try:
        contents = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError(f"Cannot read configuration {config_path}: {exc}") from exc
    overrides: dict[str, object] = {}
    for line_number, line in enumerate(contents.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            raise ValueError(f"{config_path}:{line_number}: nested YAML is not supported")
        name, separator, raw = line.partition(":")
        if not separator:
            raise ValueError(f"{config_path}:{line_number}: expected KEY: value")
        name = name.strip()
        if name not in _DEFAULTS:
            raise ValueError(f"{config_path}:{line_number}: unknown setting {name!r}")
        if name in overrides:
            raise ValueError(f"{config_path}:{line_number}: duplicate setting {name}")
        try:
            overrides[name] = _validate(name, _scalar(_uncomment(raw)))
        except ValueError as exc:
            raise ValueError(f"{config_path}:{line_number}: {exc}") from exc
    combined = {**_DEFAULTS, **overrides}
    if combined["REGULAR_SESSION_START_MINUTE"] >= combined["REGULAR_SESSION_END_MINUTE"]:
        raise ValueError("Regular session start must be before end")
    return overrides


globals().update(load_overrides())
# Keep the pre-existing environment overrides for OAuth/Keychain identifiers.
REDIRECT_URI = os.getenv("SCHWAB_REDIRECT_URI", REDIRECT_URI)
KEYCHAIN_ACCOUNT = os.getenv("SCHWAB_KEYCHAIN_ACCOUNT", KEYCHAIN_ACCOUNT)

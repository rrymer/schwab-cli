"""Validated flat YAML configuration with non-secret application defaults.

Only documented scalar settings from ~/.schwab/config.yaml are supported.
Credentials remain in macOS Keychain, not in YAML.
"""

from pathlib import Path
import math
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

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

# Configuration is read through PyYAML safe_load; only scalar settings are accepted.
# Configuration is loaded once at process import; restart a long-running
# monitor after modifying ~/.schwab/config.yaml.
CONFIG_PATH = Path.home() / ".schwab" / "config.yaml"
_DEFAULTS = {
    name: value for name, value in tuple(globals().items())
    if name.isupper() and not name.startswith("_")
    and isinstance(value, (str, int, float)) and not isinstance(value, bool)
}


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
    return float(value) if isinstance(default, float) else value


def load_overrides(path: Path | str | None = None) -> dict[str, object]:
    """Read optional YAML with PyYAML, validate a flat mapping of settings.

    Missing file preserves defaults. Only known scalar settings are permitted;
    secrets themselves must remain in macOS Keychain.
    """
    config_path = Path(path) if path is not None else CONFIG_PATH
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML configuration {config_path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read configuration {config_path}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path}: expected a mapping of setting names to scalar values")

    overrides: dict[str, object] = {}
    for name, value in data.items():
        if not isinstance(name, str) or name not in _DEFAULTS:
            raise ValueError(f"{config_path}: unknown setting {name!r}")
        try:
            overrides[name] = _validate(name, value)
        except ValueError as exc:
            raise ValueError(f"{config_path}: {exc}") from exc

    combined = {**_DEFAULTS, **overrides}
    if combined["REGULAR_SESSION_START_MINUTE"] >= combined["REGULAR_SESSION_END_MINUTE"]:
        raise ValueError("Regular session start must be before end")
    return overrides


def ensure_config_file(path: Path | str | None = None) -> bool:
    """Install a complete editable defaults file once, without overwriting it.

    Called by the CLI, not on module import, so library use and unit tests
    do not unexpectedly write into the user's home directory. The file holds
    setting names only, never actual Keychain credentials or tokens.
    """
    config_path = Path(path) if path is not None else CONFIG_PATH
    if config_path.exists():
        return False
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = (
        "# Schwab CLI local configuration. Restart the CLI to apply changes.\\n"
        "# Only Keychain lookup identifiers are stored here, never secrets.\\n"
        + yaml.safe_dump(_DEFAULTS, sort_keys=False, default_flow_style=False)
    )
    try:
        # Atomic exclusive creation prevents clobbering a file created by a
        # concurrent CLI process. Restrict readability at creation time.
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
    except BaseException:
        config_path.unlink(missing_ok=True)
        raise
    return True


globals().update(load_overrides())
# Keep the pre-existing environment overrides for OAuth/Keychain identifiers.
REDIRECT_URI = os.getenv("SCHWAB_REDIRECT_URI", REDIRECT_URI)
KEYCHAIN_ACCOUNT = os.getenv("SCHWAB_KEYCHAIN_ACCOUNT", KEYCHAIN_ACCOUNT)

# Schwab CLI

Local command-line interface for the Charles Schwab Trader API.

## Prerequisites

- Python **3.11 or newer**
- macOS with Keychain credentials configured for Schwab OAuth
- An HTTPS local callback configured for `https://127.0.0.1/callback` when using `schwab auth login`

The analytics module uses NumPy and pandas; the HTTP client uses the Python standard library. Installation requires a build backend (Hatchling), which your package installer obtains from `pyproject.toml`. Use `uv sync --locked` to install the pinned dependencies.

## Install for development (recommended)

In PyCharm, select your project virtual environment as the Python interpreter. From a terminal with that environment activated, run:

```bash
python -m pip install --editable .
```

This installs the `schwab` executable into that environment. Edits to `src/schwab_cli/` are immediately reflected without reinstalling. If the executable is not found, confirm your virtual environment is active or use `python -m schwab_cli.cli`.

An equivalent command for a project environment managed by uv is:

```bash
uv sync
uv run schwab --help
```

Alternatively, install the CLI as a standalone user tool (requires uv):

```bash
uv tool install --editable .
schwab --help
```

For a non-editable install, use `python -m pip install .`.

## Verify installation

```bash
schwab --version
schwab --help
schwab auth status
schwab positions list --help
schwab positions monitor --help
```

`auth status` reads local Keychain state. No live Schwab call is needed to check installation.

## Test

Portable, credential-free unit tests using the standard library:

```bash
python -m unittest discover -s tests -t . -v
```

Explicit, read-only live integration tests (requires configured credentials; may refresh access tokens):

```bash
SCHWAB_LIVE_TESTS=1 python -m unittest -v tests.test_live_api
```

To check the actual *installed console-script* in the active Python environment:

```bash
python -m unittest -v tests.test_packaging
schwab --help
```

## Continuous integration (GitHub Actions)

GitHub Actions runs the credential-free unit tests for each pull request and
push to `main`, with Python 3.11 and 3.14 on Linux. You can also launch a run
manually from **Actions → Python tests → Run workflow**.

The workflow also runs a **Ruff lint** job on `src/` and `tests/`. Initially it checks syntax, indentation-related errors, and undefined names (`E4`, `E7`, `E9`, `F821`–`F823`) without requiring a wholesale formatting cleanup. Locally, run:

```bash
uvx ruff check --select E4,E7,E9,F821,F822,F823 src tests
```

The workflow lives in `.github/workflows/tests.yml` and uses `uv sync --locked`
followed by:

```bash
uv run --locked python -m unittest discover -s tests -t . -v
```

Live Schwab tests remain disabled (`SCHWAB_LIVE_TESTS=0`). Do not add brokerage
credentials as repository secrets merely to run routine CI. Review test results
and logs in the PR's **Checks** tab. If the repository has required status
checks configured, GitHub can block merges until those checks pass.

## Notes on credentials and safety

Schwab credentials and tokens are accessed through the local macOS Keychain, not packaging metadata. Never commit OAuth secrets, tokens, or account details to Git. Previewing an order is the default; `positions add --submit` can transmit a **live** order.

The CLI exposes `auth`, `positions`, `monitor`, `analytics`, and `options` commands. Market-data operations use authenticated **read-only GET** requests and cannot submit orders.

## Position dashboard

Use `positions list` for one snapshot, or `positions monitor` to print updates
every 15 seconds (adjustable with `--interval`). Add `--in-place` to explicitly
clear and redraw the same terminal area on each update. The default display omits
the long internal UUID column; use `--include-ids` when you need to reference
a position for monitoring or future position analytics:

```bash
uv run schwab positions list
uv run schwab positions list --include-ids
uv run schwab positions monitor --interval 10 --in-place
uv run schwab positions monitor <position-uuid> --include-ids --once
```

Position rows display **Avg trade** (Schwab's position-level `averagePrice`),
**Price**, and **Underlying** alongside quantity, market value, and daily P/L.
Average trade price is taken from Schwab's aggregate position snapshot rather
than reconstructed from individual lots; missing values display `-`. Equity/ETF price uses Schwab's latest quoted trade,
falling back to mark if unavailable. Option price prefers the option **mark**
(falling back to last trade). For options, the Underlying column uses Schwab's
quoted underlying reference and latest underlying quote, falling back to the
option quote's embedded `underlyingPrice`. Missing quotes display `-`, not a
made-up zero. Quotes are batched per snapshot when possible. The current price
may be delayed, and an option mark is a valuation estimate, not an executable
trade price.

`positions monitor` prints successive snapshots unless `--in-place` is set.
The explicit flag emits ANSI clear-screen sequences on each refresh regardless
of terminal detection, so use it only in a terminal that supports ANSI escapes.
With `--once`, no clear-screen sequences are emitted, even with `--in-place`. Ctrl+C stops monitoring. `positions list --json` includes
`currentPrice` and (on options) `underlyingPrice`, but only includes
`localPositionId` when `--include-ids` is supplied.

## Alerts

A read-only polling alert service is available under `monitor alert`.
Unlike `positions monitor` (the account position dashboard), it evaluates
one registered metric against a threshold and sends output to the explicitly
selected destinations:

```bash
uv run schwab monitor alert --symbol SPY --metric price --above 600 --stdout
uv run schwab monitor alert --symbol '$SPX' --metric price --below 6800 --interval 10 --notify
uv run schwab monitor alert --position-id <active-position-uuid> --metric price --above 5 --stdout --notify
```

Specify exactly one of `--symbol` or `--position-id`, exactly one of
`--above` or `--below`, and at least one output flag: `--stdout`, `--notify`,
or both. `--stdout` prints one line per triggered alert; `--notify` sends the
terminal notification request. With both flags, each triggered alert goes to
both destinations. Without either flag, the command fails immediately. The price metric uses a Schwab quote; for an option
position it prefers the mark. A position alert requires an active local UUID,
not an instrument symbol. The default polling interval is 15 seconds.
The optional `--once` evaluates the alert once without sleeping.

An alert fires when a metric first meets or crosses its threshold and does not
repeat while the condition stays true. It rearms after the value crosses back
outside the condition. If no usable quote is available, no notification is
sent and the previous alert state is preserved. Polling cannot guarantee
detection of price touches between samples.

When `--notify` is supplied, notifications are delivered with an **OSC 9 terminal escape sequence**.
In iTerm2, enable notifications for the terminal profile and allow iTerm2
notifications in macOS System Settings. The terminal owns the notification,
so macOS Focus / Do Not Disturb settings govern the eventual presentation.
Terminal emulators that do not support OSC 9 may ignore the request.
The process needs to stay running in a terminal session to poll.

The initial metric registry contains only `price`. Other analytics metrics can
be added later as evaluators that accept an `AlertTarget` and return a number;
the threshold, polling and notification code requires no changes.
The existing `positions monitor` CLI remains unchanged.

## Optional position technicals

Use `--technicals` with either snapshot or monitor to append **RSI 5m**,
**Vol sess**, and **ADV 10d** columns:

```bash
uv run schwab positions list --technicals
uv run schwab positions monitor --technicals --in-place --interval 30
uv run schwab positions list --technicals --json
```

`RSI 5m` is Wilder RSI(14) over regular-session five-minute close prices.
`Vol sess` sums available five-minute volume for the **latest available**
regular US equity session, which may be partial. `ADV 10d` is the mean
of the **previous ten completed daily trading bars**, excluding that session.
When there are too few completed daily bars, an unavailable underlying, or an
unsuitable history response, metrics display `-` rather than invented values.
Volume columns abbreviate thousands (`k`) and millions (`m`).

For option positions, these are **underlying equity** indicators rather than
option-contract volume or RSI. The underlying comes from Schwab's option
quote reference; the system does not guess it from the OCC symbol. Duplicate
underlyings are fetched once per refresh. This opt-in feature makes additional
market-data requests, including two price-history calls per distinct underlying
per refresh, so use an appropriate monitor interval. Metric values use the
latest available regular session and may be stale outside market hours.
The compact **A** column shows **E** for equities and collective investments,
**C** for standalone calls, **P** for standalone puts, **CV** for bear verticals,
**BV** for bull verticals, and **IC** for iron condors (including iron flies).
Unidentified options display **O** and unrecognized non-option assets **?**.

Option strategy codes are **inferred display labels**, not broker-provided
strategy identities. The classifier requires matching underlying, expiration,
balanced opposing quantities, and qualifying strike arrangements. If the
combination is ambiguous, contracts remain labeled C/P individually. The
original contract rows and position UUIDs are preserved, and no combined
spread P&L is implied. Complex books with several overlapping strategies
will need explicitly persisted strategy membership in TradePlans; this
display-only inference is not a reliable basis for automated order execution.

## Application defaults

Operational defaults live in `src/schwab_cli/conf.py`. Change the values
there instead of separately editing CLI parser defaults and Python function
signatures. Key settings include `POSITION_POLL_SECONDS` and
`ALERT_POLL_SECONDS` (both initially 15 seconds),
`HTTP_TIMEOUT_SECONDS`, OAuth login timeout, market history defaults,
output limits, RSI period, five-minute bar width, 10-session ADV lookback,
regular US equity session boundaries, and default order parameters.
These are Python module constants, **not** a secrets file or runtime-loaded
user configuration. The existing command-line overrides remain available.
Broker protocol enum sets and invariant validation still belong to the
respective API modules.

## Account funds and position P&L

`positions funds` shows six **reported currentBalances** amounts, in USD,
on one compact line. The same line appears above the position rows in both
`positions list` and `positions monitor`:

```bash
uv run schwab positions funds
uv run schwab positions funds --json
uv run schwab positions list
uv run schwab positions monitor --in-place
```

Example: `C:12.5k  T:8.0k  A:7.0k  B:24.0k  O:3.0k  D:50.0k`

Abbreviations: **C** cash balance (`cashBalance`), **T** cash available to
trade (`cashAvailableForTrading`), **A** available funds (`availableFunds`),
**B** buying power (`buyingPower`), **O** option buying power
(`optionBuyingPower`), and **D** day trading buying power
(`dayTradingBuyingPower`). Figures are thousands of USD rounded to one
decimal; `-` means not supplied or unusable, **not zero**. These balances are
not interchangeable and can vary by account type and margin status. JSON
returns full numeric precision and canonical Schwab field names.

For live, single-position unrealized P&L use:

```bash
uv run schwab positions list --include-ids
uv run schwab analytics calculate --position-id YOUR-UUID --metric pnl
```

`pnl` currently means **unrealized average-cost P&L in USD**, not daily P&L,
cumulative realized trade P&L or a multi-leg strategy ledger. It is computed
from Schwab's signed position market value, signed net quantity, aggregate
average purchase/sale price and contract multiplier (default 100 for standard
options, 1 for stocks). Unknown inputs raise an error instead of generating
a made-up number. Closing commissions, fees, and previously realized executions
are **not** included. This metric is available through
`schwab_cli.position_metrics.calculate_position(position_id, "pnl")` for
future TradePlans use and deliberately is not a history metric. Values reflect
the latest fetched Schwab account snapshot, not a continuously executed fill.

## Analytics and market data

After `uv sync`, use `uv run schwab` to keep the executable inside the project's managed environment:

```bash
uv run schwab analytics history AAPL --limit 5
uv run schwab analytics history AAPL --period-type month --period 3 --frequency-type daily
uv run schwab analytics history AAPL --start 2026-01-01 --end 2026-06-01 --json
uv run schwab options expirations SPX
uv run schwab options expirations SPX --include-0dte --json
uv run schwab options chain SPX --type PUT --strike-count 5 --limit 10
uv run schwab options chain SPX --from-date 2026-12-01 --to-date 2026-12-31 --json
```

### Indicator metrics

`analytics history` uses `--metric price` by default. With `price`, it keeps
the familiar OHLCV history view, `--fields` CSV projection and `--json` raw response.

Use `--metric rsi_N`, `atr_N` or `vwap_session` to calculate the latest
indicator value from Schwab historical candles:

```bash
uv run schwab analytics history SPY
uv run schwab analytics history SPY --metric price --fields datetime,close,volume
uv run schwab analytics history SPY --metric rsi_14
uv run schwab analytics history SPY --metric atr_14
uv run schwab analytics history SPY --metric vwap_session
```

Indicator output is JSON with `symbol`, `metrics`, `as_of` (Unix
milliseconds) and `sources` (`daily` or `intraday`). RSI uses Wilder
smoothing over daily closing prices; ATR uses Wilder's smoothed true range;
session VWAP is a volume-weighted *typical price of one-minute bars* for the
latest regular US equity trading session, not trade-level VWAP.

Indicator source windows are managed by the analytics service. The raw price
history flags (`--period-type`, `--frequency-type`, `--start`, `--end`,
`--extended-hours`, `--limit`, `--fields`, `--json`) currently apply
only to `--metric price`; combining them with calculated metrics raises a
validation error instead of silently ignoring requested settings. Indicators
currently return the most recent calculated value, not an indicator time series.

Python callers can use `schwab_cli.analytics_service.calculate_market` with a
comma-separated selection such as `rsi_14,atr_14,vwap_session`. All requested
metrics sharing a source reuse one historical fetch and one pandas DataFrame.

### Select arbitrary response fields

Use `--fields` with comma-separated, case-sensitive property names to extract
only the columns you need from the API data. No special query syntax or optional
dependencies are needed.

```bash
uv run schwab options chain '$SPX' --type PUT --strike-count 5 \
  --fields expirationDate,strikePrice,volatility,delta,openInterest --limit 10

uv run schwab analytics history AAPL --fields datetime,open,high,low,close,volume --limit 5

uv run schwab options expirations '$SPX' --fields expirationDate,daysToExpiration
```

Output is **CSV on standard output**, including a header, so it can be saved or
piped into other tools:

```bash
uv run schwab options chain '$SPX' --type PUT --strike-count 5 \
  --fields strikePrice,volatility,delta > spx-puts.csv
```

For option chains, field names come from each option contract object. Three
additional context fields are available: `side` (`CALL`/`PUT`),
`expirationKey` (the Schwab map key), and `strikeKey`.
Any absent field produces an empty CSV cell. Column order matches `--fields`,
and the existing `--limit` is respected. This is a projection of **returned
data**, not server-side filtering. `--fields` and `--json` are mutually
exclusive; `--json` continues to return the entire raw API response.

The history endpoint contains OHLCV candles rather than historical option
implied volatility. To extract `volatility`, request an option chain.

By default, history displays the last ten returned candles; chain displays the first 20 contracts. `--json` prints the full raw API response for downstream processing. History timestamps are epoch milliseconds; date arguments are interpreted as UTC calendar dates. Zero-DTE expirations are excluded from the concise expiration list unless `--include-0dte` is specified.

Python library entry points live in `schwab_cli.market_data`: `get_price_history`, `get_option_expirations`, `nearest_expiration`, `get_option_chain`, and `iter_option_contracts`. These are read-only and have no pandas dependency.

Requests, query parameters, frequency constraints, and response fields were checked against the saved Schwab **Market Data Production** documentation. Live API behavior is not tested by the mock unit suite. The additional tests can be run using:

```bash
uv run python -m unittest -v tests.test_market_data
uv run python -m unittest discover -s tests -t . -v
```

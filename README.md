# Schwab CLI

Local command-line interface for the Charles Schwab Trader API.

## Prerequisites

- Python **3.11 or newer**
- macOS with Keychain credentials configured for Schwab OAuth
- An HTTPS local callback configured for `https://127.0.0.1/callback` when using `schwab auth login`

The package uses the Python standard library at runtime. Installation requires a build backend (Hatchling), which your package installer obtains from `pyproject.toml`. You do **not** need to install `requests`, `pytest`, or `setup.py`.

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

## Notes on credentials and safety

Schwab credentials and tokens are accessed through the local macOS Keychain, not packaging metadata. Never commit OAuth secrets, tokens, or account details to Git. Previewing an order is the default; `positions add --submit` can transmit a **live** order.

The CLI exposes `auth`, `positions`, `market`, and `options` commands. Market-data operations use authenticated **read-only GET** requests and cannot submit orders.

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

## Market data

After `uv sync`, use `uv run schwab` to keep the executable inside the project's managed environment:

```bash
uv run schwab market history AAPL --limit 5
uv run schwab market history AAPL --period-type month --period 3 --frequency-type daily
uv run schwab market history AAPL --start 2026-01-01 --end 2026-06-01 --json
uv run schwab options expirations SPX
uv run schwab options expirations SPX --include-0dte --json
uv run schwab options chain SPX --type PUT --strike-count 5 --limit 10
uv run schwab options chain SPX --from-date 2026-12-01 --to-date 2026-12-31 --json
```

### Select arbitrary response fields

Use `--fields` with comma-separated, case-sensitive property names to extract
only the columns you need from the API data. No special query syntax or optional
dependencies are needed.

```bash
uv run schwab options chain '$SPX' --type PUT --strike-count 5 \
  --fields expirationDate,strikePrice,volatility,delta,openInterest --limit 10

uv run schwab market history AAPL --fields datetime,open,high,low,close,volume --limit 5

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

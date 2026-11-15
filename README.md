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

By default, history displays the last ten returned candles; chain displays the first 20 contracts. `--json` prints the full raw API response for downstream processing. History timestamps are epoch milliseconds; date arguments are interpreted as UTC calendar dates. Zero-DTE expirations are excluded from the concise expiration list unless `--include-0dte` is specified.

Python library entry points live in `schwab_cli.market_data`: `get_price_history`, `get_option_expirations`, `nearest_expiration`, `get_option_chain`, and `iter_option_contracts`. These are read-only and have no pandas dependency.

Requests, query parameters, frequency constraints, and response fields were checked against the saved Schwab **Market Data Production** documentation. Live API behavior is not tested by the mock unit suite. The additional tests can be run using:

```bash
uv run python -m unittest -v tests.test_market_data
uv run python -m unittest discover -s tests -t . -v
```

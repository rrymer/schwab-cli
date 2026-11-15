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

The CLI currently exposes `auth` and `positions` commands. Additional market-data commands are being developed separately on `feature/market-data`.

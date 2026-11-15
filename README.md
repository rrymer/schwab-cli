# Schwab local CLI

A small Python CLI harness for Schwab API access. Authentication and API calls are intentionally not implemented yet.

## Install

From this directory:

```bash
uv tool install --editable .
```

If `schwab` is not on your PATH, run `uv tool update-shell` and open a new shell.

For development within an existing virtual environment:

```bash
uv pip install -e .
```

## Try it

```bash
schwab --help
schwab auth --help
schwab auth login
schwab auth status
schwab positions list
```

The last three commands currently exit with status 1 and a clear not-implemented message.

## Planned modules

- `auth.py`: Keychain credentials, OAuth authorization, refresh and token persistence
- `api.py`: authenticated Schwab HTTP client
- `commands/`: account and market-data commands as they grow

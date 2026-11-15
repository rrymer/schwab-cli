"""Synthetic Schwab fixtures. Never store real account data in test fixtures."""
import io
import json
from unittest.mock import MagicMock


def position(symbol='AAPL', asset='EQUITY', long=1, short=0):
    return {
        'instrument': {'symbol': symbol, 'assetType': asset},
        'longQuantity': long, 'shortQuantity': short,
        'marketValue': 123.45, 'currentDayProfitLoss': 1.25,
    }


def snapshot(*positions):
    return [{'securitiesAccount': {'accountNumber': 'SYNTHETIC1234', 'positions': list(positions)}}]


def response(payload=None, headers=None):
    """Context-managed fake urllib response with JSON body."""
    r = MagicMock()
    r.__enter__.return_value = r
    r.read.return_value = json.dumps(payload if payload is not None else {}).encode()
    r.headers = headers or {}
    return r


def decode_request(req):
    return json.loads(req.data.decode('utf-8'))

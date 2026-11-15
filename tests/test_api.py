import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs
from schwab_cli import api
from tests.helpers import response


class ApiTests(unittest.TestCase):
    def test_get_authentication_and_json(self):
        with patch.object(api, 'get_access_token', return_value='synthetic'), \
             patch.object(api, 'urlopen', return_value=response({'ok': True})) as network:
            self.assertEqual(api._get('/sample', params={'fields': 'positions'}), {'ok': True})
        req = network.call_args.args[0]
        self.assertEqual(req.get_method(), 'GET')
        self.assertEqual(req.get_header('Authorization'), 'Bearer synthetic')
        self.assertEqual(urlsplit(req.full_url).path, '/trader/v1/sample')
        self.assertEqual(parse_qs(urlsplit(req.full_url).query), {'fields': ['positions']})

    def test_account_numbers_returns_unique_hash(self):
        with patch.object(api, '_get', return_value=[{'accountNumber': 'synthetic', 'hashValue': 'HASH123'}]):
            self.assertEqual(api.account_numbers(), 'HASH123')

    def test_account_numbers_rejects_non_unique_or_invalid_response(self):
        for payload in ([], [{'hashValue': 'a'}, {'hashValue': 'b'}], {}, [{'accountNumber': 'x'}]):
            with self.subTest(payload=payload), patch.object(api, '_get', return_value=payload):
                with self.assertRaises(api.SchwabAPIError):
                    api.account_numbers()

    def test_positions_resolves_hash_at_runtime(self):
        account = {'securitiesAccount': {'positions': []}}
        with patch.object(api, 'account_numbers', return_value='FAKE/HASH'), \
             patch.object(api, '_get', return_value=account) as req:
            self.assertEqual(api.get_positions(), [account])
        self.assertEqual(req.call_args.args[0], '/accounts/FAKE%2FHASH')
        self.assertEqual(req.call_args.kwargs['params'], {'fields': 'positions'})

    def test_api_http_error_redacts_body(self):
        error = HTTPError('url', 403, 'Forbidden', {}, io.BytesIO(b'SENSITIVE_ACCOUNT_TOKEN'))
        with patch.object(api, 'get_access_token', return_value='fake'), \
             patch.object(api, 'urlopen', side_effect=error):
            with self.assertRaises(api.SchwabAPIError) as ctx:
                api._get('/sample')
        self.assertNotIn('SENSITIVE_ACCOUNT_TOKEN', str(ctx.exception))
        self.assertIn('403', str(ctx.exception))

    def test_api_network_failure(self):
        with patch.object(api, 'get_access_token', return_value='fake'), \
             patch.object(api, 'urlopen', side_effect=URLError('offline')):
            with self.assertRaises(api.SchwabAPIError):
                api._get('/sample')

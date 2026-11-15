"""Opt-in read-only integration tests. No order submission, ever.

Run explicitly on a machine with Schwab credentials:
    SCHWAB_LIVE_TESTS=1 PYTHONPATH=src python -m unittest -v tests.test_live_api

Do not run this file in ordinary unit test discovery: class is skipped unless enabled.
"""
import os
import unittest
from schwab_cli import api, auth


@unittest.skipUnless(os.environ.get('SCHWAB_LIVE_TESTS') == '1',
                     'Live Schwab tests disabled: set SCHWAB_LIVE_TESTS=1')
class LiveReadOnlyTests(unittest.TestCase):
    def test_authenticated_account_hash_and_positions(self):
        # Uses real macOS Keychain and may refresh credentials.
        token = auth.get_access_token()
        self.assertIsInstance(token, str)
        self.assertTrue(token)
        hash_value = api.account_numbers()
        self.assertIsInstance(hash_value, str)
        self.assertTrue(hash_value)
        accounts = api.get_positions()
        self.assertEqual(len(accounts), 1)
        self.assertIn('securitiesAccount', accounts[0])
        self.assertIsInstance(accounts[0]['securitiesAccount'].get('positions', []), list)
        # Never print account identifiers or positions in test output.

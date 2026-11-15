import time
import unittest
from unittest.mock import patch
from schwab_cli import auth


class AuthTests(unittest.TestCase):
    def test_cached_token_is_used_without_refresh(self):
        secrets = {'access_token': 'cached', 'access_expires_at': str(int(time.time()) + 500)}
        with patch.object(auth, 'get_secret', side_effect=secrets.get), \
             patch.object(auth, 'refresh_access_token') as refresh:
            self.assertEqual(auth.get_access_token(), 'cached')
            refresh.assert_not_called()

    def test_expired_token_triggers_refresh(self):
        secrets = {'access_token': 'old', 'access_expires_at': '1'}
        with patch.object(auth, 'get_secret', side_effect=secrets.get), \
             patch.object(auth, 'refresh_access_token', return_value='new') as refresh:
            self.assertEqual(auth.get_access_token(), 'new')
            refresh.assert_called_once_with()

    def test_near_expiry_triggers_refresh(self):
        secrets = {'access_token': 'old', 'access_expires_at': str(int(time.time()) + 30)}
        with patch.object(auth, 'get_secret', side_effect=secrets.get), \
             patch.object(auth, 'refresh_access_token', return_value='new'):
            self.assertEqual(auth.get_access_token(), 'new')

    def test_invalid_expiry_triggers_refresh(self):
        secrets = {'access_token': 'old', 'access_expires_at': 'garbage'}
        with patch.object(auth, 'get_secret', side_effect=secrets.get), \
             patch.object(auth, 'refresh_access_token', return_value='new'):
            self.assertEqual(auth.get_access_token(), 'new')

    def test_missing_refresh_token_fails_without_browser(self):
        with patch.object(auth, 'get_secret', return_value=None), \
             patch.object(auth, 'login') as login:
            with self.assertRaises(auth.AuthError):
                auth.get_access_token()
            login.assert_not_called()

    def test_refresh_rotates_and_persists_credentials(self):
        saved = {}
        with patch.object(auth, 'get_secret', return_value='old-refresh'), \
             patch.object(auth, '_token_request', return_value={
                 'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 1800,
             }) as token_request, \
             patch.object(auth, 'set_secret', side_effect=saved.__setitem__):
            self.assertEqual(auth.refresh_access_token(), 'new-access')
        token_request.assert_called_once_with({'grant_type': 'refresh_token', 'refresh_token': 'old-refresh'})
        self.assertEqual(saved['access_token'], 'new-access')
        self.assertEqual(saved['refresh_token'], 'new-refresh')
        self.assertGreater(int(saved['access_expires_at']), int(time.time()) + 1700)

    def test_refresh_preserves_existing_refresh_token_when_not_rotated(self):
        saved = {}
        with patch.object(auth, 'get_secret', return_value='old-refresh'), \
             patch.object(auth, '_token_request', return_value={'access_token': 'fresh'}), \
             patch.object(auth, 'set_secret', side_effect=saved.__setitem__):
            auth.refresh_access_token()
        self.assertEqual(saved['refresh_token'], 'old-refresh')

    def test_token_failure_never_uses_browser_by_default(self):
        with patch.object(auth, 'get_secret', return_value=None), \
             patch.object(auth, 'refresh_access_token', side_effect=auth.AuthError('invalid grant')), \
             patch.object(auth, 'login') as login:
            with self.assertRaises(auth.AuthError):
                auth.get_access_token()
            login.assert_not_called()

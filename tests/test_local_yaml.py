"""Credential-free tests for the local scalar YAML configuration interface."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from schwab_cli import auth, conf


class LocalYamlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.yaml"

    def write(self, contents):
        self.path.write_text(contents, encoding="utf-8")
        return self.path

    def test_missing_file_preserves_defaults(self):
        self.assertEqual(conf.load_overrides(self.path), {})

    def test_scalar_overrides_and_comments(self):
        self.write('''# a local configuration
POSITION_POLL_SECONDS: 30 # seconds
ALERT_POLL_SECONDS: 2.5
HTTP_TIMEOUT_SECONDS: 45
KEYCHAIN_ACCOUNT: "my account #1"
KEYCHAIN_SERVICE_CLIENT_ID: 'client-id'
DASHBOARD_RSI_PERIOD: 7
''')
        result = conf.load_overrides(self.path)
        self.assertEqual(result["POSITION_POLL_SECONDS"], 30.0)
        self.assertEqual(result["ALERT_POLL_SECONDS"], 2.5)
        self.assertEqual(result["HTTP_TIMEOUT_SECONDS"], 45)
        self.assertEqual(result["KEYCHAIN_ACCOUNT"], "my account #1")
        self.assertEqual(result["KEYCHAIN_SERVICE_CLIENT_ID"], "client-id")
        self.assertEqual(result["DASHBOARD_RSI_PERIOD"], 7)

    def test_rejects_malformed_unknown_and_sensitive_values(self):
        inputs = [
            "access_token: never-put-secrets-here\n",
            "client_secret: never-put-secrets-here\n",
            "POSITION_POLL_SECONDS: -1\n",
            "POSITION_POLL_SECONDS: true\n",
            "POSITION_POLL_SECONDS: 0\n",
            "POSITION_POLL_SECONDS: .nan\n",
            "POSITION_POLL_SECONDS: [30]\n",
            "POSITION_POLL_SECONDS:\n  - 30\n",
            "KEYCHAIN_ACCOUNT: \"unterminated\n",
            "DASHBOARD_RSI_PERIOD: 14.5\n",
            "REGULAR_SESSION_START_MINUTE: 1000\nREGULAR_SESSION_END_MINUTE: 900\n",
        ]
        for body in inputs:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    conf.load_overrides(self.write(body))

    def test_standard_yaml_features_and_non_mapping_inputs(self):
        self.write("""# PyYAML handles quoted values and aliases
KEYCHAIN_ACCOUNT: &account 'custom:keychain'
KEYCHAIN_SERVICE_CLIENT_ID: *account
POSITION_POLL_SECONDS: 3.5
""")
        overrides = conf.load_overrides(self.path)
        self.assertEqual(overrides["KEYCHAIN_ACCOUNT"], "custom:keychain")
        self.assertEqual(overrides["KEYCHAIN_SERVICE_CLIENT_ID"], "custom:keychain")
        self.assertEqual(overrides["POSITION_POLL_SECONDS"], 3.5)
        for invalid in ("- one\\n- two\\n", "42\\n", "KEYCHAIN_ACCOUNT: [a, b]\\n"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                conf.load_overrides(self.write(invalid))

    def test_auth_service_mapping_retains_existing_key_names(self):
        self.assertEqual(auth._KEYCHAIN_SERVICES["client_id"], conf.KEYCHAIN_SERVICE_CLIENT_ID)
        self.assertEqual(auth._KEYCHAIN_SERVICES["refresh_token"], conf.KEYCHAIN_SERVICE_REFRESH_TOKEN)
        self.assertEqual(conf.KEYCHAIN_ACCOUNT, "schwabbot")
        with patch.object(auth, "_keychain", return_value="pretend") as keychain:
            self.assertEqual(auth.get_secret("client_id"), "pretend")
        keychain.assert_called_once_with(conf.KEYCHAIN_SERVICE_CLIENT_ID)

    def test_fresh_python_process_reads_default_home_yaml(self):
        home = Path(self.tmp.name)
        directory = home / ".schwab"
        directory.mkdir()
        (directory / "config.yaml").write_text(
            "POSITION_POLL_SECONDS: 27\nKEYCHAIN_ACCOUNT: custom-keychain\n",
            encoding="utf-8"
        )
        code = (
            "from schwab_cli import conf; from schwab_cli.cli import build_parser; "
            "p=build_parser().parse_args(['positions','monitor']); "
            "print(p.interval, conf.KEYCHAIN_ACCOUNT)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "HOME": str(home)},
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "27.0 custom-keychain")


if __name__ == "__main__":
    unittest.main()

"""Outbound email transport tests; no network or live credentials."""
import unittest
from unittest.mock import Mock, patch
import smtplib

from schwab_cli import conf
from schwab_cli.integrations import (
    EmailDeliveryError, Gmail, Message, Provider, gmail_provider, send_email,
)


class EmailTests(unittest.TestCase):
    def test_provider_is_abstract_and_gmail_is_concrete(self):
        with self.assertRaises(TypeError):
            Provider()
        self.assertIsInstance(Gmail("sender@gmail.com", "recipient@example.com", "secret"), Provider)

        class IncompleteProvider(Provider):
            pass

        with self.assertRaises(TypeError):
            IncompleteProvider()

    def test_prefix_and_provider_injection(self):
        provider = Mock(spec=Provider)
        send_email("Test", "body", provider)
        provider.send.assert_called_once_with(Message(f"{conf.EMAIL_SUBJECT_PREFIX} Test", "body"))
        provider.reset_mock()
        send_email(f"{conf.EMAIL_SUBJECT_PREFIX} Test", "body", provider)
        provider.send.assert_called_once_with(Message(f"{conf.EMAIL_SUBJECT_PREFIX} Test", "body"))

    def test_gmail_sends_over_tls(self):
        provider = Gmail("sender@gmail.com", "recipient@example.com", "secret")
        with patch("schwab_cli.integrations.smtplib.SMTP_SSL") as factory:
            smtp = factory.return_value.__enter__.return_value
            smtp.send_message.return_value = {}
            provider.send(Message("Test", "Hello"))
        self.assertEqual(factory.call_args.args, (conf.GMAIL_SMTP_HOST, conf.GMAIL_SMTP_PORT))
        self.assertTrue(factory.call_args.kwargs["context"].check_hostname)
        smtp.login.assert_called_once_with("sender@gmail.com", "secret")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["From"], "sender@gmail.com")
        self.assertEqual(message["To"], "recipient@example.com")
        self.assertEqual(message["Subject"], "Test")
        self.assertEqual(message.get_content().strip(), "Hello")
        self.assertNotIn("secret", repr(provider))

    def test_no_retry_on_ambiguous_failure(self):
        provider = Gmail("sender@gmail.com", "recipient@example.com", "secret")
        with patch("schwab_cli.integrations.smtplib.SMTP_SSL") as factory:
            smtp = factory.return_value.__enter__.return_value
            smtp.send_message.side_effect = smtplib.SMTPServerDisconnected("secret response")
            with self.assertRaises(EmailDeliveryError) as error:
                provider.send(Message("Test", "Hello"))
            smtp.send_message.assert_called_once()
        self.assertNotIn("secret", str(error.exception))

    def test_environment_password_and_default_recipient(self):
        with (patch.object(conf, "GMAIL_SENDER", "sender@gmail.com"),
              patch.object(conf, "EMAIL_RECIPIENT", ""),
              patch.dict("os.environ", {conf.GMAIL_PASSWORD_ENV: "test-password"})):
            provider = gmail_provider()
        self.assertEqual(provider.sender, provider.recipient)
        self.assertEqual(provider.app_password, "test-password")

    def test_missing_password_and_invalid_headers(self):
        with (patch.object(conf, "GMAIL_SENDER", "sender@gmail.com"),
              patch.object(conf, "EMAIL_RECIPIENT", ""),
              patch.dict("os.environ", {}, clear=True),
              patch("schwab_cli.integrations.sys.platform", "win32")):
            with self.assertRaises(EmailDeliveryError):
                gmail_provider()
        with self.assertRaises(EmailDeliveryError):
            send_email("bad\nSubject", "body", Mock())

    def test_keychain_lookup(self):
        with (patch.object(conf, "GMAIL_SENDER", "sender@gmail.com"),
              patch.object(conf, "EMAIL_RECIPIENT", ""),
              patch.dict("os.environ", {}, clear=True),
              patch("schwab_cli.integrations.sys.platform", "darwin"),
              patch("schwab_cli.auth._keychain", return_value="test-password") as lookup):
            provider = gmail_provider()
        lookup.assert_called_once_with(conf.KEYCHAIN_SERVICE_GMAIL_APP_PASSWORD)
        self.assertEqual(provider.app_password, "test-password")

"""Outbound email transport; no trading or notification policy lives here."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import formatdate, make_msgid
import os
import smtplib
import ssl
import sys

from . import conf


class EmailDeliveryError(RuntimeError):
    """An email could not be submitted, or its submission outcome is unknown."""


@dataclass(frozen=True)
class Message:
    subject: str
    body: str


class Provider(ABC):
    """Implementations submit one plain-text message or raise EmailDeliveryError."""

    @abstractmethod
    def send(self, message: Message) -> None:
        """Submit a message through the concrete transport."""


def _address(value: str) -> str:
    try:
        address = Address(addr_spec=value)
        if not address.username or not address.domain:
            raise ValueError
        return address.addr_spec
    except (ValueError, TypeError) as exc:
        raise EmailDeliveryError("Configure a valid sender and recipient email address") from exc


@dataclass(frozen=True)
class Gmail(Provider):
    """Gmail SMTP over verified TLS, authenticated by an app password."""

    sender: str
    recipient: str
    app_password: str = field(repr=False)
    timeout: float = conf.EMAIL_TIMEOUT_SECONDS

    def send(self, message: Message) -> None:
        sender = _address(self.sender)
        recipient = _address(self.recipient)
        if not self.app_password.strip():
            raise EmailDeliveryError("Missing Gmail app password")
        email = EmailMessage()
        email["From"] = sender
        email["To"] = recipient
        email["Subject"] = message.subject
        email["Date"] = formatdate(usegmt=True)
        email["Message-ID"] = make_msgid()
        email.set_content(message.body)
        try:
            with smtplib.SMTP_SSL(
                conf.GMAIL_SMTP_HOST, conf.GMAIL_SMTP_PORT,
                timeout=self.timeout, context=ssl.create_default_context(),
            ) as smtp:
                smtp.login(sender, self.app_password)
                refused = smtp.send_message(email)
                if refused:
                    raise EmailDeliveryError("Gmail refused the recipient")
        except (OSError, smtplib.SMTPException) as exc:
            # Never include SMTP responses, passwords or message content in errors.
            raise EmailDeliveryError(
                f"Gmail submission failed or outcome unknown ({type(exc).__name__})"
            ) from None


def gmail_provider() -> Gmail:
    """Resolve credentials without reading passwords from YAML.

    Environment injection works cross-platform; macOS falls back to the
    existing local Keychain naming convention.
    """
    sender = _address(conf.GMAIL_SENDER)
    recipient = _address(conf.EMAIL_RECIPIENT or sender)
    password = os.environ.get(conf.GMAIL_PASSWORD_ENV)
    if not password and sys.platform == "darwin":
        from .auth import _keychain

        password = _keychain(conf.KEYCHAIN_SERVICE_GMAIL_APP_PASSWORD)
    if not password:
        raise EmailDeliveryError(
            f"Set {conf.GMAIL_PASSWORD_ENV} or configure the Gmail app password in macOS Keychain"
        )
    return Gmail(sender, recipient, password)


def send_email(subject: str, body: str, provider: Provider | None = None) -> None:
    """Submit a plain-text event email. The caller owns retry and audit policy."""
    if not isinstance(subject, str) or not subject.strip() or "\r" in subject or "\n" in subject:
        raise EmailDeliveryError("Email subject must be a nonempty single line")
    if not isinstance(body, str):
        raise EmailDeliveryError("Email body must be text")
    prefix = conf.EMAIL_SUBJECT_PREFIX
    if not subject.startswith(prefix):
        subject = f"{prefix} {subject}"
    (provider if provider is not None else gmail_provider()).send(Message(subject, body))

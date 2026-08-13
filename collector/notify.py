"""ntfy push notifications and SMTP email (brief §7).

A failed send is logged and raises: a notification lost in silence would
defeat the entire point of alerting (README.md, "le rapport est sa propre
surveillance"). The caller (main.py) turns that into a non-zero exit.
"""

from __future__ import annotations

import logging
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Iterable, Tuple

from collector.config import NtfyConfig, SmtpConfig

logger = logging.getLogger(__name__)

Attachment = Tuple[str, bytes, str]  # (filename, content, "maintype/subtype")


class NotificationError(Exception):
    """A notification could not be sent."""


def send_ntfy(config: NtfyConfig, title: str, message: str, priority: str = "default") -> None:
    url = f"{config.server.rstrip('/')}/{config.topic}"
    request = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
    request.add_header("Title", title)
    request.add_header("Priority", priority)
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except (urllib.error.URLError, OSError) as exc:
        logger.error("ntfy notification failed: %s", exc)
        raise NotificationError(f"ntfy: {exc}") from exc


def send_email(
    config: SmtpConfig,
    subject: str,
    body: str,
    attachments: Iterable[Attachment] = (),
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.user
    message["To"] = config.to
    message.set_content(body)

    for filename, content, mime in attachments:
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    try:
        with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.user, config.password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("email notification failed: %s", exc)
        raise NotificationError(f"smtp: {exc}") from exc

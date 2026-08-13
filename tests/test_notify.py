import os
import smtplib
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from collector.config import SMTP_PASSWORD_ENV, NtfyConfig, SmtpConfig
from collector.notify import NotificationError, send_email, send_ntfy


class NtfyTest(unittest.TestCase):
    def test_posts_title_priority_and_body(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                received["body"] = self.rfile.read(length).decode("utf-8")
                received["title"] = self.headers["Title"]
                received["priority"] = self.headers["Priority"]
                received["path"] = self.path
                self.send_response(200)
                self.end_headers()

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            config = NtfyConfig(server=f"http://127.0.0.1:{httpd.server_address[1]}", topic="clock-alerts")
            send_ntfy(config, title="Capteur muet", message="n_out silencieux depuis 7h", priority="high")
        finally:
            httpd.shutdown()
            httpd.server_close()

        self.assertEqual(received["path"], "/clock-alerts")
        self.assertEqual(received["title"], "Capteur muet")
        self.assertEqual(received["priority"], "high")
        self.assertEqual(received["body"], "n_out silencieux depuis 7h")

    def test_unreachable_server_raises_notification_error(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        config = NtfyConfig(server=f"http://127.0.0.1:{port}", topic="x")
        with self.assertRaises(NotificationError):
            send_ntfy(config, title="t", message="m")


class EmailTest(unittest.TestCase):
    """smtplib.SMTP is mocked: exercising real STARTTLS would need a TLS-capable
    test server, well beyond what this module needs to prove — that the right
    calls happen in the right order, and that a failure becomes
    NotificationError rather than propagating raw or vanishing.
    """

    def setUp(self):
        self.config = SmtpConfig(
            host="YOUR_SMTP_HOST", port=587, user="collector@example.org", to="me@example.org"
        )
        os.environ[SMTP_PASSWORD_ENV] = "s3cret"

    def tearDown(self):
        os.environ.pop(SMTP_PASSWORD_ENV, None)

    def test_sends_starttls_login_and_message_with_attachment(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            send_email(
                self.config,
                subject="Rapport mensuel",
                body="Voir pieces jointes.",
                attachments=[("archive.zip", b"PK\x03\x04", "application/zip")],
            )

        smtp_cls.assert_called_once_with("YOUR_SMTP_HOST", 587, timeout=30)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("collector@example.org", "s3cret")
        self.assertTrue(smtp.send_message.called)

        sent_message = smtp.send_message.call_args[0][0]
        self.assertEqual(sent_message["Subject"], "Rapport mensuel")
        self.assertEqual(sent_message["To"], "me@example.org")
        attachments = list(sent_message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "archive.zip")

    def test_smtp_failure_raises_notification_error(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
            with self.assertRaises(NotificationError):
                send_email(self.config, subject="s", body="b")


if __name__ == "__main__":
    unittest.main()

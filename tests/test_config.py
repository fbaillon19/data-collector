import os
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from collector import config


SAMPLE_INI = dedent(
    """\
    [device:clock]
    url = http://192.168.x.x
    timeout_s = 10

    [archive]
    dir = /var/lib/data-collector
    max_attachment_mb = 20

    [ntfy]
    server = https://ntfy.sh
    topic = test-topic

    [smtp]
    host = smtp.example.org
    port = 587
    user = collector@example.org
    to = me@example.org

    [alerts]
    unreachable_polls = 3
    mute_sensor_hours = 6
    """
)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.ini"
        self.path.write_text(SAMPLE_INI, encoding="ascii")
        os.environ.pop(config.SMTP_PASSWORD_ENV, None)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop(config.SMTP_PASSWORD_ENV, None)

    def test_missing_file_raises(self):
        with self.assertRaises(config.ConfigError):
            config.load(Path(self.tmp.name) / "does-not-exist.ini")

    def test_loads_devices_and_sections(self):
        cfg = config.load(self.path)
        self.assertEqual(len(cfg.devices), 1)
        self.assertEqual(cfg.devices[0].name, "clock")
        self.assertEqual(cfg.devices[0].timeout_s, 10.0)
        self.assertEqual(cfg.archive.max_attachment_mb, 20)
        self.assertEqual(cfg.alerts.unreachable_polls, 3)

    def test_smtp_password_not_read_from_file(self):
        cfg = config.load(self.path)
        with self.assertRaises(config.ConfigError):
            _ = cfg.smtp.password

    def test_smtp_password_comes_from_environment(self):
        os.environ[config.SMTP_PASSWORD_ENV] = "s3cret"
        cfg = config.load(self.path)
        self.assertEqual(cfg.smtp.password, "s3cret")

    def test_missing_section_raises(self):
        broken = self.path.with_name("broken.ini")
        broken.write_text("[device:clock]\nurl = http://x\ntimeout_s = 1\n", encoding="ascii")
        with self.assertRaises(config.ConfigError):
            config.load(broken)


if __name__ == "__main__":
    unittest.main()

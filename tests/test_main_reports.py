import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import archive, config, main, report
from simulator.server import serve_in_thread
from tests.test_main import NtfyCapture, make_config


def _row(ts, **overrides):
    row = {col: "" for col in archive.BASE_COLUMNS}
    row.update(ts_utc=ts, n_in="30", n_out="30", n_co2="12", n_pm="6", partial="0", overwrote="0", t_out="18.0")
    row.update(overrides)
    return row


class WeeklyReportCommandTest(unittest.TestCase):
    def test_sends_one_ntfy_report_per_device(self):
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            archive_root = Path(tmp.name)
            archive.write_batch(archive_root, "clock", archive.BASE_COLUMNS, [_row("2026-01-01T00:00:00Z")])
            cfg = make_config("http://unused.invalid", archive_root, ntfy.url)

            ok = main.weekly_report(cfg)

            self.assertTrue(ok)
            self.assertEqual(len(ntfy.alerts), 1)
            self.assertIn("rapport hebdomadaire", ntfy.alerts[0]["title"])
            self.assertIn("Heures collectees", ntfy.alerts[0]["body"])
        finally:
            ntfy.close()
            tmp.cleanup()


class MonthlyReportCommandTest(unittest.TestCase):
    def test_sends_email_with_two_attachments(self):
        tmp = tempfile.TemporaryDirectory()
        archive_root = Path(tmp.name)
        rows = [_row(f"2026-07-{1 + h // 24:02d}T{h % 24:02d}:00:00Z") for h in range(48)]
        archive.write_batch(archive_root, "clock", archive.BASE_COLUMNS, rows)

        # No device actually listening: the live overwritten count is
        # simply unavailable, and the report must say so, not crash.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        cfg = make_config(f"http://127.0.0.1:{port}", archive_root, "http://unused.invalid")
        window = report.MonthWindow(2026, 7)

        try:
            with mock.patch("smtplib.SMTP") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                os.environ[config.SMTP_PASSWORD_ENV] = "s3cret"
                ok = main.monthly_report(cfg, window=window)

            self.assertTrue(ok)
            sent = smtp.send_message.call_args[0][0]
            self.assertIn("2026-07", sent["Subject"])
            attachments = list(sent.iter_attachments())
            self.assertEqual(len(attachments), 2)
            self.assertTrue(attachments[0].get_filename().endswith(".csv"))
            self.assertEqual(attachments[1].get_filename(), "archive.zip")
        finally:
            os.environ.pop(config.SMTP_PASSWORD_ENV, None)
            tmp.cleanup()

    def test_added_criterion_3_overwritten_bilan_present_with_simulator_off(self):
        tmp = tempfile.TemporaryDirectory()
        archive_root = Path(tmp.name)
        archive.write_batch(archive_root, "clock", archive.BASE_COLUMNS, [_row("2026-07-01T00:00:00Z")])
        archive.log_event(archive_root, "clock", "overwrite_detected", detail="17", ts="2026-07-03T14:00:00Z")

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # simulateur eteint : rien n'ecoute sur ce port

        cfg = make_config(f"http://127.0.0.1:{port}", archive_root, "http://unused.invalid")
        window = report.MonthWindow(2026, 7)

        try:
            with mock.patch("smtplib.SMTP") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                os.environ[config.SMTP_PASSWORD_ENV] = "s3cret"
                ok = main.monthly_report(cfg, window=window)

            self.assertTrue(ok)
            sent = smtp.send_message.call_args[0][0]
            body = sent.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("17 enregistrement(s) perdu(s) sur 1 evenement(s)", body)
        finally:
            os.environ.pop(config.SMTP_PASSWORD_ENV, None)
            tmp.cleanup()

    def test_brief_0002_criterion_4_bilan_from_a_real_overwrote_column_simulator_off(self):
        """The events came from an actual `overwrote=1` row collected by a
        real poll — not a hand-written log_event call — and the monthly
        report is then generated with the device switched off entirely.
        """
        tmp = tempfile.TemporaryDirectory()
        archive_root = Path(tmp.name)
        httpd, _ = serve_in_thread("overwritten")
        try:
            cfg = make_config(f"http://127.0.0.1:{httpd.server_address[1]}", archive_root, "http://unused.invalid")
            main.poll(cfg)  # archives the flagged row: write_batch logs it from the column
            main.poll(cfg)  # counter-sourced second loss, no corroborating row
        finally:
            httpd.shutdown()
            httpd.server_close()

        events = archive.read_events(archive_root, "clock")
        self.assertEqual([e["event"] for e in events], ["overwrite_detected", "overwrite_suspected"])

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # simulateur eteint : rien n'ecoute sur ce port

        cfg = make_config(f"http://127.0.0.1:{port}", archive_root, "http://unused.invalid")
        window = report.MonthWindow(2026, 1)  # the fixture's rows all fall in January 2026

        try:
            with mock.patch("smtplib.SMTP") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                os.environ[config.SMTP_PASSWORD_ENV] = "s3cret"
                ok = main.monthly_report(cfg, window=window)

            self.assertTrue(ok)
            sent = smtp.send_message.call_args[0][0]
            body = sent.get_body(preferencelist=("plain",)).get_content()
            # The column-sourced event is stamped with the archived row's own
            # ts_utc (January 2026, inside the window): it counts toward the
            # month's dated total. The counter-sourced one has no such anchor
            # and is stamped "now" (today, outside the window) — it falls
            # outside this report entirely, rather than being misfiled here.
            self.assertIn("1 enregistrement(s) perdu(s) sur 1 evenement(s) dates ce mois-ci", body)
            self.assertIn("Pertes suspectees, date de survenue inconnue : aucune", body)
            self.assertIn("non disponible", body)  # the live counter, honestly absent
        finally:
            os.environ.pop(config.SMTP_PASSWORD_ENV, None)
            tmp.cleanup()


class ExportCommandTest(unittest.TestCase):
    def test_writes_one_csv_file_per_device_in_the_working_directory(self):
        tmp = tempfile.TemporaryDirectory()
        archive_root = Path(tmp.name) / "archive"
        rows = [_row(f"2026-07-{1 + h // 24:02d}T{h % 24:02d}:00:00Z") for h in range(48)]
        archive.write_batch(archive_root, "clock", archive.BASE_COLUMNS, rows)
        cfg = make_config("http://unused.invalid", archive_root, "http://unused.invalid")

        workdir = Path(tmp.name) / "workdir"
        workdir.mkdir()
        previous_cwd = Path.cwd()
        try:
            os.chdir(workdir)
            ok = main.export(cfg, "2026-07", "2026-07")
            self.assertTrue(ok)

            out_path = workdir / "clock-2026-07_2026-07.csv"
            self.assertTrue(out_path.exists())
            lines = out_path.read_text(encoding="ascii").strip("\n").split("\n")
            self.assertEqual(len(lines), 1 + 48)  # header + 48 rows
        finally:
            os.chdir(previous_cwd)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

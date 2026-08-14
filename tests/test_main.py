"""Integration tests for collector.main.poll: fetch + archive + notify wired
together, including the state-based dedup that keeps each alert kind from
firing on every single poll.
"""

import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from collector import archive, config, main
from simulator.server import serve_in_thread


class NtfyCapture:
    """Records every ntfy POST instead of really sending one."""

    def __init__(self):
        self.alerts = []
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                capture.alerts.append(
                    {
                        "title": self.headers["Title"],
                        "priority": self.headers["Priority"],
                        "body": self.rfile.read(length).decode("utf-8"),
                        "path": self.path,
                    }
                )
                self.send_response(200)
                self.end_headers()

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_config(device_url, archive_dir, ntfy_url, unreachable_polls=3, mute_sensor_hours=6):
    return config.Config(
        devices=[config.DeviceConfig(name="clock", url=device_url, timeout_s=1)],
        archive=config.ArchiveConfig(dir=archive_dir, max_attachment_mb=20),
        ntfy=config.NtfyConfig(server=ntfy_url, topic="test-topic"),
        smtp=config.SmtpConfig(host="YOUR_SMTP_HOST", port=587, user="a@example.org", to="b@example.org"),
        alerts=config.AlertsConfig(unreachable_polls=unreachable_polls, mute_sensor_hours=mute_sensor_hours),
    )


class PollNominalTest(unittest.TestCase):
    def test_successful_poll_archives_and_alerts_nothing(self):
        httpd, _ = serve_in_thread("nominal")
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            cfg = make_config(f"http://127.0.0.1:{httpd.server_address[1]}", Path(tmp.name), ntfy.url)
            ok = main.poll(cfg)
            self.assertTrue(ok)
            self.assertEqual(ntfy.alerts, [])
            path = archive.latest_file(archive.device_dir(Path(tmp.name), "clock"))
            self.assertIsNotNone(path)
        finally:
            httpd.shutdown()
            httpd.server_close()
            ntfy.close()
            tmp.cleanup()


class PollUnreachableTest(unittest.TestCase):
    def test_alert_fires_exactly_once_at_the_threshold(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # nothing listens here: every poll is unreachable

        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            cfg = make_config(f"http://127.0.0.1:{port}", Path(tmp.name), ntfy.url, unreachable_polls=3)
            results = [main.poll(cfg) for _ in range(5)]

            self.assertEqual(results, [False] * 5)
            unreachable_alerts = [a for a in ntfy.alerts if "injoignable" in a["title"]]
            self.assertEqual(len(unreachable_alerts), 1)

            # The journal entry is not gated by the alert threshold: it logs
            # the episode's first failure, one line for the whole outage.
            events = archive.read_events(Path(tmp.name), "clock")
            self.assertEqual([e["event"] for e in events], ["device_unreachable_start"])
        finally:
            ntfy.close()
            tmp.cleanup()


class PollOverwrittenTest(unittest.TestCase):
    def test_increase_triggers_a_single_loss_alert(self):
        httpd, _ = serve_in_thread("overwritten")
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            cfg = make_config(f"http://127.0.0.1:{httpd.server_address[1]}", Path(tmp.name), ntfy.url)
            # First poll: fetches the whole fixture, whose last row already
            # carries overwrote=1 — write_batch logs that one from the
            # column. No previous counter value yet, so the counter path
            # itself stays silent.
            main.poll(cfg)
            # Second poll: no new rows (the fixture doesn't grow), but the
            # counter has increased again — a second, genuinely different
            # loss that no column flagged, so the counter path is the only
            # witness and must still log it — undated, since the row that
            # would have dated it precisely was itself overwritten.
            main.poll(cfg)

            loss_alerts = [a for a in ntfy.alerts if "perte" in a["title"]]
            self.assertEqual(len(loss_alerts), 1)  # only the counter path alerts

            events = archive.read_events(Path(tmp.name), "clock")
            self.assertEqual([e["event"] for e in events], ["overwrite_detected", "overwrite_suspected"])
            self.assertEqual(events[0]["detail"], "1")  # column-sourced: one flagged row, dated
            self.assertEqual(events[1]["detail"], "5")  # counter-sourced: the delta, undated
            self.assertEqual(events[1]["context"], "17")  # the absolute counter alongside it
        finally:
            httpd.shutdown()
            httpd.server_close()
            ntfy.close()
            tmp.cleanup()


class PollOverwrittenConcordantSourcesTest(unittest.TestCase):
    """Criterion 5 of brief 0002: when the counter's increase and a flagged
    row describe the same episode within the same poll, only one durable
    entry may result — the column's, since it is the authoritative source
    and write_batch has already logged it before the counter check runs.
    """

    def test_same_poll_same_episode_logs_once(self):
        device = GrowingOverwriteServer()
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            archive_root = Path(tmp.name)
            cfg = make_config(device.url, archive_root, ntfy.url)
            main.poll(cfg)  # baseline poll: establishes last_overwritten, no event yet
            main.poll(cfg)  # one new row, overwrote=1, counter also increased: one episode

            events = archive.read_events(archive_root, "clock")
            self.assertEqual([e["event"] for e in events], ["overwrite_detected"])
            self.assertEqual(events[0]["detail"], "1")
        finally:
            device.close()
            ntfy.close()
            tmp.cleanup()


class PollDeviceRestartTest(unittest.TestCase):
    """Brief 0002 criterion 3: a decreasing counter must produce an event,
    not the silence the old counter-only logic gave it.
    """

    def test_decreasing_counter_logs_a_restart_not_a_silence(self):
        httpd, _ = serve_in_thread("device_restart")
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            cfg = make_config(f"http://127.0.0.1:{httpd.server_address[1]}", Path(tmp.name), ntfy.url)
            main.poll(cfg)  # baseline: counter=45, nothing to compare yet
            main.poll(cfg)  # counter drops to 3: the device restarted

            events = archive.read_events(Path(tmp.name), "clock")
            kinds = [e["event"] for e in events]
            self.assertIn("device_restarted", kinds)

            restart = next(e for e in events if e["event"] == "device_restarted")
            self.assertEqual(restart["detail"], "3")    # the new, lower value
            self.assertEqual(restart["context"], "45")  # the value before the restart

            # The fixture's flagged row is a separate, genuine signal — it
            # must still be journaled on its own via the column.
            self.assertIn("overwrite_detected", kinds)
        finally:
            httpd.shutdown()
            httpd.server_close()
            ntfy.close()
            tmp.cleanup()


class PollTimeUntrustedTest(unittest.TestCase):
    def test_alert_fires_once_not_every_poll(self):
        httpd, _ = serve_in_thread("time_untrusted")
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            cfg = make_config(f"http://127.0.0.1:{httpd.server_address[1]}", Path(tmp.name), ntfy.url)
            main.poll(cfg)
            main.poll(cfg)

            alerts = [a for a in ntfy.alerts if "heure non fiable" in a["title"]]
            self.assertEqual(len(alerts), 1)
        finally:
            httpd.shutdown()
            httpd.server_close()
            ntfy.close()
            tmp.cleanup()


class GrowingDeadSensorServer:
    """A device that, hour by hour, adds one more reading where n_out is
    silent — reproduces the real production pattern of one row appearing
    per poll, which is what muted_sensors_in_tail is meant to catch. Unlike
    the static `dead_sensor` fixture, the outdoor sensor here never
    recovers, so it exercises the currently-still-dead case.

    /collect/status is called exactly once per poll (main._poll_device), so
    it is used as the poll boundary: each call unlocks one more hour of
    history, regardless of how many /collect/history calls happen inside
    that same poll's pagination loop.
    """

    def __init__(self, total_hours):
        self.total_hours = total_hours
        self.unlocked = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/collect/status":
                    server.unlocked = min(server.unlocked + 1, server.total_hours)
                    body = "fw_version,0.0.0-sim\ntime_trusted,1\noverwritten,0\n".encode("ascii")
                elif parsed.path == "/collect/history":
                    since = parse_qs(parsed.query).get("since", [None])[0]
                    rows = [server._row(h) for h in range(server.unlocked) if server._ts(h) > (since or "")]
                    body = (server._header() + "".join(rows)).encode("ascii")
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def _ts(hour):
        return f"2026-01-01T{hour:02d}:00:00Z"

    @staticmethod
    def _header():
        return ",".join(archive.BASE_COLUMNS) + "\n"

    def _row(self, hour):
        values = {c: "" for c in archive.BASE_COLUMNS}
        values.update(
            ts_utc=self._ts(hour), n_in="30", n_out="0", n_co2="12", n_pm="6", partial="0", overwrote="0"
        )
        return ",".join(values[c] for c in archive.BASE_COLUMNS) + "\n"

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class GrowingOverwriteServer:
    """One new row per poll, exactly like a real hourly `poll`. On the second
    poll, that row carries `overwrote=1` and the live counter increases in
    the very same call — the concordant case criterion 5 is about.
    """

    def __init__(self):
        self.unlocked = 0
        self.calls = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/collect/status":
                    server.calls += 1
                    server.unlocked = min(server.calls, 2)
                    counter = 10 if server.calls == 1 else 15
                    body = f"fw_version,0.0.0-sim\ntime_trusted,1\noverwritten,{counter}\n".encode("ascii")
                elif parsed.path == "/collect/history":
                    since = parse_qs(parsed.query).get("since", [None])[0]
                    rows = [server._row(h) for h in range(server.unlocked) if server._ts(h) > (since or "")]
                    body = (server._header() + "".join(rows)).encode("ascii")
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def _ts(hour):
        return f"2026-01-01T{hour:02d}:00:00Z"

    @staticmethod
    def _header():
        return ",".join(archive.BASE_COLUMNS) + "\n"

    def _row(self, hour):
        values = {c: "" for c in archive.BASE_COLUMNS}
        values.update(
            ts_utc=self._ts(hour), n_in="30", n_out="30", n_co2="12", n_pm="6", partial="0",
            overwrote="1" if hour == 1 else "0",
        )
        return ",".join(values[c] for c in archive.BASE_COLUMNS) + "\n"

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class PollMutedSensorTest(unittest.TestCase):
    def test_alert_fires_once_per_silent_episode_not_every_poll(self):
        device = GrowingDeadSensorServer(total_hours=8)
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            cfg = make_config(device.url, Path(tmp.name), ntfy.url, mute_sensor_hours=6)
            for _ in range(8):
                main.poll(cfg)

            mute_alerts = [a for a in ntfy.alerts if "capteur muet" in a["title"]]
            self.assertEqual(len(mute_alerts), 1)
        finally:
            device.close()
            ntfy.close()
            tmp.cleanup()

    def test_added_criterion_2_persistent_condition_logs_one_event_not_one_per_poll(self):
        # Ten polls, the sensor never recovers: a persistent condition must
        # produce a single journal entry, not one per pass.
        device = GrowingDeadSensorServer(total_hours=10)
        tmp = tempfile.TemporaryDirectory()
        ntfy = NtfyCapture()
        try:
            archive_root = Path(tmp.name)
            cfg = make_config(device.url, archive_root, ntfy.url, mute_sensor_hours=6)
            for _ in range(10):
                main.poll(cfg)

            events = archive.read_events(archive_root, "clock")
            mute_events = [e for e in events if e["event"] == "sensor_mute_start"]
            self.assertEqual(len(mute_events), 1)
            self.assertEqual([e["event"] for e in events if e["event"] == "sensor_mute_end"], [])
        finally:
            device.close()
            ntfy.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

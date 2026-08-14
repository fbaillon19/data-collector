import contextlib
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from collector.fetch import (
    DeviceUnhealthyError,
    DeviceUnreachableError,
    MalformedBatchError,
    fetch_history,
    fetch_status,
)
from simulator.server import serve_in_thread


@contextlib.contextmanager
def raw_response_server(body: bytes):
    """A single-shot server returning a fixed byte body for any GET — used to
    hand-craft one specific malformed wire response, isolated from the
    combined `malformed` fixture.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@contextlib.contextmanager
def error_status_server(status_code: int):
    """A server that answers every GET with a fixed HTTP error status — a
    device that is on and responding, just unwell.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_error(status_code)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


class ScenarioServerCase(unittest.TestCase):
    """Base class: start a scenario server on a free port, stop it after the test."""

    scenario = "nominal"

    def setUp(self):
        self.httpd, self.thread = serve_in_thread(self.scenario)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class NominalFetchTest(ScenarioServerCase):
    scenario = "nominal"

    def test_fetches_all_rows_in_one_page(self):
        batch = fetch_history(self.base_url, since=None, timeout_s=2)
        self.assertEqual(len(batch.rows), 72)
        self.assertEqual(batch.header[0], "ts_utc")

    def test_since_filters_strictly_after(self):
        first = fetch_history(self.base_url, since=None, timeout_s=2)
        cutoff = first.rows[9]["ts_utc"]
        second = fetch_history(self.base_url, since=cutoff, timeout_s=2)
        self.assertEqual(len(second.rows), 62)
        self.assertTrue(all(row["ts_utc"] > cutoff for row in second.rows))

    def test_status_parses_key_value_csv(self):
        status = fetch_status(self.base_url, timeout_s=2)
        self.assertEqual(status["time_trusted"], "1")
        self.assertEqual(status["hours_held"], "72")


class PaginationFetchTest(ScenarioServerCase):
    scenario = "pagination"

    def test_loops_until_every_row_is_collected(self):
        batch = fetch_history(self.base_url, since=None, timeout_s=2)
        self.assertEqual(len(batch.rows), 55)
        # Strictly increasing: no page overlap, no page dropped.
        timestamps = [row["ts_utc"] for row in batch.rows]
        self.assertEqual(timestamps, sorted(set(timestamps)))


class EmptyFetchTest(ScenarioServerCase):
    scenario = "empty"

    def test_returns_zero_rows_without_error(self):
        batch = fetch_history(self.base_url, since=None, timeout_s=2)
        self.assertEqual(batch.rows, [])
        self.assertEqual(batch.header[0], "ts_utc")


class MalformedFetchTest(ScenarioServerCase):
    scenario = "malformed"

    def test_truncated_row_rejects_the_whole_batch(self):
        with self.assertRaises(MalformedBatchError):
            fetch_history(self.base_url, since=None, timeout_s=2)


class UnreachableFetchTest(ScenarioServerCase):
    scenario = "unreachable"

    def test_slow_device_times_out(self):
        with self.assertRaises(DeviceUnreachableError):
            fetch_history(self.base_url, since=None, timeout_s=0.2)


class DeviceRespondingUnhealthyTest(unittest.TestCase):
    """Off and answering-but-broken must be distinguishable diagnoses."""

    def test_http_error_raises_the_unhealthy_subclass_not_plain_unreachable(self):
        with error_status_server(500) as base_url:
            with self.assertRaises(DeviceUnhealthyError) as ctx:
                fetch_history(base_url, since=None, timeout_s=2)
            self.assertEqual(ctx.exception.status_code, 500)

    def test_unhealthy_is_still_catchable_as_unreachable(self):
        # Callers that only care "did the poll succeed" must keep working.
        with error_status_server(503) as base_url:
            with self.assertRaises(DeviceUnreachableError):
                fetch_history(base_url, since=None, timeout_s=2)


class ConnectionRefusedTest(unittest.TestCase):
    def test_closed_port_is_unreachable(self):
        # Bind to get a free port, then close it immediately: nothing listens.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        with self.assertRaises(DeviceUnreachableError):
            fetch_history(f"http://127.0.0.1:{port}", since=None, timeout_s=1)


class NonNumericFieldTest(unittest.TestCase):
    """A device serving a non-numeric value where a number is expected must be
    rejected too — covered directly here rather than via a dedicated
    simulator scenario, since the malformed scenario already covers
    truncation and the two failure modes share the same code path.
    """

    def test_non_numeric_field_rejected(self):
        body = (
            b"ts_utc,t_in,rh_in,t_out,t_out_min,t_out_max,rh_out,co2,pm1,pm25,pm10,"
            b"n_in,n_out,n_co2,n_pm,partial,overwrote\n"
            b"2026-01-01T00:00:00Z,abc,48.2,18.1,17.4,19.0,62.1,847,3.1,5.2,7.8,30,30,12,6,0,0\n"
        )
        with raw_response_server(body) as base_url:
            with self.assertRaises(MalformedBatchError):
                fetch_history(base_url, since=None, timeout_s=2)


class InvalidTimestampTest(unittest.TestCase):
    """ts_utc indexes the whole archive: a malformed value must be rejected
    like any other malformed field, not silently passed through as a string.
    """

    def test_timestamp_missing_utc_suffix_is_rejected(self):
        body = (
            b"ts_utc,t_in,rh_in,t_out,t_out_min,t_out_max,rh_out,co2,pm1,pm25,pm10,"
            b"n_in,n_out,n_co2,n_pm,partial,overwrote\n"
            b"2026-01-01T00:00:00,21.5,48.2,18.1,17.4,19.0,62.1,847,3.1,5.2,7.8,30,30,12,6,0,0\n"
        )
        with raw_response_server(body) as base_url:
            with self.assertRaisesRegex(MalformedBatchError, "ts_utc"):
                fetch_history(base_url, since=None, timeout_s=2)

    def test_non_iso_timestamp_is_rejected(self):
        body = (
            b"ts_utc,t_in,rh_in,t_out,t_out_min,t_out_max,rh_out,co2,pm1,pm25,pm10,"
            b"n_in,n_out,n_co2,n_pm,partial,overwrote\n"
            b"not-a-timestamp,21.5,48.2,18.1,17.4,19.0,62.1,847,3.1,5.2,7.8,30,30,12,6,0,0\n"
        )
        with raw_response_server(body) as base_url:
            with self.assertRaisesRegex(MalformedBatchError, "ts_utc"):
                fetch_history(base_url, since=None, timeout_s=2)


if __name__ == "__main__":
    unittest.main()

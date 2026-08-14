"""HTTP server exposing /collect/history and /collect/status per COLLECTE.md §4.

Backed by a selectable scenario from simulator.scenarios. Used standalone
against a real collector run, and embedded in tests to drive fetch.py and
archive.py against fixtures without any hardware.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from simulator import scenarios


class ScenarioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def do_GET(self):
        scenario = self.server.scenario
        if scenario.hang:
            time.sleep(scenario.hang_seconds)

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/collect/history":
            self._handle_history(scenario, query)
        elif parsed.path == "/collect/status":
            self._handle_status(scenario)
        else:
            self.send_error(404)

    def _handle_history(self, scenario, query):
        if scenario.raw_history_body is not None:
            body = scenario.raw_history_body.encode("ascii")
        else:
            since = query.get("since", [None])[0]
            requested = query.get("limit", [None])[0]
            limit = scenario.limit_cap
            if requested is not None:
                limit = min(int(requested), scenario.limit_cap)
            rows = scenarios.filter_since(scenario.rows, since)[:limit]
            body = scenarios.render_csv(scenario.header, rows).encode("ascii")
        self._send_csv(body)

    def _handle_status(self, scenario):
        status = scenario.status_factory()
        body = scenarios.render_status(status).encode("ascii")
        self._send_csv(body)

    def _send_csv(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(scenario_name: str, port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ScenarioHandler)
    httpd.daemon_threads = True
    httpd.scenario = scenarios.build(scenario_name)
    return httpd


def serve_in_thread(scenario_name: str, port: int = 0):
    """Start a scenario server on a background daemon thread. Returns (httpd, thread)."""
    httpd = make_server(scenario_name, port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--scenario", choices=scenarios.NAMES, default="nominal")
    args = parser.parse_args(argv)

    httpd = make_server(args.scenario, args.port)
    print(f"simulator: scenario={args.scenario} url=http://127.0.0.1:{httpd.server_address[1]}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fixture data for each scenario required by design/briefs/0001-brief-collecteur.md.

Every scenario produces history rows and a status dict shaped exactly like the
CSV the real firmware would serve under COLLECTE.md. None of this is random:
the same scenario name always produces the same fixture, so a failing test
points at the collector, not at the fixture.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

HISTORY_COLUMNS = [
    "ts_utc", "t_in", "rh_in", "t_out", "t_out_min", "t_out_max",
    "rh_out", "co2", "pm1", "pm25", "pm10",
    "n_in", "n_out", "n_co2", "n_pm", "partial",
]

# Hypothetical future firmware field, used only by schema_extension to prove
# the collector opens a new archive file instead of touching the old one.
EXTENSION_COLUMN = "vbus_mv"

START = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
HOUR = dt.timedelta(hours=1)

DEFAULT_LIMIT_CAP = 1000
NAMES = [
    "nominal", "gap", "dead_sensor", "overwritten", "time_untrusted",
    "unreachable", "schema_extension", "schema_violation", "pagination",
    "malformed", "empty",
]


def _fmt(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _full_row(hour_index: int, **overrides) -> Dict[str, str]:
    row = {
        "ts_utc": _fmt(START + hour_index * HOUR),
        "t_in": "21.5", "rh_in": "48.2",
        "t_out": "18.1", "t_out_min": "17.4", "t_out_max": "19.0", "rh_out": "62.1",
        "co2": "847", "pm1": "3.1", "pm25": "5.2", "pm10": "7.8",
        "n_in": "30", "n_out": "30", "n_co2": "12", "n_pm": "6",
        "partial": "0",
    }
    row.update(overrides)
    return row


def _dead_out_row(hour_index: int) -> Dict[str, str]:
    return _full_row(
        hour_index,
        t_out="", t_out_min="", t_out_max="", rh_out="",
        n_out="0",
    )


def render_csv(header: List[str], rows: List[Dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(col, "") for col in header])
    return buf.getvalue()


def render_status(status: Dict[str, str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for key, value in status.items():
        writer.writerow([key, value])
    return buf.getvalue()


def filter_since(rows: List[Dict[str, str]], since: Optional[str]) -> List[Dict[str, str]]:
    if since is None:
        return list(rows)
    return [row for row in rows if row["ts_utc"] > since]


def _base_status(rows: List[Dict[str, str]], **overrides) -> Dict[str, str]:
    status = {
        "fw_version": "0.0.0-sim",
        "time_trusted": "1",
        "hours_held": str(len(rows)),
        "oldest_ts": rows[0]["ts_utc"] if rows else "",
        "newest_ts": rows[-1]["ts_utc"] if rows else "",
        "overwritten": "0",
        "sensor.dht22_in": "ok",
        "sensor.dht22_out": "ok",
        "sensor.scd41": "ok",
        "sensor.sps30": "ok",
        "uptime_s": "12345",
    }
    status.update(overrides)
    return status


@dataclass
class Scenario:
    name: str
    header: List[str]
    rows: List[Dict[str, str]]
    status_factory: Callable[[], Dict[str, str]]
    limit_cap: int = DEFAULT_LIMIT_CAP
    raw_history_body: Optional[str] = None
    hang: bool = False
    hang_seconds: float = 5.0


def _build_nominal() -> Scenario:
    rows = [_full_row(i) for i in range(72)]
    return Scenario("nominal", HISTORY_COLUMNS, rows, lambda: _base_status(rows))


def _build_gap() -> Scenario:
    rows = [_full_row(i) for i in range(72) if i not in (30, 31, 32)]
    return Scenario("gap", HISTORY_COLUMNS, rows, lambda: _base_status(rows))


def _build_dead_sensor() -> Scenario:
    rows = []
    for i in range(24):
        if 8 <= i <= 17:  # 10 consecutive hours, muted outdoor sensor
            rows.append(_dead_out_row(i))
        else:
            rows.append(_full_row(i))
    return Scenario("dead_sensor", HISTORY_COLUMNS, rows, lambda: _base_status(rows))


def _build_overwritten() -> Scenario:
    rows = [_full_row(i) for i in range(24)]
    counter = {"value": 12}

    def status_factory():
        status = _base_status(rows, overwritten=str(counter["value"]))
        counter["value"] += 5
        return status

    return Scenario("overwritten", HISTORY_COLUMNS, rows, status_factory)


def _build_time_untrusted() -> Scenario:
    rows = [_full_row(i) for i in range(5)]
    return Scenario(
        "time_untrusted", HISTORY_COLUMNS, rows,
        lambda: _base_status(rows, time_trusted="0"),
    )


def _build_unreachable() -> Scenario:
    rows = [_full_row(i) for i in range(5)]
    return Scenario(
        "unreachable", HISTORY_COLUMNS, rows, lambda: _base_status(rows),
        hang=True,
    )


def _build_schema_extension() -> Scenario:
    header = HISTORY_COLUMNS + [EXTENSION_COLUMN]
    rows = [_full_row(i, **{EXTENSION_COLUMN: "5020"}) for i in range(24)]
    return Scenario("schema_extension", header, rows, lambda: _base_status(rows))


def _build_schema_violation() -> Scenario:
    header = list(HISTORY_COLUMNS)
    header[1], header[2] = header[2], header[1]  # swap t_in / rh_in: reordered
    rows = [_full_row(i) for i in range(5)]
    return Scenario("schema_violation", header, rows, lambda: _base_status(rows))


def _build_pagination() -> Scenario:
    rows = [_full_row(i) for i in range(55)]
    return Scenario("pagination", HISTORY_COLUMNS, rows, lambda: _base_status(rows), limit_cap=20)


def _build_malformed() -> Scenario:
    rows = [_full_row(i) for i in range(5)]
    lines = [",".join(HISTORY_COLUMNS)]
    for row in rows[:2]:
        lines.append(",".join(row[col] for col in HISTORY_COLUMNS))
    # Corrupt timestamp: right field count, but not a valid UTC instant.
    bad_ts_row = dict(rows[2])
    bad_ts_row["ts_utc"] = "2026-01-01T02:00:00"  # missing the trailing Z
    lines.append(",".join(bad_ts_row[col] for col in HISTORY_COLUMNS))
    # Truncated record: an appliance that stopped writing mid-line.
    truncated = HISTORY_COLUMNS[:8]
    lines.append(",".join(rows[3][col] for col in truncated))
    raw_body = "\n".join(lines) + "\n"
    return Scenario(
        "malformed", HISTORY_COLUMNS, rows, lambda: _base_status(rows),
        raw_history_body=raw_body,
    )


def _build_empty() -> Scenario:
    rows: List[Dict[str, str]] = []
    return Scenario("empty", HISTORY_COLUMNS, rows, lambda: _base_status(rows))


_BUILDERS = {
    "nominal": _build_nominal,
    "gap": _build_gap,
    "dead_sensor": _build_dead_sensor,
    "overwritten": _build_overwritten,
    "time_untrusted": _build_time_untrusted,
    "unreachable": _build_unreachable,
    "schema_extension": _build_schema_extension,
    "schema_violation": _build_schema_violation,
    "pagination": _build_pagination,
    "malformed": _build_malformed,
    "empty": _build_empty,
}


def build(name: str) -> Scenario:
    try:
        return _BUILDERS[name]()
    except KeyError:
        raise ValueError(f"unknown scenario: {name!r} (known: {', '.join(NAMES)})")

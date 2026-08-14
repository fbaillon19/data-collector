"""Archive writing and the schema it enforces (COLLECTE.md §3).

The archive is a directory per device, one CSV file per schema generation,
append-only without exception. This module owns everything that requires
comparing an incoming batch against what is already on disk: idempotence,
schema extension vs. schema violation, hour gaps, ring overwrite, muted
sensors, untrusted time.

A batch is written whole or not at all. There is no partial write: a
SchemaViolation is raised, and validated, before any file is touched.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_COLUMNS = [
    "ts_utc", "t_in", "rh_in", "t_out", "t_out_min", "t_out_max",
    "rh_out", "co2", "pm1", "pm25", "pm10",
    "n_in", "n_out", "n_co2", "n_pm", "partial", "overwrote",
]

# Always present, never empty, regardless of what a given hour measured.
REQUIRED_COLUMNS = {"ts_utc", "n_in", "n_out", "n_co2", "n_pm", "partial", "overwrote"}

# May be empty (sensor silent that hour); when present, must parse as a number.
NUMERIC_COLUMNS = {
    "t_in", "rh_in", "t_out", "t_out_min", "t_out_max", "rh_out",
    "co2", "pm1", "pm25", "pm10", "n_in", "n_out", "n_co2", "n_pm", "partial", "overwrote",
}

COUNT_COLUMNS = ["n_in", "n_out", "n_co2", "n_pm"]

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

EVENTS_FILENAME = "events.csv"
EVENT_COLUMNS = ["ts_utc", "event", "detail", "context"]


class SchemaViolation(Exception):
    """The device's CSV header does not respect COLLECTE.md §3. Nothing is written."""


@dataclass
class Gap:
    after_ts: str
    before_ts: str
    missing_hours: int


@dataclass
class WriteResult:
    written: int
    duplicates: int
    new_file: Optional[Path] = None
    gaps: List[Gap] = field(default_factory=list)
    overwrote_count: int = 0


@dataclass
class MuteStreak:
    column: str
    first_ts: str
    last_ts: str
    hours: int


def device_dir(archive_root: Path, device: str) -> Path:
    return Path(archive_root) / device


_DATED_FILENAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv$")


def dated_files(directory: Path) -> List[Path]:
    """Archive generation files only — never events.csv or any other CSV
    someone might drop in a device's directory. A loose "*.csv" glob would
    silently corrupt schema-generation detection: any stray file sorting
    after the real latest one would be mistaken for "the current file".
    """
    if not directory.is_dir():
        return []
    # ISO-formatted filenames (YYYY-MM-DD.csv) sort lexically in chronological
    # order; files are only ever created forward in time.
    return sorted(p for p in directory.iterdir() if p.is_file() and _DATED_FILENAME.match(p.name))


def latest_file(directory: Path) -> Optional[Path]:
    files = dated_files(directory)
    return files[-1] if files else None


def read_header(csv_path: Path) -> List[str]:
    with csv_path.open(encoding="ascii", newline="") as f:
        return next(csv.reader(f))


def _last_row(csv_path: Path) -> Optional[List[str]]:
    last = None
    with csv_path.open(encoding="ascii", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if row:
                last = row
    return last


def last_archived_timestamp(archive_root: Path, device: str) -> Optional[str]:
    path = latest_file(device_dir(archive_root, device))
    if path is None:
        return None
    row = _last_row(path)
    return row[0] if row else None


def _validate_header(header: List[str]) -> None:
    n = len(BASE_COLUMNS)
    if len(header) < n or header[:n] != BASE_COLUMNS:
        raise SchemaViolation(
            f"columns missing, renamed, or reordered against the contract: {header!r}"
        )


def _validate_extension(new_header: List[str], previous_header: Optional[List[str]]) -> None:
    """An extension only ever adds trailing columns. Anything else is a violation."""
    if previous_header is None or new_header == previous_header:
        return
    is_pure_extension = (
        len(new_header) > len(previous_header)
        and new_header[: len(previous_header)] == previous_header
    )
    if not is_pure_extension:
        raise SchemaViolation(
            f"header is not a trailing extension of the archived one: "
            f"{previous_header!r} -> {new_header!r}"
        )


def _hours_between(ts_a: str, ts_b: str) -> float:
    a = dt.datetime.strptime(ts_a, TS_FORMAT)
    b = dt.datetime.strptime(ts_b, TS_FORMAT)
    return (b - a).total_seconds() / 3600.0


def detect_gaps(ordered_ts: List[str]) -> List[Gap]:
    gaps = []
    for prev, cur in zip(ordered_ts, ordered_ts[1:]):
        delta = _hours_between(prev, cur)
        if delta > 1:
            gaps.append(Gap(after_ts=prev, before_ts=cur, missing_hours=int(round(delta)) - 1))
    return gaps


def _append_rows(path: Path, header: List[str], rows: List[Dict[str, str]], write_header: bool) -> None:
    with path.open("a", encoding="ascii", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        if write_header:
            writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(col, "") for col in header])


def write_batch(archive_root: Path, device: str, header: List[str], rows: List[Dict[str, str]]) -> WriteResult:
    """Append a fetched batch to the device's archive.

    Raises SchemaViolation, without writing anything, if the header breaks
    the contract or is not a trailing extension of what is already archived.
    """
    if not rows:
        return WriteResult(written=0, duplicates=0)

    _validate_header(header)

    directory = device_dir(archive_root, device)
    current_file = latest_file(directory)
    previous_header = read_header(current_file) if current_file else None
    _validate_extension(header, previous_header)

    baseline_ts = last_archived_timestamp(archive_root, device)

    fresh_rows: List[Dict[str, str]] = []
    duplicates = 0
    watermark = baseline_ts
    for row in rows:
        if watermark is not None and row["ts_utc"] <= watermark:
            logger.warning("duplicate timestamp ignored for %s: %s", device, row["ts_utc"])
            duplicates += 1
            continue
        fresh_rows.append(row)
        watermark = row["ts_utc"]

    if not fresh_rows:
        return WriteResult(written=0, duplicates=duplicates)

    gap_input = ([baseline_ts] if baseline_ts else []) + [r["ts_utc"] for r in fresh_rows]
    gaps = detect_gaps(gap_input)

    new_file = None
    if current_file is None or header != previous_header:
        directory.mkdir(parents=True, exist_ok=True)
        date_prefix = fresh_rows[0]["ts_utc"][:10]
        target = directory / f"{date_prefix}.csv"
        if target.exists():
            raise RuntimeError(
                f"cannot open a new generation file, {target} already exists "
                "(two schema extensions on the same calendar date is not supported)"
            )
        new_file = target
        _append_rows(target, header, fresh_rows, write_header=True)
        if current_file is not None:  # a genuine extension, not the archive's first-ever file
            added_columns = header[len(previous_header):]
            log_event(
                archive_root, device, "schema_extension",
                detail=",".join(added_columns), ts=fresh_rows[0]["ts_utc"],
            )
    else:
        _append_rows(current_file, header, fresh_rows, write_header=False)

    # The `overwrote` column is the durable, per-record source of truth for a
    # ring-buffer loss (COLLECTE.md §5) — it survives a device restart, which
    # the live `overwritten` counter does not. Every flagged row gets its own
    # journal entry, at the moment it is archived.
    overwrote_rows = [r for r in fresh_rows if r.get("overwrote") == "1"]
    for row in overwrote_rows:
        log_event(archive_root, device, "overwrite_detected", detail="1", ts=row["ts_utc"])

    return WriteResult(
        written=len(fresh_rows), duplicates=duplicates, new_file=new_file, gaps=gaps,
        overwrote_count=len(overwrote_rows),
    )


@dataclass
class OverwrittenChange:
    kind: str  # "loss" (current > previous) or "restart" (current < previous)
    previous: int
    current: int

    @property
    def delta(self) -> int:
        return abs(self.current - self.previous)


def check_overwritten(previous: Optional[int], current: int) -> Optional[OverwrittenChange]:
    """Classifies a change in the device's live `overwritten` counter.

    "loss": a genuine increase — a fallback signal only, since the
    `overwrote` column is the authoritative, durable one.

    "restart": the counter is *lower* than last seen. This in-RAM counter
    resets to zero on every device reboot (COLLECTE.md §4), so a decrease
    does not mean nothing was lost — it means the counter itself cannot be
    trusted as a running total anymore. Treating it as "no news" would
    silently swallow that fact; it must produce an event, not a silence.
    """
    if previous is None or current == previous:
        return None
    kind = "loss" if current > previous else "restart"
    return OverwrittenChange(kind=kind, previous=previous, current=current)


def is_time_untrusted(status: Dict[str, str]) -> bool:
    return status.get("time_trusted") == "0"


def detect_muted_sensors(rows: List[Dict[str, str]], min_hours: int) -> List[MuteStreak]:
    """Finds runs of `min_hours`+ consecutive hours where one sample count is
    zero while at least one other sensor on the same device is alive that
    hour — i.e. the device answered, but this one sensor stayed silent.
    """
    streaks: List[MuteStreak] = []
    for column in COUNT_COLUMNS:
        others = [c for c in COUNT_COLUMNS if c != column]
        run: List[Dict[str, str]] = []
        for row in rows:
            device_alive = any(int(row[o]) > 0 for o in others)
            if int(row[column]) == 0 and device_alive:
                run.append(row)
                continue
            if len(run) >= min_hours:
                streaks.append(MuteStreak(column, run[0]["ts_utc"], run[-1]["ts_utc"], len(run)))
            run = []
        if len(run) >= min_hours:
            streaks.append(MuteStreak(column, run[0]["ts_utc"], run[-1]["ts_utc"], len(run)))
    return streaks


def read_rows(csv_path: Path) -> List[Dict[str, str]]:
    """All data rows of a CSV file, header-keyed."""
    with csv_path.open(encoding="ascii", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        return [dict(zip(header, row)) for row in reader if row]


def events_path(archive_root: Path, device: str) -> Path:
    return device_dir(archive_root, device) / EVENTS_FILENAME


def log_event(
    archive_root: Path, device: str, event: str, detail: str = "", context: str = "", ts: Optional[str] = None
) -> None:
    """Appends one line to the device's event journal — same discipline as
    the archive itself: append-only, never rewritten, never completed.

    This is the only durable record of *why* a gap exists. A gap caused by
    an unplugged device is benign; a gap caused by the ring overwriting
    uncollected hours is a device failure. Once state.json (deliberately
    reconstructible, and so disposable) is gone, `overwritten`'s history
    would be gone with it if it lived nowhere else.

    `detail` carries the event's own measure (a delta, a column name);
    `context` carries surrounding state worth keeping alongside it, e.g. the
    absolute `overwritten` counter next to the loss it just grew by — empty
    for events that have none.

    Callers are responsible for calling this once per episode, not once per
    poll — a persistent condition logged every pass would make the journal
    something nobody reads.
    """
    ts = ts or dt.datetime.now(dt.timezone.utc).strftime(TS_FORMAT)
    path = events_path(archive_root, device)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", encoding="ascii", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        if write_header:
            writer.writerow(EVENT_COLUMNS)
        writer.writerow([ts, event, detail, context])


def read_events(archive_root: Path, device: str) -> List[Dict[str, str]]:
    path = events_path(archive_root, device)
    if not path.exists():
        return []
    return read_rows(path)


def muted_sensors_in_archive(archive_root: Path, device: str, min_hours: int) -> List[MuteStreak]:
    """The correct caller for detect_muted_sensors.

    A device polled hourly delivers one row per poll: a streak of more than
    `min_hours` hours can only ever be seen by looking at what is now on
    disk, not at the single-row batch a poll just fetched. Calling
    detect_muted_sensors on the batch instead would make the alert
    structurally unreachable in normal operation.

    Reads the whole current file rather than a bounded tail: a fixed-size
    window would make a streak's reported start drift forward as it outgrows
    the window, both misreporting how long the sensor has been silent and
    breaking dedup (the "same" streak would look new every poll once past
    the window). At the volumes this archive is built for — about a
    megabyte of CSV a year — a full read costs nothing.
    """
    path = latest_file(device_dir(archive_root, device))
    if path is None:
        return []
    return detect_muted_sensors(read_rows(path), min_hours)
